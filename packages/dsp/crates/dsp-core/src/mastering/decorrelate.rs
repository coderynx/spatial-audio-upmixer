//! Mid-bass stereo decorrelation: an ERB-warped allpass cascade per channel,
//! applied to the sustained part of the 100-300 Hz band only.
//!
//! Band corners, cascade length and the group-delay ceiling are owned by
//! `packages/core/src/mastering/bass.py`. See
//! `docs/standards/spatial_layouts_bs775_bs2051.md`, "Bass management".

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::butter_bandpass_sos;
use crate::kernels::rng::next_unit;

use super::bass::BassParams;
use super::compressor::alpha;

/// Keeps the sustain gate at unity while both envelopes converge from
/// silence, instead of `0/0`.
const GATE_EPS: f64 = 1e-9;

/// Lower bound on the pole radius, per Kermit-Canfield & Abel: below this the
/// section's phase response is too shallow to decorrelate anything.
pub const POLE_R_MIN: f64 = 0.5;

/// Upper bound regardless of the group-delay budget — `2(1+r)/(1-r)` grows
/// without bound as `r → 1`, and a single runaway section would read as
/// ringing rather than width.
pub const POLE_R_MAX: f64 = 0.95;

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

/// Glasberg & Moore's ERB-rate scale, the warping that puts the poles at
/// roughly constant density per critical band rather than per hertz.
pub fn erb_rate(hz: f64) -> f64 {
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
pub fn cascade_rows(channel: usize, sample_rate: u32, p: &BassParams) -> Vec<[f64; 6]> {
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
