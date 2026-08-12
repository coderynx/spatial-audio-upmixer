//! Shaping applied to a source before it reaches a surround or height
//! speaker: Haas decorrelation, early-reflection diffusion, and the
//! elevation cue EQ.

use crate::kernels::biquad::sosfilt;
use crate::kernels::butter::{butter_sos, BandType};

/// Delay by whole samples, zero-padded at the head.
pub fn haas_decorrelate(signal: &[f64], delay_samples: usize) -> Vec<f64> {
    if delay_samples == 0 {
        return signal.to_vec();
    }
    let mut out = vec![0.0; signal.len()];
    if delay_samples < signal.len() {
        out[delay_samples..].copy_from_slice(&signal[..signal.len() - delay_samples]);
    }
    out
}

/// Blend the dry signal with a delayed copy to suggest room diffusion
/// without convolving a full impulse response.
pub fn diffuse_send(signal: &[f64], sample_rate: u32, delay_ms: f64, blend: f64) -> Vec<f64> {
    let delay_n = (sample_rate as f64 * delay_ms / 1000.0) as usize;
    let delayed = haas_decorrelate(signal, delay_n);
    signal
        .iter()
        .zip(delayed.iter())
        .map(|(dry, wet)| dry * (1.0 - blend) + wet * blend)
        .collect()
}

/// Sub-bass rolloff plus high-frequency lift, mirroring the HRTF elevation
/// cue for height channel sends.
pub fn elevation_eq(
    signal: &[f64],
    sample_rate: u32,
    low_rolloff_hz: f64,
    low_rolloff_gain: f64,
    high_shelf_hz: f64,
    high_shelf_gain: f64,
) -> Vec<f64> {
    let nyq = sample_rate as f64 / 2.0;
    let sos_lp = butter_sos(1, low_rolloff_hz / nyq, BandType::Low);
    let low_comp = sosfilt(&sos_lp, signal);
    let bass_shaped: Vec<f64> = signal
        .iter()
        .zip(low_comp.iter())
        .map(|(x, low)| x - low * (1.0 - low_rolloff_gain))
        .collect();

    let sos_hp = butter_sos(2, high_shelf_hz / nyq, BandType::High);
    let high = sosfilt(&sos_hp, &bass_shaped);
    bass_shaped
        .iter()
        .zip(high.iter())
        .map(|(x, hp)| x + hp * (high_shelf_gain - 1.0))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn haas_shifts_without_changing_length() {
        let signal = [1.0, 2.0, 3.0, 4.0];
        assert_eq!(haas_decorrelate(&signal, 2), vec![0.0, 0.0, 1.0, 2.0]);
        assert_eq!(haas_decorrelate(&signal, 0), signal.to_vec());
    }

    #[test]
    fn diffuse_send_is_a_pure_dry_wet_blend() {
        let signal: Vec<f64> = (0..100).map(|i| i as f64).collect();
        let out = diffuse_send(&signal, 48_000, 0.0, 0.55);
        // A zero delay makes wet and dry identical, so the blend is a no-op.
        for (a, b) in out.iter().zip(signal.iter()) {
            assert!((a - b).abs() < 1e-12);
        }
    }

    #[test]
    fn elevation_eq_attenuates_low_frequency_content() {
        let sr = 48_000;
        let low: Vec<f64> = (0..4800)
            .map(|i| (2.0 * std::f64::consts::PI * 50.0 * i as f64 / sr as f64).sin())
            .collect();
        let out = elevation_eq(&low, sr, 150.0, 0.15, 3000.0, 1.5);
        let before: f64 = low[2400..].iter().map(|v| v * v).sum();
        let after: f64 = out[2400..].iter().map(|v| v * v).sum();
        assert!(after < before * 0.5, "{after} vs {before}");
    }
}
