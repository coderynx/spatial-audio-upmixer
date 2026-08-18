//! Post-decode voicing chain: crossfeed, shelving/presence EQ, M/S widen.
//!
//! Profile values live in `packages/core/src/binaural/profiles.py` and
//! `crosstalk/profiles.py`. The filter topology is the additive
//! subtract/add-band trick rather than native shelving biquads, so the
//! browser can match it parameter-for-parameter.

use crate::kernels::biquad::sosfilt;
use crate::kernels::butter::{butter_bandpass_sos, butter_sos, BandType};

#[derive(Clone, Copy, Debug, Default, PartialEq, serde::Deserialize)]
pub struct VoicingParams {
    pub crossfeed_amount: f64,
    pub crossfeed_cutoff_hz: f64,
    pub bass_shelf_hz: f64,
    pub bass_shelf_gain_db: f64,
    pub air_shelf_hz: f64,
    pub air_shelf_gain_db: f64,
    pub presence_hz: f64,
    pub presence_gain_db: f64,
    pub presence_q: f64,
    pub stereo_widen: f64,
}

fn shelf(signal: &[f64], sample_rate: u32, freq_hz: f64, gain_db: f64, band: BandType) -> Vec<f64> {
    if gain_db == 0.0 {
        return signal.to_vec();
    }
    let nyq = sample_rate as f64 / 2.0;
    let sos = butter_sos(2, freq_hz / nyq, band);
    let filtered = sosfilt(&sos, signal);
    let gain = 10.0_f64.powf(gain_db / 20.0) - 1.0;
    signal.iter().zip(filtered.iter()).map(|(x, b)| x + b * gain).collect()
}

fn presence(signal: &[f64], sample_rate: u32, freq_hz: f64, gain_db: f64, q: f64) -> Vec<f64> {
    if gain_db == 0.0 {
        return signal.to_vec();
    }
    let nyq = sample_rate as f64 / 2.0;
    let bandwidth = (freq_hz / q).max(1.0);
    let low = (freq_hz - bandwidth / 2.0).max(1.0) / nyq;
    let high = (freq_hz + bandwidth / 2.0).min(nyq - 1.0) / nyq;
    let sos = butter_bandpass_sos(2, low, high);
    let filtered = sosfilt(&sos, signal);
    let gain = 10.0_f64.powf(gain_db / 20.0) - 1.0;
    signal.iter().zip(filtered.iter()).map(|(x, b)| x + b * gain).collect()
}

pub fn crossfeed(
    left: &[f64],
    right: &[f64],
    sample_rate: u32,
    amount: f64,
    cutoff_hz: f64,
) -> (Vec<f64>, Vec<f64>) {
    if amount <= 0.0 {
        return (left.to_vec(), right.to_vec());
    }
    let nyq = sample_rate as f64 / 2.0;
    let sos = butter_sos(1, cutoff_hz / nyq, BandType::Low);
    let bleed_l = sosfilt(&sos, left);
    let bleed_r = sosfilt(&sos, right);
    let out_l = left
        .iter()
        .zip(bleed_r.iter())
        .map(|(x, b)| x * (1.0 - amount) + b * amount)
        .collect();
    let out_r = right
        .iter()
        .zip(bleed_l.iter())
        .map(|(x, b)| x * (1.0 - amount) + b * amount)
        .collect();
    (out_l, out_r)
}

pub fn widen(left: &[f64], right: &[f64], amount: f64) -> (Vec<f64>, Vec<f64>) {
    if amount == 0.0 {
        return (left.to_vec(), right.to_vec());
    }
    let mut out_l = Vec::with_capacity(left.len());
    let mut out_r = Vec::with_capacity(right.len());
    for (l, r) in left.iter().zip(right.iter()) {
        let mid = (l + r) * 0.5;
        let side = (l - r) * 0.5 * (1.0 + amount);
        out_l.push(mid + side);
        out_r.push(mid - side);
    }
    (out_l, out_r)
}

/// The chain in signal-graph order: crossfeed, then EQ, then widen.
pub fn apply_voicing(
    left: &[f64],
    right: &[f64],
    sample_rate: u32,
    p: &VoicingParams,
) -> (Vec<f64>, Vec<f64>) {
    let (mut l, mut r) = crossfeed(left, right, sample_rate, p.crossfeed_amount, p.crossfeed_cutoff_hz);
    l = shelf(&l, sample_rate, p.bass_shelf_hz, p.bass_shelf_gain_db, BandType::Low);
    r = shelf(&r, sample_rate, p.bass_shelf_hz, p.bass_shelf_gain_db, BandType::Low);
    l = shelf(&l, sample_rate, p.air_shelf_hz, p.air_shelf_gain_db, BandType::High);
    r = shelf(&r, sample_rate, p.air_shelf_hz, p.air_shelf_gain_db, BandType::High);
    l = presence(&l, sample_rate, p.presence_hz, p.presence_gain_db, p.presence_q);
    r = presence(&r, sample_rate, p.presence_hz, p.presence_gain_db, p.presence_q);
    widen(&l, &r, p.stereo_widen)
}
