//! Velvet-noise decorrelator pair for surround and height sends.
//!
//! Reference design: Alary, Politis & Välimäki, "Velvet-noise decorrelator"
//! (DAFx 2018) — a sparse FIR of ~1 tap/ms with ±1 signs on an exponentially
//! decaying envelope, which decorrelates without the periodic comb a single
//! delayed copy produces.
//!
//! The pair is built as **complements**: `2 * taps` velvet cells are laid over
//! the filter length and handed out alternately, so the two sides never share
//! a tap position. Three properties follow by construction rather than by
//! tuning:
//!
//! - `<h_L, h_R> = 0` exactly, so the pair is decorrelated at zero lag;
//! - `h_L + h_R` is itself a velvet sequence of twice the density, so a
//!   BS.775 fold-down of a pair fed from one source has no cancellation
//!   structure at all — its energy is the power sum (+3.01 dB), where the
//!   single-delay blend it replaces loses 1.5 dB to comb cancellation;
//! - each side carries unit energy, where that blend costs 2.97 dB broadband.
//!
//! Per-bin magnitude flatness is *not* a property of any sparse FIR: with M
//! taps the response has ~M²/2 non-zero autocorrelation lags against 2M free
//! parameters, so the Rayleigh floor of ~5.5 dB per-bin sigma cannot be
//! optimized away, at any length or tap count (measured: it does not move).
//! What the design controls is where the dips land — ~60 aperiodic ones here
//! against the ~490 evenly spaced −20 dB notches of the blend. See
//! `docs/plans/mixing/phase2_report.md` for the measured comparison.

use crate::kernels::rng::{next_sign, next_unit};

/// Default filter span. Long enough to decorrelate down to ~200 Hz, short
/// enough to read as one event rather than an echo.
pub const VELVET_LENGTH_MS: f64 = 30.0;

/// Default taps per side — ~1 tap/ms, the density the reference design uses.
pub const VELVET_TAPS_PER_SIDE: usize = 30;

/// Envelope ratio between successive taps, ~−27 dB by the end of the span.
pub const VELVET_DECAY: f64 = 0.9;

/// Default tap-set seed. Positions and signs are a random draw, and the draw
/// matters: over 400k seeds the worst third-octave deviation of the pair
/// ranges 2.5-16 dB, so this one is the best of that search rather than an
/// arbitrary constant.
pub const VELVET_SEED: u64 = 260_797;

/// Default wet fraction. Fully wet, by measurement: a dry component only
/// re-correlates the pair (its correlation is exactly `1 - wet`) and, at every
/// fraction measured, made the third-octave deviation worse rather than
/// better.
pub const VELVET_WET: f64 = 1.0;

/// One side's sparse FIR: `(delay, gain)` pairs, unit energy.
#[derive(Clone, Debug, PartialEq)]
pub struct VelvetFir {
    taps: Vec<(usize, f64)>,
    span: usize,
}

impl VelvetFir {
    /// Taps as `(delay in samples, signed gain)`, ascending by delay.
    pub fn taps(&self) -> &[(usize, f64)] {
        &self.taps
    }

    /// Delay of the last tap.
    pub fn span(&self) -> usize {
        self.span
    }

    /// Whole-buffer convolution, truncated to the input length the way the
    /// other sends are.
    pub fn process(&self, signal: &[f64]) -> Vec<f64> {
        let mut out = vec![0.0; signal.len()];
        for &(delay, gain) in &self.taps {
            if delay >= signal.len() {
                break;
            }
            for (o, x) in out[delay..].iter_mut().zip(signal.iter()) {
                *o += gain * x;
            }
        }
        out
    }
}

/// The streaming counterpart: one ring buffer, read once per tap.
///
/// Carries state across blocks like [`crate::stream::routing::DelayLine`], and
/// agrees with [`VelvetFir::process`] sample for sample from a cold start.
pub struct VelvetLine {
    taps: Vec<(usize, f64)>,
    ring: Vec<f64>,
    write: usize,
}

impl VelvetLine {
    pub fn new(fir: &VelvetFir) -> Self {
        // Power-of-two ring: the read index masks instead of dividing, which
        // is 30 integer divisions per sample saved on the audio thread.
        Self {
            taps: fir.taps.clone(),
            ring: vec![0.0; (fir.span + 1).next_power_of_two()],
            write: 0,
        }
    }

