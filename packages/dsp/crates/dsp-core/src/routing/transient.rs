//! Transient/sustain split for the surround and height send inputs: holds
//! onsets in the front bed while sustain still reaches the diffuse sends.
//!
//! The detector is causal and per sample so the offline render and the
//! streaming preview run the same state machine — see
//! `docs/plans/mixing/phase11_report.md` §1.4 for why non-causal analysis is
//! ruled out here.

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
    let mut ducker = TransientDucker::new(sample_rate, depth);
    let mut out_l = Vec::with_capacity(left.len());
    let mut out_r = Vec::with_capacity(right.len());
    for (l, r) in left.iter().zip(right.iter()) {
        let gain = ducker.tick(*l, *r);
        out_l.push(l * gain);
        out_r.push(r * gain);
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
        assert!(click_db < -6.0, "click only {click_db} dB down");
        assert!(sustain_db > -0.5, "sustain moved {sustain_db} dB");
    }

    #[test]
    fn steady_tone_is_left_alone_after_its_own_onset() {
        let tone: Vec<f64> = (0..96_000)
            .map(|i| (2.0 * std::f64::consts::PI * 440.0 * i as f64 / SR as f64).sin())
            .collect();
        let (out, _) = transient_duck(&tone, &tone, SR, 0.8);
        for i in 48_000..96_000 {
            assert!((out[i] - tone[i]).abs() < 1e-6, "sample {i}");
        }
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

    /// Both sides take the same gain, so a one-sided onset cannot move the
    /// send's image.
    #[test]
    fn one_sided_onset_does_not_shift_the_balance() {
        let bed: Vec<f64> = (0..48_000)
            .map(|i| 0.2 * (2.0 * std::f64::consts::PI * 220.0 * i as f64 / SR as f64).sin())
            .collect();
        let mut left = bed.clone();
        for s in left.iter_mut().skip(24_000).take(24) {
            *s += 0.9;
        }
        let (out_l, out_r) = transient_duck(&left, &bed, SR, 0.7);
        for i in 0..48_000 {
            let want = out_l[i] * bed[i];
            let got = out_r[i] * left[i];
            assert!((want - got).abs() < 1e-9, "sample {i}");
        }
    }

    /// Block-by-block ticking is the same as one pass: the streaming preview
    /// and the offline render must not diverge on render-block size.
    #[test]
    fn ticking_in_blocks_matches_one_pass() {
        let x = click_train_over_bed(48_000);
        let (want, _) = transient_duck(&x, &x, SR, 0.5);

        let mut ducker = TransientDucker::new(SR, 0.5);
        let mut got = Vec::with_capacity(x.len());
        let mut rest = &x[..];
        for size in [333usize, 999, 128].iter().cycle() {
            if rest.is_empty() {
                break;
            }
            let n = (*size).min(rest.len());
            for s in &rest[..n] {
                got.push(s * ducker.tick(*s, *s));
            }
            rest = &rest[n..];
        }
        assert_eq!(got, want);
    }
}
