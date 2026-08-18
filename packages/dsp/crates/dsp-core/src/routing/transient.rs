//! Transient/sustain split for the surround and height send inputs: holds
//! onsets in the front bed while sustain still reaches the diffuse sends.
//!
//! The detector is causal and per sample so the offline render and the
//! streaming preview run the same state machine — see
//! `docs/plans/mixing/phase11_report.md` §1.4 for why non-causal analysis is
//! ruled out here. It runs per band so a snare hit does not duck the ride
//! wash sharing its moment — see `docs/plans/mixing/phase13_report.md`.

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::linkwitz_riley_lowpass_sos;

/// Fast envelope attack. Short enough to catch a snare's leading edge.
pub const DUCK_ATTACK_MS: f64 = 1.5;

/// Fast envelope release, and so the length of the duck a single onset
/// causes. Also what keeps the gain from moving at audio rate.
pub const DUCK_RELEASE_MS: f64 = 60.0;

/// Slow reference envelope. Long enough that a loud passage raises its own
/// threshold, so only relative onsets score.
pub const DUCK_REFERENCE_MS: f64 = 250.0;

/// Envelope ratio an onset must beat before it ducks at all, and the ratio
/// that ducks fully. The floor is not sensitivity tuning: a rectify-and-smooth
/// follower ripples a few percent within every cycle of a low tone, and
/// without a floor that ripple would amplitude-modulate steady content.
pub const DUCK_THRESHOLD_RATIO: f64 = 1.25;
pub const DUCK_FULL_RATIO: f64 = 2.5;

/// Crossover corners between the three detector bands: the body of a hit
/// below, the cymbal wash the duck must not chase above.
pub const DUCK_BAND_LOW_HZ: f64 = 200.0;
pub const DUCK_BAND_HIGH_HZ: f64 = 4000.0;

const CROSSOVER_ORDER: usize = 4;
const EPS: f64 = 1e-12;

fn pole(ms: f64, sample_rate: u32) -> f64 {
    (-1.0 / (ms * 1e-3 * sample_rate as f64)).exp()
}

/// Shared-gain transient ducker for one stereo send.
///
/// Detection runs on the summed magnitude of both sides and the gain is
/// applied to both, so an onset on one side alone cannot pull the send's
/// image across.
pub struct TransientDucker {
    fast: f64,
    slow: f64,
    attack: f64,
    release: f64,
    reference: f64,
    depth: f64,
}

impl TransientDucker {
    pub fn new(sample_rate: u32, depth: f64) -> Self {
        Self {
            fast: 0.0,
            slow: 0.0,
            attack: pole(DUCK_ATTACK_MS, sample_rate),
            release: pole(DUCK_RELEASE_MS, sample_rate),
            reference: pole(DUCK_REFERENCE_MS, sample_rate),
            depth: depth.clamp(0.0, 1.0),
        }
    }

    pub fn reset(&mut self) {
        self.fast = 0.0;
        self.slow = 0.0;
    }

    /// Re-derive coefficients and depth in place, keeping both envelopes.
    pub fn retune(&mut self, sample_rate: u32, depth: f64) {
        self.attack = pole(DUCK_ATTACK_MS, sample_rate);
        self.release = pole(DUCK_RELEASE_MS, sample_rate);
        self.reference = pole(DUCK_REFERENCE_MS, sample_rate);
        self.depth = depth.clamp(0.0, 1.0);
    }

    pub fn depth(&self) -> f64 {
        self.depth
    }