    pub fn reset(&mut self) {
        self.ring.fill(0.0);
        self.write = 0;
    }

    #[inline]
    pub fn tick(&mut self, x: f64) -> f64 {
        let mask = self.ring.len() - 1;
        self.ring[self.write] = x;
        let mut acc = 0.0;
        for &(delay, gain) in &self.taps {
            acc += gain * self.ring[(self.write.wrapping_sub(delay)) & mask];
        }
        self.write = (self.write + 1) & mask;
        acc
    }
}

/// The decorrelator pair for one channel pair.
///
/// `wet` blends in an undelayed copy at constant power, so either side keeps
/// unit energy at any setting; `wet = 0` leaves a plain impulse.
pub fn velvet_pair(
    sample_rate: u32,
    length_ms: f64,
    taps: usize,
    seed: u64,
    wet: f64,
) -> (VelvetFir, VelvetFir) {
    let n = (sample_rate as f64 * length_ms / 1000.0).round() as usize;
    let wet = wet.clamp(0.0, 1.0);
    if n < 2 || taps == 0 || wet == 0.0 {
        let unit = VelvetFir { taps: vec![(0, 1.0)], span: 0 };
        return (unit.clone(), unit);
    }

    let cells = 2 * taps;
    let width = (n - 1) as f64 / cells as f64;
    let mut state = seed;
    let mut sides: [Vec<(usize, f64)>; 2] = [Vec::with_capacity(taps), Vec::with_capacity(taps)];
    let mut envelope = 1.0;

    for cell in 0..cells {
        // One tap per cell, uniformly inside it: adjacent cells cannot collide
        // while `width >= 2`, which is what keeps the two sides disjoint.
        let jitter = next_unit(&mut state) * (width - 1.0).max(0.0);
        let position = (1 + (cell as f64 * width + jitter) as usize).min(n - 1);
        let gain = next_sign(&mut state) * envelope;
        sides[cell % 2].push((position, gain));
        if cell % 2 == 1 {
            envelope *= VELVET_DECAY;
        }
    }

    let mut built = sides.into_iter().map(|mut taps| {
        taps.sort_by_key(|(position, _)| *position);
        let norm = taps.iter().map(|(_, g)| g * g).sum::<f64>().sqrt();
        let scale = wet.sqrt() / norm;
        let mut taps: Vec<(usize, f64)> = taps.iter().map(|(p, g)| (*p, g * scale)).collect();
        if wet < 1.0 {
            taps.insert(0, (0, (1.0 - wet).sqrt()));
        }
        let span = taps.last().map(|(p, _)| *p).unwrap_or(0);
        VelvetFir { taps, span }
    });
    let left = built.next().expect("left side");
    let right = built.next().expect("right side");
    (left, right)
}

