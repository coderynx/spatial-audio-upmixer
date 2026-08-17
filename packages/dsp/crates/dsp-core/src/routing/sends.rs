//! Shaping applied to a source before it reaches a height speaker: the
//! elevation cue EQ. Decorrelation itself lives in `routing::decorrelate`.

use crate::kernels::biquad::{peaking_sos, sosfilt};
use crate::kernels::butter::{butter_sos, BandType};

/// Q of the directional-band peak. Structural, not served: the band is a
/// psychoacoustic cue (Blauert), its width is not a mix control.
pub const DIRECTIONAL_BAND_Q: f64 = 1.0;

/// The directional band as a single section, `wn` normalized to Nyquist.
pub fn directional_band_sos(band_hz: f64, sample_rate: u32, band_gain: f64) -> [f64; 6] {
    peaking_sos(
        band_hz / (sample_rate as f64 / 2.0),
        DIRECTIONAL_BAND_Q,
        band_gain,
    )
}

/// Sub-bass rolloff, high-frequency lift, and the ~8 kHz directional band,
/// mirroring the HRTF elevation cue for height channel sends.
///
/// A `directional_band_gain` of exactly 1.0 skips the band section outright,
/// so the default voicing is the pre-band output bit for bit.
pub fn elevation_eq(
    signal: &[f64],
    sample_rate: u32,
    low_rolloff_hz: f64,
    low_rolloff_gain: f64,
    high_shelf_hz: f64,
    high_shelf_gain: f64,
    directional_band_hz: f64,
    directional_band_gain: f64,
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
    let shelved: Vec<f64> = bass_shaped
        .iter()
        .zip(high.iter())
        .map(|(x, hp)| x + hp * (high_shelf_gain - 1.0))
        .collect();

    if directional_band_gain == 1.0 {
        return shelved;
    }
    sosfilt(
        &[directional_band_sos(
            directional_band_hz,
            sample_rate,
            directional_band_gain,
        )],
        &shelved,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernels::biquad::sos_magnitude;

    #[test]
    fn elevation_eq_attenuates_low_frequency_content() {
        let sr = 48_000;
        let low: Vec<f64> = (0..4800)
            .map(|i| (2.0 * std::f64::consts::PI * 50.0 * i as f64 / sr as f64).sin())
            .collect();
        let out = elevation_eq(&low, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, 1.0);
        let before: f64 = low[2400..].iter().map(|v| v * v).sum();
        let after: f64 = out[2400..].iter().map(|v| v * v).sum();
        assert!(after < before * 0.5, "{after} vs {before}");
    }

    #[test]
    fn unity_band_gain_is_the_pre_band_output_bit_for_bit() {
        let sr = 48_000;
        let signal: Vec<f64> = (0..4800).map(|i| (i as f64 * 0.11).sin()).collect();
        let with_band = elevation_eq(&signal, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, 1.0);

        let nyq = sr as f64 / 2.0;
        let low = sosfilt(&butter_sos(1, 150.0 / nyq, BandType::Low), &signal);
        let bass: Vec<f64> = signal
            .iter()
            .zip(low.iter())
            .map(|(x, l)| x - l * (1.0 - 0.15))
            .collect();
        let high = sosfilt(&butter_sos(2, 3000.0 / nyq, BandType::High), &bass);
        for (i, (got, (x, hp))) in with_band.iter().zip(bass.iter().zip(high.iter())).enumerate() {
            assert_eq!(*got, x + hp * 0.5, "sample {i}");
        }
    }

    #[test]
    fn the_directional_band_peaks_at_its_centre_gain() {
        let sr = 48_000;
        let sos = directional_band_sos(8000.0, sr, 1.6);
        let nyq = sr as f64 / 2.0;

        assert!((sos_magnitude(&sos, 8000.0 / nyq) - 1.6).abs() < 1e-9);
        // +4.1 dB at centre must not read as a broadband brightness change.
        assert!((sos_magnitude(&sos, 1000.0 / nyq) - 1.0).abs() < 0.05);
        assert!((sos_magnitude(&sos, 20000.0 / nyq) - 1.0).abs() < 0.05);
    }

    #[test]
    fn band_gain_lifts_energy_at_the_centre_only() {
        let sr = 48_000;
        let tone = |hz: f64| -> Vec<f64> {
            (0..48_000)
                .map(|i| (2.0 * std::f64::consts::PI * hz * i as f64 / sr as f64).sin())
                .collect()
        };
        for (hz, want) in [(8000.0, 1.6), (1000.0, 1.0)] {
            let x = tone(hz);
            let flat = elevation_eq(&x, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, 1.0);
            let lifted = elevation_eq(&x, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, 1.6);
            let energy = |v: &[f64]| v[24_000..].iter().map(|s| s * s).sum::<f64>().sqrt();
            let ratio = energy(&lifted) / energy(&flat);
            assert!((ratio - want).abs() < 0.05, "{hz} Hz: {ratio} vs {want}");
        }
    }
}
