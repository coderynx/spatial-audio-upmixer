//! Mid-bass stereo decorrelation: an ERB-warped allpass cascade per channel,
//! applied to the sustained part of the 100-300 Hz band only.
//!
//! Band corners, cascade length and the group-delay ceiling are owned by
//! `packages/core/src/mastering/bass.py`. See
//! `docs/standards/spatial_layouts_bs775_bs2051.md`, "Bass management".

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::butter_bandpass_sos;

use super::bass::BassParams;
use super::compressor::alpha;

/// Keeps the sustain gate at unity while both envelopes converge from
/// silence, instead of `0/0`.
const GATE_EPS: f64 = 1e-9;

/// Lower bound on the pole radius, per Kermit-Canfield & Abel: below this the
/// section's phase response is too shallow to decorrelate anything.
const POLE_R_MIN: f64 = 0.5;

/// Upper bound regardless of the group-delay budget — `2(1+r)/(1-r)` grows
/// without bound as `r → 1`, and a single runaway section would read as
/// ringing rather than width.
const POLE_R_MAX: f64 = 0.95;

/// Fractions of the group-delay budget successive channels are placed at.
///
/// 100-300 Hz is only ~180 Hz wide, so full decorrelation of a pair needs
/// ~5.6 ms of group-delay separation and the 30 ms ceiling leaves room for a
/// handful of distinct classes, not eleven. Channels beyond that wrap and
/// share a class, which is why the stage reduces the coherent sum rather than
/// eliminating it — see `docs/standards/spatial_layouts_bs775_bs2051.md`.
const DELAY_STAGGER: [f64; 6] = [0.35, 0.48, 0.61, 0.74, 0.87, 1.0];

/// Band-pass order. 2nd order never reaches unity across 1.6 octaves, which
/// spreads the reconstruction ripple over the whole band instead of its edges.
const BAND_ORDER: usize = 4;

fn splitmix64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

fn next_unit(state: &mut u64) -> f64 {
    (splitmix64(state) >> 11) as f64 / (1u64 << 53) as f64
}

/// Glasberg & Moore's ERB-rate scale, the warping that puts the poles at
/// roughly constant density per critical band rather than per hertz.
fn erb_rate(hz: f64) -> f64 {
    21.4 * (1.0 + 0.00437 * hz).log10()
}

fn erb_rate_inv(rate: f64) -> f64 {
    (10.0_f64.powf(rate / 21.4) - 1.0) / 0.00437
}

/// One 2nd-order allpass as a biquad row: `b = [r², -2r·cosθ, 1]`,
/// `a = [1, -2r·cosθ, r²]`.
fn allpass_row(radius: f64, theta: f64) -> [f64; 6] {
    let a1 = -2.0 * radius * theta.cos();
    let a2 = radius * radius;
    [a2, a1, 1.0, 1.0, a1, a2]
}

/// Pole radius whose peak group delay `2(1+r)/(1-r)` fits `budget` samples.
fn radius_for_delay(budget_samples: f64) -> f64 {
    if budget_samples <= 2.0 {
        return POLE_R_MIN;
    }
    ((budget_samples - 2.0) / (budget_samples + 2.0)).clamp(POLE_R_MIN, POLE_R_MAX)
}

/// The band the stage runs on, or `None` when it is off or has collapsed.
///
/// The low corner never reaches below the unifier's crossover: that band is
/// mono by design and decorrelating it would break the Σa = 1 invariant.
fn band_edges(sample_rate: u32, p: &BassParams) -> Option<(f64, f64)> {
    let nyq = sample_rate as f64 / 2.0;
    let lo = p.decorr_low_hz.max(p.unify_hz.unwrap_or(0.0));
    let hi = p.decorr_high_hz.min(nyq * 0.99);
    (p.decorrelate > 0.0 && hi > lo * 1.01 && lo < nyq * 0.98).then_some((lo, hi))
}