/// The default pair, as both bindings and the streaming engine take it.
pub fn velvet_pair_default(sample_rate: u32) -> (VelvetFir, VelvetFir) {
    velvet_pair(
        sample_rate,
        VELVET_LENGTH_MS,
        VELVET_TAPS_PER_SIDE,
        VELVET_SEED,
        VELVET_WET,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernels::fft::RealFft;

    const SR: u32 = 48_000;
    const NFFT: usize = 1 << 16;

    fn default_pair() -> (VelvetFir, VelvetFir) {
        velvet_pair_default(SR)
    }

    fn impulse_response(fir: &VelvetFir) -> Vec<f64> {
        let mut h = vec![0.0; fir.span() + 1];
        for &(delay, gain) in fir.taps() {
            h[delay] += gain;
        }
        h
    }

    /// Per-bin magnitude in dB above 200 Hz, relative to the mean power.
    fn deviation_db(h: &[f64]) -> Vec<f64> {
        let spectrum = RealFft::new(NFFT).rfft(h);
        let bin_hz = SR as f64 / NFFT as f64;
        let power: Vec<f64> = spectrum
            .iter()
            .enumerate()
            .filter(|(i, _)| {
                let f = *i as f64 * bin_hz;
                (200.0..=16_000.0).contains(&f)
            })
            .map(|(_, c)| c.norm_sqr())
            .collect();
        let mean = power.iter().sum::<f64>() / power.len() as f64;
        power.iter().map(|p| 10.0 * (p / mean).log10()).collect()
    }

    /// Worst third-octave band deviation from flat, 200 Hz to 16 kHz.
    fn third_octave_worst(h: &[f64]) -> f64 {
        let spectrum = RealFft::new(NFFT).rfft(h);
        let bin_hz = SR as f64 / NFFT as f64;
        let power: Vec<f64> = spectrum.iter().map(|c| c.norm_sqr()).collect();
        let band_power = |lo: f64, hi: f64| -> Option<f64> {
            let bins: Vec<f64> = power
                .iter()
                .enumerate()
                .filter(|(i, _)| {
                    let f = *i as f64 * bin_hz;
                    f >= lo && f < hi
                })
                .map(|(_, p)| *p)
                .collect();
            (!bins.is_empty()).then(|| bins.iter().sum::<f64>() / bins.len() as f64)
        };
        let reference = band_power(200.0, 16_000.0).expect("band");
        let step = 2.0_f64.powf(1.0 / 3.0);
        let edge = 2.0_f64.powf(1.0 / 6.0);
        let mut centre = 200.0;
        let mut worst = 0.0_f64;
        while centre <= 16_000.0 {
            if let Some(p) = band_power(centre / edge, centre * edge) {
                worst = worst.max((10.0 * (p / reference).log10()).abs());
            }
            centre *= step;
        }
        worst
    }

    /// Dips below `-10 dB`, counted as crossings — a comb produces hundreds of
    /// evenly spaced ones, a velvet sequence a few dozen scattered ones.
    fn dip_count(h: &[f64]) -> usize {
        deviation_db(h)
            .windows(2)
            .filter(|w| w[0] >= -10.0 && w[1] < -10.0)
            .count()
    }

    fn energy(x: &[f64]) -> f64 {
        x.iter().map(|v| v * v).sum()
    }

    fn noise(n: usize, seed: u64) -> Vec<f64> {
        let mut state = seed;
        (0..n).map(|_| next_unit(&mut state) * 2.0 - 1.0).collect()
    }

    #[test]
    fn the_same_seed_builds_the_same_pair() {
        assert_eq!(default_pair(), default_pair());
        let (other, _) = velvet_pair(SR, VELVET_LENGTH_MS, VELVET_TAPS_PER_SIDE, 7, 1.0);
        assert_ne!(other, default_pair().0);
    }

    #[test]
    fn the_pair_is_sparse_and_spans_the_requested_length() {
        let (left, right) = default_pair();
        let n = (SR as f64 * VELVET_LENGTH_MS / 1000.0).round() as usize;
        for side in [&left, &right] {
            assert_eq!(side.taps().len(), VELVET_TAPS_PER_SIDE);
            assert!(side.span() < n, "span {} exceeds {n}", side.span());
            assert!(side.taps().windows(2).all(|w| w[0].0 < w[1].0), "taps not ascending");
        }
        // Both sides reach into the last cell pair, so neither is a short
        // cluster the other has to compensate for.
        assert!(left.span() > n * 9 / 10 && right.span() > n * 9 / 10);
    }

    #[test]
    fn the_sides_share_no_tap_and_carry_unit_energy() {
        let (left, right) = default_pair();
        for side in [&left, &right] {
            let e: f64 = side.taps().iter().map(|(_, g)| g * g).sum();
            assert!((e - 1.0).abs() < 1e-12, "energy {e}");
        }
        for &(p, _) in left.taps() {
            assert!(right.taps().iter().all(|(q, _)| *q != p), "shared tap at {p}");
        }
    }

    /// The property that must never regress: a mono fold-down of the pair
    /// keeps the full power sum, because the two sides cannot cancel.
    #[test]
    fn a_mono_fold_down_of_the_pair_is_the_power_sum() {
        let (left, right) = default_pair();
        let x = noise(48_000, 11);
        let sum: Vec<f64> = left
            .process(&x)
            .iter()
            .zip(right.process(&x).iter())
            .map(|(a, b)| a + b)
            .collect();
        let ratio = 10.0 * (energy(&sum[1440..]) / (2.0 * energy(&x[1440..]))).log10();
        assert!(ratio.abs() < 0.1, "fold-down lost {ratio} dB against the power sum");
    }

    #[test]
    fn neither_side_nor_their_sum_carries_a_comb() {
        let (left, right) = default_pair();
        let hl = impulse_response(&left);
        let hr = impulse_response(&right);
        let sum: Vec<f64> = hl.iter().zip(hr.iter()).map(|(a, b)| a + b).collect();

        for (name, h) in [("left", &hl), ("right", &hr), ("sum", &sum)] {
            // 3.5 dB, not the 1.5 dB the plan guessed at: a sparse FIR sits on
            // a Rayleigh magnitude floor no tap count or length improves. The
            // dip count is the metric that separates this from a comb.
            let worst = third_octave_worst(h);
            assert!(worst < 3.5, "{name} third-octave deviation {worst} dB");
            let dips = dip_count(h);
            assert!(dips < 120, "{name} has {dips} dips, comb-like");
        }

        // The single-delay blend this replaces, measured the same way.
        let delay = (SR as f64 * 0.031) as usize;
        let mut comb = vec![0.0; delay + 1];
        comb[0] = 0.45;
        comb[delay] = 0.55;
        assert!(dip_count(&comb) > 400, "the comb baseline stopped combing");
    }

    #[test]
    fn the_pair_decorrelates_white_noise() {
        let (left, right) = default_pair();
        let x = noise(192_000, 3);
        let a = left.process(&x);
        let b = right.process(&x);
        let cross: f64 = a[1440..].iter().zip(b[1440..].iter()).map(|(p, q)| p * q).sum();
        let corr = cross / (energy(&a[1440..]) * energy(&b[1440..])).sqrt();
        assert!(corr.abs() < 0.4, "interchannel correlation {corr}");

        // Each side on its own holds the input level.
        for (name, y) in [("left", &a), ("right", &b)] {
            let db = 10.0 * (energy(&y[1440..]) / energy(&x[1440..])).log10();
            assert!(db.abs() < 0.2, "{name} moved the level by {db} dB");
        }
    }

    #[test]
    fn the_streaming_form_matches_the_offline_one_across_blocks() {
        let (left, _) = default_pair();
        let x = noise(4096, 5);
        let want = left.process(&x);

        let mut line = VelvetLine::new(&left);
        let mut got = Vec::with_capacity(x.len());
        for block in x.chunks(128) {
            got.extend(block.iter().map(|v| line.tick(*v)));
        }
        for (i, (a, b)) in got.iter().zip(want.iter()).enumerate() {
            assert!((a - b).abs() < 1e-12, "sample {i}: {a} vs {b}");
        }

        line.reset();
        assert_eq!(line.tick(1.0), left.taps()[0].1 * f64::from(left.taps()[0].0 == 0));
    }

    #[test]
    fn the_wet_fraction_trades_flatness_for_correlation() {
        let x = noise(96_000, 9);
        let mut previous = 0.0;
        for wet in [0.25, 0.5, 0.75] {
            let (left, right) = velvet_pair(SR, VELVET_LENGTH_MS, VELVET_TAPS_PER_SIDE, VELVET_SEED, wet);
            let a = left.process(&x);
            let b = right.process(&x);
            let cross: f64 = a[1440..].iter().zip(b[1440..].iter()).map(|(p, q)| p * q).sum();
            let corr = cross / (energy(&a[1440..]) * energy(&b[1440..])).sqrt();
            // Correlation is 1 - wet by construction: only the dry taps overlap.
            assert!((corr - (1.0 - wet)).abs() < 0.05, "wet {wet} gave correlation {corr}");
            assert!(corr < 1.0 - previous + 1e-9);
            previous = wet;
            let e: f64 = left.taps().iter().map(|(_, g)| g * g).sum();
            assert!((e - 1.0).abs() < 1e-12, "wet {wet} lost energy: {e}");
        }

        // Fully dry degenerates to an impulse rather than to silence.
        let (dry, _) = velvet_pair(SR, VELVET_LENGTH_MS, VELVET_TAPS_PER_SIDE, VELVET_SEED, 0.0);
        assert_eq!(dry.taps(), &[(0, 1.0)]);
    }
}
