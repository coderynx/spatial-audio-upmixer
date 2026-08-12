//! Post-decode voicing chain: crossfeed, shelving/presence EQ, M/S widen.
//!
//! Profile values live in `packages/core/src/binaural/profiles.py` and
//! `crosstalk/profiles.py`. The filter topology is the additive
//! subtract/add-band trick rather than native shelving biquads, so the
//! browser can match it parameter-for-parameter.

use crate::kernels::biquad::sosfilt;
use crate::kernels::butter::{butter_bandpass_sos, butter_sos, BandType};

#[derive(Clone, Copy, Debug, Default, serde::Deserialize)]
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

fn crossfeed(
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

fn widen(left: &[f64], right: &[f64], amount: f64) -> (Vec<f64>, Vec<f64>) {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_zero_params_are_an_exact_bypass() {
        let l: Vec<f64> = (0..512).map(|i| (i as f64 * 0.1).sin()).collect();
        let r: Vec<f64> = (0..512).map(|i| (i as f64 * 0.13).cos()).collect();
        let p = VoicingParams { crossfeed_cutoff_hz: 700.0, presence_q: 1.0, ..Default::default() };
        let (out_l, out_r) = apply_voicing(&l, &r, 48_000, &p);
        assert_eq!(out_l, l);
        assert_eq!(out_r, r);
    }

    #[test]
    fn widen_preserves_the_mid_and_scales_the_side() {
        let (l, r) = widen(&[1.0], &[-1.0], 1.0);
        // Pure side content doubles at amount 1.0.
        assert!((l[0] - 2.0).abs() < 1e-15);
        assert!((r[0] + 2.0).abs() < 1e-15);
        let (l, r) = widen(&[1.0], &[1.0], 1.0);
        assert!((l[0] - 1.0).abs() < 1e-15 && (r[0] - 1.0).abs() < 1e-15);
    }

    #[test]
    fn crossfeed_moves_low_frequency_content_across() {
        let n = 4800;
        let sr = 48_000;
        let tone: Vec<f64> = (0..n)
            .map(|i| (2.0 * std::f64::consts::PI * 100.0 * i as f64 / sr as f64).sin())
            .collect();
        let silence = vec![0.0; n];
        let (_, out_r) = crossfeed(&tone, &silence, sr, 0.3, 700.0);
        let energy: f64 = out_r[2400..].iter().map(|v| v * v).sum();
        assert!(energy > 1.0, "expected bleed into the silent channel, got {energy}");
    }
}
