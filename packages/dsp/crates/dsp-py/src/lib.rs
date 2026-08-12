//! Python bindings for the shared DSP core.
//!
//! Every export is a thin adapter: validation, profile resolution, logging,
//! and channel naming stay in `packages/core`. Channel dictionaries cross the
//! boundary as ordered lists so the core never needs the layout tables.

use numpy::{PyArray1, PyArray2, PyArrayMethods, PyReadonlyArray1, ToPyArray};
use pyo3::prelude::*;

use upmixer_dsp_core::loudness;
use upmixer_dsp_core::mastering::{bass, compressor, eq, limiter};
use upmixer_dsp_core::routing::sends;
use upmixer_dsp_core::spatial::downmix::{self, DownmixRole};

type Bed = Vec<Vec<f64>>;

fn to_bed(channels: Vec<PyReadonlyArray1<f64>>) -> Bed {
    channels.iter().map(|c| c.as_array().to_vec()).collect()
}

fn from_bed<'py>(py: Python<'py>, bed: Bed) -> Vec<Bound<'py, PyArray1<f64>>> {
    bed.into_iter().map(|c| PyArray1::from_vec(py, c)).collect()
}

#[pyfunction]
fn dsp_core_version() -> &'static str {
    upmixer_dsp_core::version()
}

#[pyfunction]
fn k_weighting_sos(py: Python<'_>, sample_rate: u32) -> Bound<'_, PyArray2<f64>> {
    let sos = loudness::k_weighting_sos(sample_rate);
    let flat: Vec<f64> = sos.iter().flat_map(|r| r.iter().copied()).collect();
    let rows = sos.len();
    flat.to_pyarray(py)
        .reshape([rows, 6])
        .expect("SOS rows are six wide")
}

#[pyfunction]
fn integrated_loudness(
    weights: Vec<f64>,
    channels: Vec<PyReadonlyArray1<f64>>,
    sample_rate: u32,
) -> f64 {
    let bed = to_bed(channels);
    let weighted: Vec<(f64, &[f64])> = weights
        .iter()
        .zip(bed.iter())
        .map(|(w, c)| (*w, c.as_slice()))
        .collect();
    loudness::measure_integrated_loudness(&weighted, sample_rate)
}

#[pyfunction]
fn true_peak_dbtp(channels: Vec<PyReadonlyArray1<f64>>) -> f64 {
    let bed = to_bed(channels);
    let refs: Vec<&[f64]> = bed.iter().map(|c| c.as_slice()).collect();
    loudness::measure_true_peak(&refs)
}

#[pyfunction]
#[pyo3(signature = (breakpoints, sample_rate, n_taps))]
fn build_eq_fir(
    py: Python<'_>,
    breakpoints: Vec<(f64, f64)>,
    sample_rate: u32,
    n_taps: usize,
) -> Bound<'_, PyArray1<f64>> {
    PyArray1::from_vec(py, eq::build_fir(&breakpoints, sample_rate, n_taps))
}

#[pyfunction]
fn apply_fir<'py>(
    py: Python<'py>,
    channel: PyReadonlyArray1<'py, f64>,
    ir: PyReadonlyArray1<'py, f64>,
    strength: f64,
) -> Bound<'py, PyArray1<f64>> {
    let out = eq::apply_fir(
        channel.as_array().to_vec().as_slice(),
        ir.as_array().to_vec().as_slice(),
        strength,
    );
    PyArray1::from_vec(py, out)
}

#[pyfunction]
#[pyo3(signature = (channels, lfe_index, sample_rate, threshold_db, ratio, attack_ms,
                    release_ms, knee_db, makeup_db))]
#[allow(clippy::too_many_arguments)]
fn bus_compress<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    lfe_index: Option<usize>,
    sample_rate: u32,
    threshold_db: f64,
    ratio: f64,
    attack_ms: f64,
    release_ms: f64,
    knee_db: f64,
    makeup_db: f64,
) -> (Vec<Bound<'py, PyArray1<f64>>>, f64, f64) {
    let mut bed = to_bed(channels);
    let info = compressor::bus_compress(
        &mut bed,
        lfe_index,
        sample_rate,
        &compressor::CompParams {
            threshold_db,
            ratio,
            attack_ms,
            release_ms,
            knee_db,
            makeup_db,
        },
    );
    (from_bed(py, bed), info.max_gr_db, info.avg_gr_db)
}

#[pyfunction]
#[pyo3(signature = (channels, lfe_index, stereo_pairs, sample_rate, sub_gain_db, mid_gain_db,
                    mono_cutoff_hz, excite, lfe_gain_db, sub_cutoff_hz, mid_cutoff_hz,
                    excite_blend, excite_drive))]
#[allow(clippy::too_many_arguments)]
fn bass_control<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    lfe_index: Option<usize>,
    stereo_pairs: Vec<(usize, usize)>,
    sample_rate: u32,
    sub_gain_db: f64,
    mid_gain_db: f64,
    mono_cutoff_hz: Option<f64>,
    excite: bool,
    lfe_gain_db: f64,
    sub_cutoff_hz: f64,
    mid_cutoff_hz: f64,
    excite_blend: f64,
    excite_drive: f64,
) -> Vec<Bound<'py, PyArray1<f64>>> {
    let mut bed = to_bed(channels);
    bass::bass_control(
        &mut bed,
        lfe_index,
        &stereo_pairs,
        sample_rate,
        &bass::BassParams {
            sub_gain_db,
            mid_gain_db,
            mono_cutoff_hz,
            excite,
            lfe_gain_db,
            sub_cutoff_hz,
            mid_cutoff_hz,
            excite_blend,
            excite_drive,
        },
    );
    from_bed(py, bed)
}

