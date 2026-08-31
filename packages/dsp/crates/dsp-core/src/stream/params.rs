//! Parameters the host hands the streaming engine.
//!
//! Every acoustic value here is owned by `packages/core/src/config.py` and
//! the profile tables and reaches the browser through
//! `GET /api/v1/configuration`; nothing in this module has a default of its
//! own beyond "stage absent means stage off".

use serde::Deserialize;

use crate::mastering::{
    bass::BassParams, clip::ClipParams, compressor::CompParams, dyneq::BandParams,
    head::HeadParams, limiter::LimiterParams,
};
use crate::routing::ambient::AMBIENT_HEIGHT_CROSSOVER_HZ;
use crate::spatial::voicing::VoicingParams;
use crate::stem_dynamics::StemDynamicsParams;
use crate::stem_dynamic_eq::StemDynamicEqParams;
use crate::stem_eq::StemEqParams;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ObjectMode {
    LinkedStereo,
    Mono,
}

#[derive(Clone, Debug, PartialEq, Deserialize)]
pub struct ObjectPlacement {
    pub azimuth_deg: f64,
    pub elevation_deg: f64,
    pub width_deg: f64,
    pub object_size: f64,
    #[serde(default = "unit_scale")]
    pub gain: f64,
    #[serde(default)]
    pub channel_lock: bool,
    #[serde(default)]
    pub zone_exclusion: Vec<String>,
}

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
#[derive(Clone, Debug, PartialEq, Deserialize)]
pub struct SpeakerParams {
    pub name: String,
    pub azimuth_rad: f64,
    pub elevation_rad: f64,
    /// Gain for the channel's group (centre, surround, back, height).
    pub group_gain: f64,
    /// Monitor-only speaker mute. Applied to the finished bed in
    /// `PreviewEngine::render`, never to the routing gain: folding it in
    /// earlier would take the channel out of the shared bass bus and the
    /// linked compressor's detector, changing every other speaker.
    #[serde(default)]
    pub muted: bool,
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

/// Send shaping constants, all served from core. The decorrelator pair the
/// sends run through is structural rather than tunable and lives in
/// `routing::decorrelate`, so nothing about it appears here.
#[derive(Clone, Copy, Debug, PartialEq, Deserialize)]
pub struct SendParams {
    pub surround_bass_cutoff_hz: f64,
    pub height_low_rolloff_hz: f64,
    pub height_low_rolloff_gain: f64,
    pub height_crossover_hz: f64,
    pub height_high_shelf_gain: f64,
    pub height_directional_band_hz: f64,
    pub height_directional_band_gain: f64,
    pub lfe_cutoff_hz: f64,
    pub lfe_filter_order: usize,
    pub lfe_gain: f64,
}

/// Per-stem live state: what it plays into and how loud.
#[derive(Clone, Debug, PartialEq, Deserialize, Default)]
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
    #[serde(default)]
    pub eq: Option<StemEqParams>,
    #[serde(default)]
    pub dynamics: Option<StemDynamicsParams>,
    #[serde(default)]
    pub dynamic_eq: Option<StemDynamicEqParams>,
    /// Energy normalization across the stem's routed sends, computed against
    /// the whole stem exactly as `StemRouter.route` does.
    #[serde(default = "unit_scale")]
    pub route_scale: f64,
    /// How much of the stem's ambient half is sent to the surround speakers,
    /// and to the height speakers. Zero — the default — leaves the stem
    /// routed exactly as it was before the split existed.
    #[serde(default)]
    pub ambient_rear: f64,
    #[serde(default)]
    pub ambient_height: f64,
    /// Per-stem crossover which shares the ambient half between rear and
    /// height sends.
    #[serde(default = "ambient_height_crossover_default")]
    pub ambient_height_crossover_hz: f64,
    /// Presence makes this stem a direct object over its ambient bed.
    #[serde(default)]
    pub object_mode: Option<ObjectMode>,
    #[serde(default)]
    pub object_placement: Option<ObjectPlacement>,
}

