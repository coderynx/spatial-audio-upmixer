//! Parameters the host hands the streaming engine.
//!
//! Every acoustic value here is owned by `packages/core/src/config.py` and
//! the profile tables and reaches the browser through
//! `GET /api/v1/configuration`; nothing in this module has a default of its
//! own beyond "stage absent means stage off".

use serde::Deserialize;

use crate::mastering::{bass::BassParams, compressor::CompParams, limiter::LimiterParams};
use crate::spatial::voicing::VoicingParams;

/// How the mastered bed reaches the listener.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OutputMode {
    Binaural,
    Transaural,
    Stereo,
    Native,
}

/// One positional speaker in the bed, plus the direction it encodes from.
#[derive(Clone, Debug, Deserialize)]
pub struct SpeakerParams {
    pub name: String,
    pub azimuth_rad: f64,
    pub elevation_rad: f64,
    /// Gain for the channel's group (centre, surround, back, height).
    pub group_gain: f64,
    /// BS.775 stereo-downmix contribution; absent for height channels and LFE.
    #[serde(default)]
    pub downmix: Option<(f64, f64)>,
}

/// Which shaped signal feeds a given speaker.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SendShape {
    Left,
    Right,
    Mono,
    SurroundLeft,
    SurroundRight,
    HeightLeft,
    HeightRight,
}

/// Send shaping constants, all served from core.
#[derive(Clone, Copy, Debug, Deserialize)]
pub struct SendParams {
    pub surround_bass_cutoff_hz: f64,
    pub surround_haas_ms: (f64, f64),
    pub height_haas_ms: (f64, f64),
    pub diffuse_blend: f64,
    pub height_low_rolloff_hz: f64,
    pub height_low_rolloff_gain: f64,
    pub height_crossover_hz: f64,
    pub height_high_shelf_gain: f64,
    pub lfe_cutoff_hz: f64,
    pub lfe_filter_order: usize,
    pub lfe_gain: f64,
}

/// Per-stem live state: what it plays into and how loud.
#[derive(Clone, Debug, Deserialize, Default)]
pub struct StemParams {
    /// Speaker name to routing weight, including `"LFE"`.
    #[serde(default)]
    pub routing: Vec<(String, f64)>,
    #[serde(default)]
    pub rebalance_db: f64,
    #[serde(default = "enabled_by_default")]
    pub enabled: bool,
    /// Minimum-phase FIR applied before routing; empty means no stem EQ.
    #[serde(default)]
    pub eq_fir: Vec<f64>,
    /// Energy normalization across the stem's routed sends, computed against
    /// the whole stem exactly as `StemRouter.route` does.
    #[serde(default = "unit_scale")]
    pub route_scale: f64,
}

fn enabled_by_default() -> bool {
    true
}

fn unit_scale() -> f64 {
    1.0
}

/// Mastering-bus stages, in the contracted order. `None` means the stage is
/// off, matching how `MasteringChain` skips an unset profile.
#[derive(Clone, Debug, Deserialize, Default)]
pub struct MasterParams {
    /// Reference-match level gain, applied before the curve.
    #[serde(default = "unit_scale")]
    pub reference_gain: f64,
    /// Reference-match correction FIR, applied at full wet.
    #[serde(default)]
    pub reference_fir: Vec<f64>,
    #[serde(default)]
    pub eq_fir: Vec<f64>,
    #[serde(default = "unit_scale")]
    pub eq_strength: f64,
    #[serde(default)]
    pub compressor: Option<CompParams>,
    #[serde(default)]
    pub bass: Option<BassParams>,
    #[serde(default)]
    pub limiter: Option<LimiterParams>,
    /// Stereo pairs the bass mono-maker couples, as speaker indices.
    #[serde(default)]
    pub stereo_pairs: Vec<(usize, usize)>,
    /// Scalar loudness/true-peak correction measured by the precompute pass.
    #[serde(default = "unit_scale")]
    pub output_gain: f64,
}

/// The whole engine configuration.
#[derive(Clone, Debug, Deserialize)]
pub struct EngineParams {
    pub speakers: Vec<SpeakerParams>,
    /// Index into `speakers` carrying LFE, if the layout has one.
    #[serde(default)]
    pub lfe_index: Option<usize>,
    pub shapes: Vec<SendShape>,
    pub sends: SendParams,
    #[serde(default)]
    pub stems: Vec<StemParams>,
    #[serde(default)]
    pub master: MasterParams,
    pub output_mode: OutputMode,
    /// Flattened `[acn][ear][tap]` binaural decode bank.
    #[serde(default)]
    pub decode_taps: Vec<f64>,
    /// Flattened `[speaker][ear][tap]` crosstalk matrix.
    #[serde(default)]
    pub xtc_taps: Vec<f64>,
    #[serde(default)]
    pub voicing: Option<VoicingParams>,
    #[serde(default)]
    pub soft_limit_threshold: f64,
    /// Engaged by the transport's A/B button: renders the unmastered bed.
    #[serde(default)]
    pub bypass_mastering: bool,
}

impl EngineParams {
    pub fn speaker_index(&self, name: &str) -> Option<usize> {
        self.speakers.iter().position(|s| s.name == name)
    }
}