/// The allpass cascade for one channel, seeded so that every channel gets an
/// independent pole set while the whole stage stays reproducible.
fn cascade_rows(channel: usize, sample_rate: u32, p: &BassParams) -> Vec<[f64; 6]> {
    let sections = p.decorr_sections.max(1);
    let mut rng = (channel as u64).wrapping_add(1).wrapping_mul(0x9E37_79B9_7F4A_7C15);
    let budget = p.decorr_max_delay_ms / 1000.0 * sample_rate as f64 / sections as f64;
    // Channels are staggered deterministically across the delay budget rather
    // than each drawing from one distribution. Independent draws converge on
    // the same average response over a band this narrow — measured mean
    // pairwise |correlation| got *worse* with more sections, 0.64 at 8 up to
    // 0.95 at 128. What decorrelates a narrow band is a difference in group
    // delay: the phase difference between two channels only varies across the
    // band as `2*pi*dtau*f`, so it takes `dtau*bandwidth >~ 1`.
    let stagger = DELAY_STAGGER[channel % DELAY_STAGGER.len()];
    let r_max = radius_for_delay(budget * stagger);

    let nyq = sample_rate as f64 / 2.0;
    let (lo, hi) = band_edges(sample_rate, p).unwrap_or((p.decorr_low_hz, p.decorr_high_hz));
    let (e_lo, e_hi) = (erb_rate(lo), erb_rate(hi));

    (0..sections)
        .map(|k| {
            let step = (k as f64 + next_unit(&mut rng)) / sections as f64;
            let hz = erb_rate_inv(e_lo + step * (e_hi - e_lo));
            let radius = POLE_R_MIN + next_unit(&mut rng) * (r_max - POLE_R_MIN);
            allpass_row(radius, std::f64::consts::PI * hz / nyq)
        })
        .collect()
}

/// The band split the cascade runs on.
///
/// Zero-phase, for the same reason `bass::lf_unify` filters that way:
/// reconstruction is `x - band + allpass(band)`, so the response is
/// `1 + B(ω)·(A(ω) − 1)`, and only a real `B` lets the pass band collapse to
/// `|A| = 1`. Run causally, `B` carries its own phase lag and the rotated
/// copy beats against the residual — a measured 3 dB dip at 200 Hz.
///
/// At the two −3 dB skirts `|B| ≈ 0.71` either way, so ripple there is
/// inherent to replacing a band with a phase-rotated copy of itself; it
/// scales with `decorrelate`.
pub fn band_sos(sample_rate: u32, p: &BassParams) -> Option<Vec<[f64; 6]>> {
    let (lo, hi) = band_edges(sample_rate, p)?;
    let nyq = sample_rate as f64 / 2.0;
    Some(butter_bandpass_sos(
        BAND_ORDER,
        (lo / nyq).clamp(1e-4, 0.999),
        (hi / nyq).clamp(1e-4, 0.999),
    ))
}

/// Per-channel allpass cascade and its sustain gate.
///
/// Causal, so it carries across block boundaries the way `PunchState` does;
/// the zero-phase band split it consumes is the caller's, since offline and
/// streaming source that differently (`sosfiltfilt` vs `HorizonFiltFilt`).
pub struct Decorrelator {
    allpass: SosFilter,
    amount: f64,
    alpha_fast: f64,
    alpha_slow: f64,
    fast: f64,
    slow: f64,
    primed: bool,
}

impl Decorrelator {
    pub fn new(channel: usize, sample_rate: u32, p: &BassParams) -> Self {
        Self {
            allpass: SosFilter::from_flat(&cascade_rows(channel, sample_rate, p)),
            amount: p.decorrelate.clamp(0.0, 1.0),
            alpha_fast: alpha(p.decorr_fast_ms, sample_rate),
            alpha_slow: alpha(p.decorr_slow_ms, sample_rate),
            fast: 0.0,
            slow: 0.0,
            primed: false,
        }
    }

