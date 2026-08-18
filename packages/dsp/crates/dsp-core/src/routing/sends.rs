//! Shaping applied to a source before it reaches a height speaker: the
//! elevation cue EQ. Decorrelation itself lives in `routing::decorrelate`.

use crate::kernels::biquad::{peaking_sos, sos_cascade_response, sos_magnitude, sosfilt};
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

/// Magnitude response of the whole `elevation_eq` chain at each frequency in
/// Hz, from the same section designs, so an STFT mask can voice heights the
/// way the time-domain send does without approximating it.
///
/// Magnitude only: the sections are minimum-phase, a per-bin mask is
/// zero-phase, so magnitude is the agreement that is achievable.
#[allow(clippy::too_many_arguments)]
pub fn elevation_response(
    freqs_hz: &[f64],
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
    let sos_hp = butter_sos(2, high_shelf_hz / nyq, BandType::High);
    let band = directional_band_sos(directional_band_hz, sample_rate, directional_band_gain);

    freqs_hz
        .iter()
        .map(|f| {
            let wn = (f / nyq).min(1.0);
            let bass = 1.0 - sos_cascade_response(&sos_lp, wn) * (1.0 - low_rolloff_gain);
            let shelf = 1.0 + sos_cascade_response(&sos_hp, wn) * (high_shelf_gain - 1.0);
            let shaped = (bass * shelf).norm();
            if directional_band_gain == 1.0 {
                shaped
            } else {
                shaped * sos_magnitude(&band, wn)
            }
        })
        .collect()
}
