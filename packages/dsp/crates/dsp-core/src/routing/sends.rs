//! Shaping applied to a source before it reaches a height speaker: the
//! elevation cue EQ. Decorrelation itself lives in `routing::decorrelate`.

use crate::kernels::biquad::sosfilt;
use crate::kernels::butter::{butter_sos, BandType};

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