fn ambient_height_crossover_default() -> f64 {
    AMBIENT_HEIGHT_CROSSOVER_HZ
}

impl StemParams {
    pub fn wants_ambient(&self) -> bool {
        self.ambient_rear > 0.0 || self.ambient_height > 0.0
    }
}

fn enabled_by_default() -> bool {
    true
}

fn unit_scale() -> f64 {
    1.0
}

/// Mastering-bus stages, in the contracted order. `None` means the stage is
/// off, matching how `MasteringChain` skips an unset profile.
#[derive(Clone, Debug, PartialEq, Deserialize, Default)]
pub struct MasterParams {
    /// Subsonic high-pass at the head of the chain, ahead of everything else.
    #[serde(default)]
    pub head: Option<HeadParams>,
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
    /// Dynamic-EQ bands, between the static EQ and the compressor. Empty —
    /// the default — is the stage absent, not a stage doing nothing.
    #[serde(default)]
    pub dynamic_eq: Vec<BandParams>,
    #[serde(default)]
    pub compressor: Option<CompParams>,
    #[serde(default)]
    pub bass: Option<BassParams>,
    /// Soft clip between loudness normalization and the limiter.
    #[serde(default)]
    pub clip: Option<ClipParams>,
    #[serde(default)]
    pub limiter: Option<LimiterParams>,
    /// Where the LF unifier hands the low band back, as
    /// `(speaker index, weight)`. Weights normally sum to 1, which is what
    /// leaves the coherent low-frequency level unchanged; the LFE entry, when
    /// present, already carries its BS.775 authoring gain.
    #[serde(default)]
    pub lf_targets: Vec<(usize, f64)>,
    /// Scalar loudness/true-peak correction measured by the precompute pass.
    #[serde(default = "unit_scale")]
    pub output_gain: f64,
    /// Renderer-only correction for an object-authored collapsed monitor.
    #[serde(default = "unit_scale")]
    pub monitor_output_gain: f64,
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
    /// Stereo-downmix coefficients BS.775-4 Annex 4 leaves configurable; the
    /// centre/back-fold coefficient is not among them (ledger D6).
    pub surround_downmix_coeff: f64,
    pub height_downmix_coeff: f64,
    /// Restore every routed stem's stereo fold at the routing boundary.
    #[serde(default)]
    pub spatial_downmix_lock: bool,
    #[serde(default)]
    pub stems: Vec<StemParams>,
    #[serde(default)]
    pub master: MasterParams,
    /// FIR taps travel separately from a live JSON update in the worklet.
    #[serde(default)]
    pub transferred_firs: bool,
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
    /// BS.1770 channel weights the live momentary/short-term meters read the
    /// delivered channels with, in channel order — the same set a
    /// `MeasurementPass` is given. Empty leaves them unity-weighted, which is
    /// what a collapsed pair needs; a native bed wider than 5.1 is metered on
    /// its 5.1 re-render, whose weights are fixed and ignore these.
    #[serde(default)]
    pub meter_weights: Vec<f64>,
}

impl EngineParams {
    pub fn speaker_index(&self, name: &str) -> Option<usize> {
        self.speakers.iter().position(|s| s.name == name)
    }

    /// Per-speaker share of an ambient send, by shape class: the amount is
    /// spread over the class's speakers as `1/sqrt(n)`, so a 7.1.4's four
    /// surrounds carry the same total as a 5.1's two.
    pub fn ambient_share(&self, shape: SendShape) -> f64 {
        let class = |s: SendShape| {
            matches!(
                (shape, s),
                (
                    SendShape::SurroundLeft | SendShape::SurroundRight,
                    SendShape::SurroundLeft | SendShape::SurroundRight
                ) | (
                    SendShape::HeightLeft | SendShape::HeightRight,
                    SendShape::HeightLeft | SendShape::HeightRight
                )
            )
        };
        let count = self.shapes.iter().filter(|s| class(**s)).count();
        if count == 0 {
            0.0
        } else {
            1.0 / (count as f64).sqrt()
        }
    }
}