    /// Send gain for this sample pair, in `[1 - depth, 1]`.
    #[inline]
    pub fn tick(&mut self, left: f64, right: f64) -> f64 {
        if self.depth == 0.0 {
            return 1.0;
        }
        let mag = (left.abs() + right.abs()) * 0.5;

        let coeff = if mag > self.fast { self.attack } else { self.release };
        self.fast = mag + coeff * (self.fast - mag);
        // The reference tracks the fast envelope, not the raw magnitude:
        // against |x| a steady tone reads as its own crest factor forever.
        self.slow = self.fast + self.reference * (self.slow - self.fast);

        // Sub-threshold is the overwhelmingly common case, and taking it
        // before the divide keeps that divide off the audio thread's
        // per-sample dependency chain. Algebraically the same score.
        let threshold = self.slow * DUCK_THRESHOLD_RATIO;
        if self.slow <= EPS || self.fast <= threshold {
            return 1.0;
        }
        let span = self.slow * (DUCK_FULL_RATIO - DUCK_THRESHOLD_RATIO);
        let score = ((self.fast - threshold) / span).min(1.0);
        1.0 - self.depth * score
    }
}

/// Three-band split of one channel: two Linkwitz-Riley low-passes and their
/// subtractive complements.
///
/// The complements are taken by subtraction rather than by matching
/// high-passes, as `mastering::bass::lf_unify` already does: the three bands
/// then sum back to the input exactly, where an LR low/high pair only sums
/// flat in magnitude.
struct BandSplit {
    low: SosFilter,
    mid: SosFilter,
}

impl BandSplit {
    fn new(sample_rate: u32) -> Self {
        Self {
            low: SosFilter::from_flat(&lr_lowpass(DUCK_BAND_LOW_HZ, sample_rate)),
            mid: SosFilter::from_flat(&lr_lowpass(DUCK_BAND_HIGH_HZ, sample_rate)),
        }
    }

    fn reset(&mut self) {
        self.low.reset();
        self.mid.reset();
    }

    fn retune(&mut self, sample_rate: u32) {
        self.low.retune_flat(&lr_lowpass(DUCK_BAND_LOW_HZ, sample_rate));
        self.mid.retune_flat(&lr_lowpass(DUCK_BAND_HIGH_HZ, sample_rate));
    }

    #[inline]
    fn tick(&mut self, x: f64) -> [f64; 3] {
        let low = self.low.tick(x);
        let rest = x - low;
        let mid = self.mid.tick(rest);
        [low, mid, rest - mid]
    }
}

fn lr_lowpass(hz: f64, sample_rate: u32) -> Vec<[f64; 6]> {
    let wn = (hz / (sample_rate as f64 / 2.0)).clamp(1e-4, 0.999);
    linkwitz_riley_lowpass_sos(CROSSOVER_ORDER, wn)
}

/// Shared-gain transient ducker for one stereo send, one gain per band.
///
/// Each band runs its own [`TransientDucker`] over the summed magnitude of
/// both sides, so an onset in one band leaves the others flowing and a
/// one-sided onset still cannot pull the send's image across.
pub struct MultibandDucker {
    split: [BandSplit; 2],
    bands: [TransientDucker; 3],
    depth: f64,
}

impl MultibandDucker {
    pub fn new(sample_rate: u32, depth: f64) -> Self {
        Self {
            split: [BandSplit::new(sample_rate), BandSplit::new(sample_rate)],
            bands: std::array::from_fn(|_| TransientDucker::new(sample_rate, depth)),
            depth: depth.clamp(0.0, 1.0),
        }
    }

    pub fn reset(&mut self) {
        for s in &mut self.split {
            s.reset();
        }
        for b in &mut self.bands {
            b.reset();
        }
    }

    /// Re-derive coefficients and depth in place, keeping every envelope and
    /// the crossover's filter state.
    pub fn retune(&mut self, sample_rate: u32, depth: f64) {
        for s in &mut self.split {
            s.retune(sample_rate);
        }
        for b in &mut self.bands {
            b.retune(sample_rate, depth);
        }
        self.depth = depth.clamp(0.0, 1.0);
    }

    pub fn depth(&self) -> f64 {
        self.depth
    }

    /// Ducked sample pair. At depth 0.0 the input passes through untouched
    /// and the crossover never runs.
    #[inline]
    pub fn tick(&mut self, left: f64, right: f64) -> (f64, f64) {
        if self.depth == 0.0 {
            return (left, right);
        }
        let l = self.split[0].tick(left);
        let r = self.split[1].tick(right);
        let mut out_l = 0.0;
        let mut out_r = 0.0;
        for (i, band) in self.bands.iter_mut().enumerate() {
            let gain = band.tick(l[i], r[i]);
            out_l += l[i] * gain;
            out_r += r[i] * gain;
        }
        (out_l, out_r)
    }
}

