//! Downmix, send shaping, and soft-limit exports.

use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::prelude::*;

use upmixer_dsp_core::routing::decorrelate;
use upmixer_dsp_core::routing::sends;
use upmixer_dsp_core::spatial::downmix::{self, DownmixRole};

use crate::to_bed;

/// Downmix roles cross the boundary as the channel names `packages/core`
/// already uses; anything else (LFE) has no downmix contribution.
fn parse_role(name: &str) -> Option<DownmixRole> {
    Some(match name {
        "FL" => DownmixRole::Fl,
        "FR" => DownmixRole::Fr,
        "C" => DownmixRole::C,
        "SL" => DownmixRole::Sl,
        "SR" => DownmixRole::Sr,
        "BL" => DownmixRole::Bl,
        "BR" => DownmixRole::Br,
        "TFL" => DownmixRole::Tfl,
        "TFR" => DownmixRole::Tfr,
        "TBL" => DownmixRole::Tbl,
        "TBR" => DownmixRole::Tbr,
        _ => return None,
    })
}

fn downmix_inputs<'a>(names: &[String], bed: &'a [Vec<f64>]) -> Vec<(DownmixRole, &'a [f64])> {
    names
        .iter()
        .zip(bed.iter())
        .filter_map(|(name, samples)| parse_role(name).map(|role| (role, samples.as_slice())))
        .collect()
}

#[pyfunction]
fn itu_downmix_stereo<'py>(
    py: Python<'py>,
    names: Vec<String>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    surround_coeff: f64,
    height_coeff: f64,
) -> (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>) {
    let bed = to_bed(channels);
    let inputs = downmix_inputs(&names, &bed);
    let (left, right) = downmix::itu_downmix_stereo(&inputs, surround_coeff, height_coeff);
    (PyArray1::from_vec(py, left), PyArray1::from_vec(py, right))
}

#[pyfunction]
fn itu_downmix_mono<'py>(
    py: Python<'py>,
    names: Vec<String>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    surround_coeff: f64,
    height_coeff: f64,
) -> Bound<'py, PyArray1<f64>> {
    let bed = to_bed(channels);
    let inputs = downmix_inputs(&names, &bed);
    PyArray1::from_vec(py, downmix::itu_downmix_mono(&inputs, surround_coeff, height_coeff))
}

#[pyfunction]
fn soft_limit<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f64>,
    threshold: f64,
) -> Bound<'py, PyArray1<f64>> {
    let mut out = signal.as_array().to_vec();
    downmix::soft_limit(&mut out, threshold);
    PyArray1::from_vec(py, out)
}

/// One side of the velvet-noise decorrelator pair, applied to `signal`.
///
/// `side` is `"left"` or `"right"`; both sides of a channel pair must come
/// from the same `seed` for the fold-down property to hold.
#[pyfunction]
#[pyo3(signature = (signal, sample_rate, side, length_ms, taps, seed, wet))]
fn velvet_pair_send<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f64>,
    sample_rate: u32,
    side: &str,
    length_ms: f64,
    taps: usize,
    seed: u64,
    wet: f64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let (left, right) = decorrelate::velvet_pair(sample_rate, length_ms, taps, seed, wet);
    let fir = match side {
        "left" => left,
        "right" => right,
        other => {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "side must be 'left' or 'right', got {other:?}"
            )))
        }
    };
    Ok(PyArray1::from_vec(py, fir.process(signal.as_array().to_vec().as_slice())))
}

#[pyfunction]
#[pyo3(signature = (signal, sample_rate, low_rolloff_hz, low_rolloff_gain, high_shelf_hz,
                    high_shelf_gain, directional_band_hz, directional_band_gain))]
#[allow(clippy::too_many_arguments)]
fn elevation_eq<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f64>,
    sample_rate: u32,
    low_rolloff_hz: f64,
    low_rolloff_gain: f64,
    high_shelf_hz: f64,
    high_shelf_gain: f64,
    directional_band_hz: f64,
    directional_band_gain: f64,
) -> Bound<'py, PyArray1<f64>> {
    let out = sends::elevation_eq(
        signal.as_array().to_vec().as_slice(),
        sample_rate,
        low_rolloff_hz,
        low_rolloff_gain,
        high_shelf_hz,
        high_shelf_gain,
        directional_band_hz,
        directional_band_gain,
    );
    PyArray1::from_vec(py, out)
}

/// Magnitude response of the whole `elevation_eq` chain at each frequency, so
/// the STFT height mask and the time-domain send share one filter design.
#[pyfunction]
#[pyo3(signature = (freqs_hz, sample_rate, low_rolloff_hz, low_rolloff_gain, high_shelf_hz,
                    high_shelf_gain, directional_band_hz, directional_band_gain))]
#[allow(clippy::too_many_arguments)]
fn elevation_response<'py>(
    py: Python<'py>,
    freqs_hz: PyReadonlyArray1<'py, f64>,
    sample_rate: u32,
    low_rolloff_hz: f64,
    low_rolloff_gain: f64,
    high_shelf_hz: f64,
    high_shelf_gain: f64,
    directional_band_hz: f64,
    directional_band_gain: f64,
) -> Bound<'py, PyArray1<f64>> {
    let out = sends::elevation_response(
        freqs_hz.as_array().to_vec().as_slice(),
        sample_rate,
        low_rolloff_hz,
        low_rolloff_gain,
        high_shelf_hz,
        high_shelf_gain,
        directional_band_hz,
        directional_band_gain,
    );
    PyArray1::from_vec(py, out)
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(itu_downmix_stereo, m)?)?;
    m.add_function(wrap_pyfunction!(itu_downmix_mono, m)?)?;
    m.add_function(wrap_pyfunction!(soft_limit, m)?)?;
    m.add_function(wrap_pyfunction!(velvet_pair_send, m)?)?;
    m.add_function(wrap_pyfunction!(elevation_eq, m)?)?;
    m.add_function(wrap_pyfunction!(elevation_response, m)?)?;
    m.add("VELVET_LENGTH_MS", decorrelate::VELVET_LENGTH_MS)?;
    m.add("VELVET_TAPS_PER_SIDE", decorrelate::VELVET_TAPS_PER_SIDE)?;
    m.add("VELVET_SEED", decorrelate::VELVET_SEED)?;
    m.add("VELVET_SEED_HEIGHT", decorrelate::VELVET_SEED_HEIGHT)?;
    m.add("VELVET_WET", decorrelate::VELVET_WET)?;
    Ok(())
}
