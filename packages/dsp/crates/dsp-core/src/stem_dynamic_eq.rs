//! Per-stem linked dynamic EQ, before spatial routing.

use serde::Deserialize;

use crate::mastering::dyneq::{BandParams, DynamicEq};
use crate::stream::state::OnePole;

const SMOOTH_MS: f64 = 5.0;

#[derive(Clone, Copy, Debug, PartialEq, Deserialize)]
pub struct StemDynamicEqBand {
    #[serde(default)]
    pub enabled: bool,
    pub freq_hz: f64,
    pub q: f64,
    pub threshold_db: f64,
    pub ratio: f64,
    pub max_cut_db: f64,
    pub attack_ms: f64,
    pub release_ms: f64,
}

impl From<StemDynamicEqBand> for BandParams {
    fn from(value: StemDynamicEqBand) -> Self {
        Self { freq_hz: value.freq_hz, q: value.q, threshold_db: value.threshold_db,
            ratio: value.ratio, max_cut_db: value.max_cut_db, attack_ms: value.attack_ms,
            release_ms: value.release_ms }
    }
}

#[derive(Clone, Debug, PartialEq, Deserialize)]
pub struct StemDynamicEqParams {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub bands: Vec<StemDynamicEqBand>,
    #[serde(default = "full_mix")]
    pub mix: f64,
}

fn full_mix() -> f64 { 1.0 }

impl StemDynamicEqParams {
    fn bands(&self) -> Vec<BandParams> {
        self.bands.iter().copied().filter(|band| band.enabled).map(Into::into).collect()
    }
    fn is_active(&self) -> bool {
        self.enabled && self.mix > 0.0 && self.bands.iter().any(|band| band.enabled && band.ratio > 1.0 && band.max_cut_db > 0.0)
    }
}

pub struct StemDynamicEq {
    sample_rate: u32,
    params: StemDynamicEqParams,
    stage: Option<DynamicEq>,
    mix: OnePole,
    gain_reduction_db: f64,
}

impl StemDynamicEq {
    pub fn new(sample_rate: u32, params: StemDynamicEqParams) -> Self {
        let stage = params.is_active().then(|| DynamicEq::new(sample_rate, 2, None, &params.bands())).flatten();
        let mix = if params.is_active() { params.mix } else { 0.0 };
        Self { sample_rate, params, stage, mix: OnePole::new_at(SMOOTH_MS, sample_rate as f64, mix), gain_reduction_db: 0.0 }
    }

    pub fn retune(&mut self, sample_rate: u32, params: StemDynamicEqParams) {
        let bands = params.bands();
        let active = params.is_active();
        if active {
            let retained = self.stage.as_mut().is_some_and(|stage| stage.retune(&bands, sample_rate));
            if !retained { self.stage = DynamicEq::new(sample_rate, 2, None, &bands); }
        }
        self.mix.retune(SMOOTH_MS, sample_rate as f64);
        self.sample_rate = sample_rate;
        self.params = params;
    }

    pub fn process_stereo(&mut self, left: &mut [f64], right: &mut [f64]) {
        let target_mix = if self.params.is_active() { self.params.mix } else { 0.0 };
        if target_mix == 0.0 && self.mix.is_settled(0.0) {
            self.gain_reduction_db = 0.0;
            return;
        }
        let Some(stage) = &mut self.stage else { return };
        self.gain_reduction_db = 0.0;
        for (left, right) in left.iter_mut().zip(right.iter_mut()) {
            let (dry_left, dry_right) = (*left, *right);
            if !left.is_finite() { *left = 0.0; }
            if !right.is_finite() { *right = 0.0; }
            stage.process_stereo_frame(left, right);
            let mix = self.mix.tick(target_mix);
            self.gain_reduction_db = stage.gain_reduction_db();
            *left = dry_left + (*left - dry_left) * mix;
            *right = dry_right + (*right - dry_right) * mix;
        }
    }

    pub fn gain_reduction_db(&self) -> f64 { self.gain_reduction_db }

    pub fn reset(&mut self) {
        if let Some(stage) = &mut self.stage { stage.reset(); }
        self.mix.set(if self.params.is_active() { self.params.mix } else { 0.0 });
        self.gain_reduction_db = 0.0;
    }
}

pub fn process(sample_rate: u32, params: StemDynamicEqParams, left: &mut [f64], right: &mut [f64]) {
    StemDynamicEq::new(sample_rate, params).process_stereo(left, right);
}