/// Duck a whole stereo send input. `depth` of exactly 0.0 returns the inputs
/// untouched, so the static path stays bit for bit what it was.
pub fn transient_duck(
    left: &[f64],
    right: &[f64],
    sample_rate: u32,
    depth: f64,
) -> (Vec<f64>, Vec<f64>) {
    if depth == 0.0 {
        return (left.to_vec(), right.to_vec());
    }
    let mut ducker = MultibandDucker::new(sample_rate, depth);
    let mut out_l = Vec::with_capacity(left.len());
    let mut out_r = Vec::with_capacity(right.len());
    for (l, r) in left.iter().zip(right.iter()) {
        let (dl, dr) = ducker.tick(*l, *r);
        out_l.push(dl);
        out_r.push(dr);
    }
    (out_l, out_r)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SR: u32 = 48_000;

    /// A click train over a steady bed: the clicks must come out quieter
    /// relative to the bed than they went in.
    fn click_train_over_bed(n: usize) -> Vec<f64> {
        (0..n)
            .map(|i| {
                let bed = 0.2 * (2.0 * std::f64::consts::PI * 220.0 * i as f64 / SR as f64).sin();
                let click = if i % 12_000 < 24 { 0.8 } else { 0.0 };
                bed + click
            })
            .collect()
    }

    #[test]
    fn zero_depth_is_the_input_bit_for_bit() {
        let x = click_train_over_bed(48_000);
        let (l, r) = transient_duck(&x, &x, SR, 0.0);
        assert_eq!(l, x);
        assert_eq!(r, x);
    }

    #[test]
    fn transients_are_attenuated_and_sustain_is_not() {
        let x = click_train_over_bed(96_000);
        let (out, _) = transient_duck(&x, &x, SR, 0.7);

        // A window on a click, and one deep in the sustain between clicks.
        let energy = |v: &[f64], from: usize, to: usize| -> f64 {
            v[from..to].iter().map(|s| s * s).sum::<f64>()
        };
        let click_in = energy(&x, 12_000, 12_048);
        let click_out = energy(&out, 12_000, 12_048);
        let sustain_in = energy(&x, 20_000, 23_000);
        let sustain_out = energy(&out, 20_000, 23_000);

        let click_db = 10.0 * (click_out / click_in).log10();
        let sustain_db = 10.0 * (sustain_out / sustain_in).log10();
        // A 0.5 ms click is the detector's stated worst case, and the
        // crossover's group delay spreads it further still.
        assert!(click_db < -4.5, "click only {click_db} dB down");
        assert!(sustain_db > -0.5, "sustain moved {sustain_db} dB");
    }

    #[test]
    fn gain_never_leaves_the_depth_bound() {
        let x = click_train_over_bed(48_000);
        let depth = 0.6;
        let mut ducker = TransientDucker::new(SR, depth);
        for (l, r) in x.iter().zip(x.iter()) {
            let g = ducker.tick(*l, *r);
            assert!((1.0 - depth - 1e-12..=1.0).contains(&g), "gain {g}");
        }
    }

    /// Both sides take the same per-band gain, so a one-sided onset cannot
    /// move the send's image: the quiet side ducks with the loud one.
    #[test]
    fn one_sided_onset_ducks_both_sides() {
        let bed: Vec<f64> = (0..48_000)
            .map(|i| 0.2 * (2.0 * std::f64::consts::PI * 220.0 * i as f64 / SR as f64).sin())
            .collect();
        let mut left = bed.clone();
        for s in left.iter_mut().skip(24_000).take(240) {
            *s += 0.9;
        }
        let (_, out_r) = transient_duck(&left, &bed, SR, 0.7);
        let (_, quiet_r) = transient_duck(&bed, &bed, SR, 0.7);
        let energy = |v: &[f64]| v[24_000..25_000].iter().map(|s| s * s).sum::<f64>();
        assert!(
            energy(&out_r) < 0.5 * energy(&quiet_r),
            "right side did not follow the left's onset"
        );
    }

    /// A shared gain is a scalar on both sides, so proportional inputs stay
    /// proportional through the duck.
    #[test]
    fn proportional_sides_stay_proportional() {
        let left = click_train_over_bed(48_000);
        let right: Vec<f64> = left.iter().map(|s| 0.5 * s).collect();
        let (out_l, out_r) = transient_duck(&left, &right, SR, 0.7);
        for i in 0..left.len() {
            assert!((out_r[i] - 0.5 * out_l[i]).abs() < 1e-12, "sample {i}");
        }
    }

    /// The regression anchor for the crossover: three bands, no gain, back to
    /// the input.
    #[test]
    fn the_bands_sum_back_to_the_input() {
        let x = click_train_over_bed(48_000);
        let mut split = BandSplit::new(SR);
        for (i, s) in x.iter().enumerate() {
            let bands = split.tick(*s);
            let sum: f64 = bands.iter().sum();
            assert!((sum - s).abs() < 1e-12, "sample {i}: {sum} vs {s}");
        }
    }

    /// Steady content scores zero in every band, not only the one the phase 11
    /// test happened to land in.
    #[test]
    fn a_steady_tone_in_any_band_is_left_alone() {
        for hz in [60.0, 440.0, 9_000.0] {
            let tone: Vec<f64> = (0..96_000)
                .map(|i| (2.0 * std::f64::consts::PI * hz * i as f64 / SR as f64).sin())
                .collect();
            let (out, _) = transient_duck(&tone, &tone, SR, 0.8);
            for i in 48_000..96_000 {
                assert!((out[i] - tone[i]).abs() < 1e-4, "{hz} Hz, sample {i}");
            }
        }
    }

    /// The motivating case: a low-band hit must not duck the high-band wash
    /// sharing its moment.
    #[test]
    fn a_low_band_hit_leaves_the_high_band_wash_alone() {
        let n = 96_000;
        let hit: Vec<f64> = (0..n)
            .map(|i| {
                let phase = i % 24_000;
                let env = if phase < 2_400 {
                    (-(phase as f64) / 480.0).exp()
                } else {
                    0.0
                };
                0.9 * env * (2.0 * std::f64::consts::PI * 80.0 * i as f64 / SR as f64).sin()
            })
            .collect();
        let wash: Vec<f64> = (0..n)
            .map(|i| 0.2 * (2.0 * std::f64::consts::PI * 9_000.0 * i as f64 / SR as f64).sin())
            .collect();
        let mixed: Vec<f64> = hit.iter().zip(&wash).map(|(h, w)| h + w).collect();

        let (out, _) = transient_duck(&mixed, &mixed, SR, 0.7);
        // The hit's own band is gone by 2400 samples, so what is left in the
        // window right after it is the wash.
        let tail = |v: &[f64]| v[26_400..47_000].iter().map(|s| s * s).sum::<f64>();
        let kept = 10.0 * (tail(&out) / tail(&mixed)).log10();
        assert!(kept > -0.5, "wash lost {kept} dB to the hit");
    }

    /// Block-by-block ticking is the same as one pass: the streaming preview
    /// and the offline render must not diverge on render-block size.
    #[test]
    fn ticking_in_blocks_matches_one_pass() {
        let x = click_train_over_bed(48_000);
        let (want, _) = transient_duck(&x, &x, SR, 0.5);

        let mut ducker = MultibandDucker::new(SR, 0.5);
        let mut got = Vec::with_capacity(x.len());
        let mut rest = &x[..];
        for size in [333usize, 999, 128].iter().cycle() {
            if rest.is_empty() {
                break;
            }
            let n = (*size).min(rest.len());
            for s in &rest[..n] {
                got.push(ducker.tick(*s, *s).0);
            }
            rest = &rest[n..];
        }
        assert_eq!(got, want);
    }
}
