//! Deserialized parameter blocks the host sends as JSON.

use serde::Deserialize;
use upmixer_dsp_core::mastering::{bass, compressor, limiter};

/// Mastering parameters as the host sends them. Every value is owned by
/// `packages/core/src/config.py` and the profile tables and is served to the
/// browser — nothing here has a default of its own.
#[derive(Deserialize)]
pub(crate) struct MasterParams {
    #[serde(default)]
    pub(crate) lfe_index: Option<usize>,
    #[serde(default)]
    pub(crate) lf_targets: Vec<(usize, f64)>,
    /// Reference-match level gain, applied before the correction FIR.
    #[serde(default = "unit")]
    pub(crate) reference_gain: f64,
    #[serde(default)]
    pub(crate) reference_fir: Vec<f64>,
    #[serde(default)]
    pub(crate) eq_fir: Vec<f64>,
    #[serde(default = "unit")]
    pub(crate) eq_strength: f64,
    #[serde(default)]
    pub(crate) compressor: Option<compressor::CompParams>,
    #[serde(default)]
    pub(crate) bass: Option<bass::BassParams>,
    #[serde(default)]
    pub(crate) limiter: Option<limiter::LimiterParams>,
}

fn unit() -> f64 {
    1.0
}

/// Binaural collapse parameters for the offline harness.
#[derive(Deserialize)]
pub(crate) struct CollapseParams {
    pub(crate) directions: Vec<(f64, f64)>,
    #[serde(default)]
    pub(crate) lfe_index: Option<usize>,
    #[serde(default)]
    pub(crate) lfe_gain: f64,
    #[serde(default)]
    pub(crate) lfe_cutoff_hz: f64,
    #[serde(default)]
    pub(crate) lfe_filter_order: usize,
    pub(crate) decode_taps: Vec<f64>,
    pub(crate) n_taps: usize,
    #[serde(default)]
    pub(crate) voicing: Option<upmixer_dsp_core::spatial::voicing::VoicingParams>,
    /// The delivery tail: BS.1770 normalization then a soft limit, matching
    /// `render_binaural_delivery`. Absent means stop after voicing.
    #[serde(default)]
    pub(crate) delivery: Option<DeliveryParams>,
}

#[derive(Deserialize)]
pub(crate) struct DeliveryParams {
    pub(crate) target_lkfs: f64,
    pub(crate) max_tp_dbtp: f64,
    pub(crate) max_gain_db: f64,
    pub(crate) soft_limit_threshold: f64,
}