#[pyfunction]
fn forward_window_min<'py>(
    py: Python<'py>,
    values: PyReadonlyArray1<'py, f64>,
    window: usize,
) -> Bound<'py, PyArray1<f64>> {
    let out = limiter::forward_window_min(values.as_array().to_vec().as_slice(), window);
    PyArray1::from_vec(py, out)
}

#[pyfunction]
#[pyo3(signature = (channels, sample_rate, ceiling_dbtp, lookahead_ms, release_ms,
                    safety_margin_db))]
fn lookahead_limit<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    sample_rate: u32,
    ceiling_dbtp: f64,
    lookahead_ms: f64,
    release_ms: f64,
    safety_margin_db: f64,
) -> (Vec<Bound<'py, PyArray1<f64>>>, f64) {
    let mut bed = to_bed(channels);
    let gr = limiter::lookahead_limit(
        &mut bed,
        sample_rate,
        &limiter::LimiterParams {
            ceiling_dbtp,
            lookahead_ms,
            release_ms,
            safety_margin_db,
        },
    );
    (from_bed(py, bed), gr)
}

/// Downmix roles cross the boundary as the names `packages/core` already
/// uses for its channels; anything else is not part of a BS.775 downmix.
fn parse_role(name: &str) -> Option<DownmixRole> {
    Some(match name {
        "FL" => DownmixRole::Fl,
        "FR" => DownmixRole::Fr,
        "C" => DownmixRole::C,
        "SL" => DownmixRole::Sl,
        "SR" => DownmixRole::Sr,
        "BL" => DownmixRole::Bl,
        "BR" => DownmixRole::Br,
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
) -> (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>) {
    let bed = to_bed(channels);
    let inputs = downmix_inputs(&names, &bed);
    let (left, right) = downmix::itu_downmix_stereo(&inputs, surround_coeff);
    (PyArray1::from_vec(py, left), PyArray1::from_vec(py, right))
}

#[pyfunction]
fn itu_downmix_mono<'py>(
    py: Python<'py>,
    names: Vec<String>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    surround_coeff: f64,
) -> Bound<'py, PyArray1<f64>> {
    let bed = to_bed(channels);
    let inputs = downmix_inputs(&names, &bed);
    PyArray1::from_vec(py, downmix::itu_downmix_mono(&inputs, surround_coeff))
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

#[pyfunction]
fn haas_decorrelate<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f64>,
    delay_samples: usize,
) -> Bound<'py, PyArray1<f64>> {
    let out = sends::haas_decorrelate(signal.as_array().to_vec().as_slice(), delay_samples);
    PyArray1::from_vec(py, out)
}

#[pyfunction]
fn diffuse_send<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f64>,
    sample_rate: u32,
    delay_ms: f64,
    blend: f64,
) -> Bound<'py, PyArray1<f64>> {
    let out = sends::diffuse_send(
        signal.as_array().to_vec().as_slice(),
        sample_rate,
        delay_ms,
        blend,
    );
    PyArray1::from_vec(py, out)
}

#[pyfunction]
#[pyo3(signature = (signal, sample_rate, low_rolloff_hz, low_rolloff_gain, high_shelf_hz,
                    high_shelf_gain))]
fn elevation_eq<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f64>,
    sample_rate: u32,
    low_rolloff_hz: f64,
    low_rolloff_gain: f64,
    high_shelf_hz: f64,
    high_shelf_gain: f64,
) -> Bound<'py, PyArray1<f64>> {
    let out = sends::elevation_eq(
        signal.as_array().to_vec().as_slice(),
        sample_rate,
        low_rolloff_hz,
        low_rolloff_gain,
        high_shelf_hz,
        high_shelf_gain,
    );
    PyArray1::from_vec(py, out)
}

#[pymodule]
fn upmixer_dsp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(dsp_core_version, m)?)?;
    m.add_function(wrap_pyfunction!(k_weighting_sos, m)?)?;
    m.add_function(wrap_pyfunction!(integrated_loudness, m)?)?;
    m.add_function(wrap_pyfunction!(true_peak_dbtp, m)?)?;
    m.add_function(wrap_pyfunction!(build_eq_fir, m)?)?;
    m.add_function(wrap_pyfunction!(apply_fir, m)?)?;
    m.add_function(wrap_pyfunction!(bus_compress, m)?)?;
    m.add_function(wrap_pyfunction!(bass_control, m)?)?;
    m.add_function(wrap_pyfunction!(forward_window_min, m)?)?;
    m.add_function(wrap_pyfunction!(lookahead_limit, m)?)?;
    m.add_function(wrap_pyfunction!(itu_downmix_stereo, m)?)?;
    m.add_function(wrap_pyfunction!(itu_downmix_mono, m)?)?;
    m.add_function(wrap_pyfunction!(soft_limit, m)?)?;
    m.add_function(wrap_pyfunction!(haas_decorrelate, m)?)?;
    m.add_function(wrap_pyfunction!(diffuse_send, m)?)?;
    m.add_function(wrap_pyfunction!(elevation_eq, m)?)?;
    Ok(())
}