    /// Sustain weight in [0, 1]. A fast envelope running ahead of the slow one
    /// is an onset, which the cascade must not smear; steady content and
    /// decays both read as sustain.
    fn sustain(&mut self, band: f64) -> f64 {
        let level = band.abs();
        // Both envelopes start at the first level seen. From cold they would
        // spend the slow one's whole rise reading the programme's opening as
        // one long onset, and gate the stage off for over a second.
        if !self.primed {
            self.primed = true;
            self.fast = level;
            self.slow = level;
        }
        self.fast += self.alpha_fast * (level - self.fast);
        self.slow += self.alpha_slow * (level - self.slow);
        ((self.slow + GATE_EPS) / (self.fast + GATE_EPS)).clamp(0.0, 1.0)
    }

    /// Swap `band` out of `x` for its allpassed self, in place.
    ///
    /// The blend between `band` and its rotated copy is constant-power, and
    /// only the two of them are crossfaded — out of band both are zero, so
    /// `x` passes untouched and there is no broadband cut. A linear blend
    /// averages `(1 − w)² + w²` of the power instead, since the two are
    /// mutually decorrelated: 2.4 dB down at `w = 0.7`, which is squarely
    /// inside the range the control is meant to be used in.
    pub fn run(&mut self, x: &mut [f64], band: &[f64]) {
        for (v, b) in x.iter_mut().zip(band.iter()) {
            let gate = self.sustain(*b);
            let wet = self.allpass.tick(*b);
            let w = (self.amount * gate).clamp(0.0, 1.0);
            *v += (1.0 - w).sqrt() * b + w.sqrt() * wet - b;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mastering::bass::BassParams;

    fn params() -> BassParams {
        BassParams {
            sub_gain_db: 0.0,
            mid_gain_db: 0.0,
            unify_hz: None,
            punch: 0.0,
            excite: false,
            lfe_gain_db: 0.0,
            sub_cutoff_hz: 80.0,
            mid_cutoff_hz: 200.0,
            excite_blend: 0.15,
            excite_drive: 3.0,
            punch_fast_ms: 10.0,
            punch_slow_ms: 120.0,
            punch_max_db: 6.0,
            decorrelate: 1.0,
            decorr_low_hz: 100.0,
            decorr_high_hz: 300.0,
            decorr_sections: 32,
            decorr_max_delay_ms: 30.0,
            decorr_fast_ms: 30.0,
            decorr_slow_ms: 300.0,
        }
    }
    fn noise(n: usize, seed: u64) -> Vec<f64> {
        let mut state = seed;
        (0..n).map(|_| next_unit(&mut state) * 2.0 - 1.0).collect()
    }

    fn energy(x: &[f64]) -> f64 {
        x[4800..].iter().map(|v| v * v).sum()
    }

    /// What `bass_control` does around the cascade: zero-phase band, then the
    /// blend. Kept here so the tests exercise the same pairing the callers do.
    fn decorrelate(channel: usize, sr: u32, p: &BassParams, x: &[f64]) -> Vec<f64> {
        let sos = band_sos(sr, p).expect("band");
        let band = crate::mastering::bass::zero_phase(&sos, x);
        let mut out = x.to_vec();
        Decorrelator::new(channel, sr, p).run(&mut out, &band);
        out
    }

    #[test]
    fn amount_zero_leaves_no_band_to_process() {
        let p = BassParams { decorrelate: 0.0, ..params() };
        assert!(band_sos(48_000, &p).is_none());
    }

    #[test]
    fn the_band_stays_above_the_mono_crossover() {
        // A unify crossover past the top of the band leaves no band at all.
        let p = BassParams { unify_hz: Some(400.0), ..params() };
        assert!(band_sos(48_000, &p).is_none());

        // Below it, the band starts at the crossover, never under it.
        let p = BassParams { unify_hz: Some(120.0), ..params() };
        let rows = cascade_rows(0, 48_000, &p);
        assert!(band_sos(48_000, &p).is_some());
        assert_eq!(rows.len(), p.decorr_sections);
    }

    #[test]
    fn group_delay_budget_bounds_the_pole_radius() {
        let rows = cascade_rows(3, 48_000, &params());
        assert_eq!(rows.len(), params().decorr_sections);
        for row in &rows {
            // a2 = r², and the row must be a true allpass: b reversed == a.
            let r = row[5].sqrt();
            assert!((POLE_R_MIN..=POLE_R_MAX).contains(&r), "radius {r} out of range");
            assert!((row[0] - row[5]).abs() < 1e-12);
            assert!((row[1] - row[4]).abs() < 1e-12);
            assert!((row[2] - 1.0).abs() < 1e-12 && (row[3] - 1.0).abs() < 1e-12);
        }
    }

    #[test]
    fn poles_land_inside_the_band_at_constant_erb_density() {
        let p = params();
        let rows = cascade_rows(1, 48_000, &p);
        let mut rates: Vec<f64> = rows
            .iter()
            .map(|row| {
                let r = row[5].sqrt();
                let theta = (-row[4] / (2.0 * r)).clamp(-1.0, 1.0).acos();
                erb_rate(theta / std::f64::consts::PI * 24_000.0)
            })
            .collect();
        rates.sort_by(|a, b| a.partial_cmp(b).unwrap());

        let (lo, hi) = (erb_rate(p.decorr_low_hz), erb_rate(p.decorr_high_hz));
        assert!(rates[0] >= lo - 1e-9 && *rates.last().unwrap() <= hi + 1e-9);
        // One pole per equal ERB slice, so no gap can exceed two slices.
        let slice = (hi - lo) / rows.len() as f64;
        for pair in rates.windows(2) {
            assert!(pair[1] - pair[0] < 2.0 * slice, "ERB gap {}", pair[1] - pair[0]);
        }
    }

    #[test]
    fn the_cascade_is_allpass_so_it_moves_no_energy_of_its_own() {
        let sr = 48_000;
        let x = noise(24_000, 7);
        let mut only_allpass = SosFilter::from_flat(&cascade_rows(0, sr, &params()));
        let y: Vec<f64> = x.iter().map(|v| only_allpass.tick(*v)).collect();
        let (ex, ey) = (energy(&x), energy(&y));
        assert!((ey / ex - 1.0).abs() < 0.02, "energy moved: {ex} -> {ey}");
    }

    /// Noise already confined to the band, so total energy is in-band energy
    /// and no measuring filter is needed — band-passing the result to measure
    /// it would double-filter and confound the very thing under test.
    fn band_limited_noise(n: usize, seed: u64, sr: u32) -> Vec<f64> {
        let x = noise(n, seed);
        let sos = butter_bandpass_sos(4, 120.0 / (sr as f64 / 2.0), 280.0 / (sr as f64 / 2.0));
        crate::mastering::bass::zero_phase(sos.as_slice(), &x)
    }

    /// Two channels must not end up with the same cascade: independent random
    /// draws converge on one average response over a band this narrow, so the
    /// separation comes from `DELAY_STAGGER` placing them at different group
    /// delays.
    #[test]
    fn neighbouring_channels_get_different_cascades() {
        let sr = 48_000;
        let p = params();
        let a = cascade_rows(0, sr, &p);
        let b = cascade_rows(1, sr, &p);
        assert_ne!(a, b);
        // The radii differ systematically, not just by the random jitter.
        let mean_r = |rows: &[[f64; 6]]| -> f64 {
            rows.iter().map(|r| r[5].sqrt()).sum::<f64>() / rows.len() as f64
        };
        assert!(
            (mean_r(&a) - mean_r(&b)).abs() > 0.01,
            "{} vs {}",
            mean_r(&a),
            mean_r(&b)
        );
    }

    #[test]
    fn the_band_keeps_its_energy_at_every_depth() {
        // Constant-power is what makes this hold at partial depth. A linear
        // blend averages (1-w)² + w² instead — 2.4 dB down at w = 0.7, since
        // the band and its rotated copy are mutually decorrelated.
        let sr = 48_000;
        let n = 192_000;
        let settle = 24_000;
        let x = band_limited_noise(n, 21, sr);
        let energy_of = |v: &[f64]| -> f64 { v[settle..].iter().map(|s| s * s).sum() };

        for depth in [0.25, 0.5, 0.7, 1.0] {
            let p = BassParams { decorrelate: depth, ..params() };
            let out = decorrelate(0, sr, &p, &x);
            let db = 10.0 * (energy_of(&out) / energy_of(&x)).log10();
            assert!(db.abs() < 0.6, "depth {depth} moved the band by {db} dB");
        }
    }

    #[test]
    fn channels_diverge_without_any_one_of_them_moving() {
        let sr = 48_000;
        let n = 192_000;
        let settle = 24_000;
        let x = band_limited_noise(n, 5, sr);
        let p = params();
        let left = decorrelate(0, sr, &p, &x);
        let right = decorrelate(1, sr, &p, &x);
        let energy_of = |v: &[f64]| -> f64 { v[settle..].iter().map(|s| s * s).sum() };

        // Independent seeds must actually produce different signals.
        let diff: f64 = left[settle..]
            .iter()
            .zip(right[settle..].iter())
            .map(|(a, b)| (a - b).powi(2))
            .sum();
        assert!(diff > energy_of(&x) * 0.1, "channels stayed correlated: {diff}");

        // Each channel on its own holds its level — a speaker level is a
        // speaker level, decorrelation must not become a gain change.
        for (name, ch) in [("left", &left), ("right", &right)] {
            let db = 10.0 * (energy_of(ch) / energy_of(&x)).log10();
            assert!(db.abs() < 0.6, "{name} level moved by {db} dB");
        }

        // The coherent sum drops — that reduction is the enveloping effect.
        let summed: f64 = (settle..n).map(|i| (left[i] + right[i]).powi(2)).sum();
        assert!(
            summed < 4.0 * energy_of(&x) * 0.75,
            "sum did not decorrelate: {summed}"
        );
    }

    #[test]
    fn a_transient_passes_through_far_more_intact_than_sustain() {
        let sr = 48_000;
        let n = 24_000;
        let p = params();
        // An impulse train in the band: onsets with silence between them.
        let mut clicks = vec![0.0; n];
        for i in (2400..n).step_by(6000) {
            clicks[i] = 1.0;
        }
        let hit = decorrelate(0, sr, &p, &clicks);

        let sustained: Vec<f64> = (0..n)
            .map(|i| 0.3 * (2.0 * std::f64::consts::PI * 200.0 * i as f64 / sr as f64).sin())
            .collect();
        let smeared = decorrelate(0, sr, &p, &sustained);

        let change = |a: &[f64], b: &[f64]| -> f64 {
            let num: f64 = a[2400..].iter().zip(b[2400..].iter()).map(|(x, y)| (x - y).powi(2)).sum();
            num / a[2400..].iter().map(|v| v * v).sum::<f64>().max(1e-20)
        };
        assert!(
            change(&clicks, &hit) < change(&sustained, &smeared),
            "transients moved as much as sustain: {} vs {}",
            change(&clicks, &hit),
            change(&sustained, &smeared)
        );
    }

    #[test]
    fn content_outside_the_band_is_left_alone() {
        let sr = 48_000;
        let n = 24_000;
        let high: Vec<f64> = (0..n)
            .map(|i| 0.5 * (2.0 * std::f64::consts::PI * 4000.0 * i as f64 / sr as f64).sin())
            .collect();
        let out = decorrelate(0, sr, &params(), &high);

        // Bounded by the band-pass stopband, not zero: a 4th-order pass is far
        // down at 4 kHz, and the stage can only move what it captures.
        let moved: f64 = (4800..n).map(|i| (out[i] - high[i]).powi(2)).sum();
        assert!(moved < energy(&high) * 1e-4, "out-of-band moved: {moved}");
    }

    #[test]
    fn the_cascade_carries_across_block_boundaries() {
        let sr = 48_000;
        let p = params();
        let x = noise(4096, 3);
        let sos = band_sos(sr, &p).expect("band");
        let band = crate::mastering::bass::zero_phase(sos.as_slice(), &x);

        let mut whole = x.clone();
        Decorrelator::new(2, sr, &p).run(&mut whole, &band);

        let mut blocked = x.clone();
        let mut d = Decorrelator::new(2, sr, &p);
        for (chunk, b) in blocked.chunks_mut(128).zip(band.chunks(128)) {
            d.run(chunk, b);
        }
        assert_eq!(whole, blocked);
    }
}
