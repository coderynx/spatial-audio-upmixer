//! Linked-detection dynamic EQ: a few bell bands that act only when their own
//! band flares.
//!
//! Each band carries one detector per bed channel — the band's own RBJ
//! band-pass, built from the same `(f0, Q)` as its bell — and sums their
//! powers into a single linked RMS, the topology
//! [`super::compressor::bus_compress`] uses. That produces *one* gain
//! trajectory per band, realized as one bell design applied identically to
//! every non-LFE channel, so the stage stays a shared time-varying filter
//! across the bed and commutes with the LF sum (see
//! `docs/contracts/preview_export_parity.md` §1).
//!
//! Detection is per band but never per channel: the failure that closed
//! mixing phase 13 was independent detectors diverging across a broadband
//! decay, and nothing here can diverge across channels by construction.
//!
//! The bell's coefficients are recomputed whenever its gain moves, keeping
//! the section's delay registers, and at unity gain the design is exactly
//! `b == a`, so a band that never crosses its threshold returns the input
//! sample for sample.

use crate::kernels::biquad::{bandpass_sos, peaking_sos, Sos};

use super::compressor::{alpha, knee_gain_db};
use super::non_lfe;

/// Soft knee every band's gain computer runs, in dB. Structural rather than
/// tunable: a hard knee chatters as the detector crosses the threshold, and
/// the value only has to be wide enough to stop that.
pub const KNEE_DB: f64 = 6.0;

/// Bands a chain may carry. Surgical correction is a handful of moves, and
/// each band costs two biquads per channel per sample on the audio thread.
pub const MAX_BANDS: usize = 4;

#[derive(Clone, Copy, Debug, PartialEq, serde::Deserialize)]
pub struct BandParams {
    pub freq_hz: f64,
    pub q: f64,
    /// Level the band's own linked RMS has to reach before the bell engages.
    pub threshold_db: f64,
    /// Slope above the threshold, exactly as the bus compressor's ratio —
    /// 1.0 leaves the band inert whatever the detector reads.
    pub ratio: f64,
    pub attack_ms: f64,
    pub release_ms: f64,
}

struct Band {
    params: BandParams,
    /// One detector and one bell per bed channel, in `channels` order.
    detect: Vec<Sos>,
    bell: Vec<Sos>,
    attack: f64,
    release: f64,
    fast: f64,
    slow: f64,
    /// Design constants of the bell that do not depend on its gain.
    alpha: f64,
    cos_w0: f64,
    /// Gain the bell sections currently realize, so the design is rebuilt
    /// only when the detector actually moves it.
    gain: f64,
    peak_cut_db: f64,
}

