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
/// that ducks fully — 8 dB and 12 dB over the running mean.
///
/// These are two independent controls, and moving both at once is a mistake
/// worth not repeating.
///
/// The **threshold** decides what counts as an onset. It has to sit above
/// ordinary crest variation, not inside it: measured over real stems the
/// fast/slow ratio of sustained material runs p75 ~1.2 and p90 ~1.5 while
/// percussive onsets reach 16-45, so the original 1.25 fired on the top
/// quartile of normal peakiness — a ride wash then scored as heavily as a
/// snare hit (mean 0.120 vs 0.126), the exact opposite of this module's
/// purpose, and the continuous mid-scale gain motion that produces is heard
/// as compression rather than as ducking.
///
/// The **span** above it decides how hard a qualifying onset ducks. Widening
/// it leaves the same events triggering but makes each one shallower: at
/// 2.5/8.0 the duty cycle was right and a snare saturated only 2.5% of the
/// time against the original's 9.5%, which measures as clean selectivity and
/// is inaudible. 4.0 keeps the selectivity — `active` depends on the
/// threshold alone — while restoring 5.8% saturation.
pub const DUCK_THRESHOLD_RATIO: f64 = 2.5;
pub const DUCK_FULL_RATIO: f64 = 4.0;

/// Deepest attenuation one band may reach, -20 dB. Not a taste setting: at
/// depth 1.0 the gain would otherwise land on exactly 0.0 and annihilate the
/// band rather than duck it, so a band that saturates leaves its neighbours
/// sounding alone — measured as a 6x jump in cymbal timbre swing between
/// depth 0.99 and 1.00, against a smooth curve everywhere below.
pub const DUCK_MIN_GAIN: f64 = 0.1;

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

    /// Send gain for this sample pair, in `[max(1 - depth, DUCK_MIN_GAIN), 1]`.
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
        (1.0 - self.depth * score).max(DUCK_MIN_GAIN)
    }
}

/// Three-band split of one channel: two Linkwitz-Riley low-passes and their
/// subtractive complements.
///
/// The complements are taken by subtraction rather than by matching
/// high-passes, as `mastering::bass::lf_unify` already does: the three bands
/// then sum back to the input exactly, where an LR low/high pair only sums
/// flat in magnitude.
pub struct BandSplit {
    low: SosFilter,
    mid: SosFilter,
}

impl BandSplit {
    pub fn new(sample_rate: u32) -> Self {
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
    pub fn tick(&mut self, x: f64) -> [f64; 3] {
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
    last_gain: f64,
}

impl MultibandDucker {
    pub fn new(sample_rate: u32, depth: f64) -> Self {
        Self {
            split: [BandSplit::new(sample_rate), BandSplit::new(sample_rate)],
            bands: std::array::from_fn(|_| TransientDucker::new(sample_rate, depth)),
            depth: depth.clamp(0.0, 1.0),
            last_gain: 1.0,
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

    /// Deepest band gain the last [`Self::tick`] applied, in
    /// `[1 - depth, 1]`. The readout the UI's duck display samples — the
    /// deepest band rather than an average, since one band collapsing is what
    /// the duck is for and what a listener hears move.
    pub fn last_gain(&self) -> f64 {
        self.last_gain
    }

    /// Ducked sample pair. At depth 0.0 the input passes through untouched
    /// and the crossover never runs.
    #[inline]
    pub fn tick(&mut self, left: f64, right: f64) -> (f64, f64) {
        if self.depth == 0.0 {
            self.last_gain = 1.0;
            return (left, right);
        }
        let l = self.split[0].tick(left);
        let r = self.split[1].tick(right);
        let mut out_l = 0.0;
        let mut out_r = 0.0;
        let mut lowest = 1.0;
        for (i, band) in self.bands.iter_mut().enumerate() {
            let gain = band.tick(l[i], r[i]);
            out_l += l[i] * gain;
            out_r += r[i] * gain;
            lowest = f64::min(lowest, gain);
        }
        self.last_gain = lowest;
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