impl Band {
    fn new(params: BandParams, sample_rate: u32, n_bed: usize) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        let wn = (params.freq_hz / nyq).clamp(1e-4, 0.999);
        let q = params.q.max(0.1);
        let w0 = std::f64::consts::PI * wn;
        Self {
            detect: vec![Sos::new(bandpass_sos(wn, q)); n_bed],
            bell: vec![Sos::new(peaking_sos(wn, q, 1.0)); n_bed],
            attack: alpha(params.attack_ms, sample_rate),
            release: alpha(params.release_ms, sample_rate),
            fast: 0.0,
            slow: 0.0,
            alpha: w0.sin() / (2.0 * q),
            cos_w0: w0.cos(),
            gain: 1.0,
            peak_cut_db: 0.0,
            params,
        }
    }

    fn reset(&mut self) {
        for section in self.detect.iter_mut().chain(self.bell.iter_mut()) {
            section.reset();
        }
        self.fast = 0.0;
        self.slow = 0.0;
        self.peak_cut_db = 0.0;
    }

    /// Detector RMS across the bed for one sample, advancing every band-pass.
    #[inline]
    fn linked_rms(&mut self, bed: &super::Bed, channels: &[usize], frame: usize) -> f64 {
        let mut acc = 0.0;
        for (slot, &channel) in channels.iter().enumerate() {
            let v = self.detect[slot].tick(bed[channel][frame]);
            acc += v * v;
        }
        (acc / channels.len() as f64 + 1e-20).sqrt()
    }

    /// Gain the bell should realize for one sample, from the detector level.
    #[inline]
    fn gain_for(&mut self, rms: f64) -> f64 {
        self.fast += self.attack * (rms - self.fast);
        self.slow += self.release * (rms - self.slow);
        let env_db = 20.0 * self.fast.max(self.slow).max(1e-20).log10();
        let cut_db = knee_gain_db(env_db, self.params.threshold_db, self.params.ratio, KNEE_DB);
        self.peak_cut_db = self.peak_cut_db.max(-cut_db);
        10.0_f64.powf(cut_db / 20.0)
    }

    /// Filter one sample of every bed channel through the bell, redesigning
    /// it first when the gain moved.
    #[inline]
    fn apply(&mut self, bed: &mut super::Bed, channels: &[usize], frame: usize, gain: f64) {
        if gain != self.gain {
            let a = gain.sqrt();
            let coeffs = [
                1.0 + self.alpha * a,
                -2.0 * self.cos_w0,
                1.0 - self.alpha * a,
                1.0 + self.alpha / a,
                -2.0 * self.cos_w0,
                1.0 - self.alpha / a,
            ];
            for section in &mut self.bell {
                section.retune(coeffs);
            }
            self.gain = gain;
        }
        for (slot, &channel) in channels.iter().enumerate() {
            bed[channel][frame] = self.bell[slot].tick(bed[channel][frame]);
        }
    }
}

/// The stage: every band, sharing one bed-channel list.
pub struct DynamicEq {
    bands: Vec<Band>,
    channels: Vec<usize>,
}

impl DynamicEq {
    /// `None` when nothing would act — no bands, no bed, or every band at a
    /// ratio of 1.0 — so the stage costs nothing when it is off.
    pub fn new(
        sample_rate: u32,
        n_channels: usize,
        lfe: Option<usize>,
        bands: &[BandParams],
    ) -> Option<Self> {
        let channels = non_lfe(n_channels, lfe);
        let bands: Vec<Band> = bands
            .iter()
            .filter(|b| b.ratio > 1.0)
            .map(|b| Band::new(*b, sample_rate, channels.len()))
            .collect();
        (!bands.is_empty() && !channels.is_empty()).then_some(Self { bands, channels })
    }

    pub fn reset(&mut self) {
        for band in &mut self.bands {
            band.reset();
        }
    }

    /// Whether this stage was built for exactly `bands`, so a live parameter
    /// edit that changed nothing does not restart the detectors.
    pub fn matches(&self, bands: &[BandParams]) -> bool {
        let active: Vec<&BandParams> = bands.iter().filter(|b| b.ratio > 1.0).collect();
        self.bands.len() == active.len()
            && self.bands.iter().zip(active).all(|(band, p)| band.params == *p)
    }

    /// Run every band over the whole bed, in place.
    pub fn process(&mut self, bed: &mut super::Bed) {
        let frames = self.channels.iter().map(|&i| bed[i].len()).min().unwrap_or(0);
        for frame in 0..frames {
            for band in &mut self.bands {
                let rms = band.linked_rms(bed, &self.channels, frame);
                let gain = band.gain_for(rms);
                band.apply(bed, &self.channels, frame, gain);
            }
        }
    }

    /// Deepest cut each band reached, in dB, in the order they were given.
    pub fn peak_cut_db(&self) -> Vec<f64> {
        self.bands.iter().map(|b| b.peak_cut_db).collect()
    }
}

/// Run the dynamic EQ over a whole bed, returning each band's deepest cut.
pub fn dynamic_eq(
    bed: &mut super::Bed,
    lfe: Option<usize>,
    sample_rate: u32,
    bands: &[BandParams],
) -> Vec<f64> {
    match DynamicEq::new(sample_rate, bed.len(), lfe, bands) {
        Some(mut stage) => {
            stage.process(bed);
            stage.peak_cut_db()
        }
        None => Vec::new(),
    }
}
