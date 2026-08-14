//! Mastering-bus stage exports.

use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::prelude::*;

use upmixer_dsp_core::mastering::{bass, compressor, eq, limiter};

use crate::{from_bed, to_bed};

#[pyfunction]
#[pyo3(signature = (breakpoints, sample_rate, n_taps))]
fn build_eq_fir(
    py: Python<'_>,
    breakpoints: Vec<(f64, f64)>,
    sample_rate: u32,
    n_taps: usize,
) -> Bound<'_, PyArray1<f64>> {
    let taps = py.detach(|| eq::build_fir(&breakpoints, sample_rate, n_taps));
    PyArray1::from_vec(py, taps)
}

#[pyfunction]
fn apply_fir<'py>(
    py: Python<'py>,
    channel: PyReadonlyArray1<'py, f64>,
    ir: PyReadonlyArray1<'py, f64>,
    strength: f64,
) -> Bound<'py, PyArray1<f64>> {
    let channel = channel.as_array().to_vec();
    let ir = ir.as_array().to_vec();
    let out = py.detach(|| eq::apply_fir(&channel, &ir, strength));
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
    let info = py.detach(|| compressor::bus_compress(
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
    ));
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
    py.detach(|| bass::bass_control(
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
    ));
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
    let gr = py.detach(|| limiter::lookahead_limit(
        &mut bed,
        sample_rate,
        &limiter::LimiterParams {
            ceiling_dbtp,
            lookahead_ms,
            release_ms,
            safety_margin_db,
        },
    ));
    (from_bed(py, bed), gr)
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(build_eq_fir, m)?)?;
    m.add_function(wrap_pyfunction!(apply_fir, m)?)?;
    m.add_function(wrap_pyfunction!(bus_compress, m)?)?;
    m.add_function(wrap_pyfunction!(bass_control, m)?)?;
    m.add_function(wrap_pyfunction!(forward_window_min, m)?)?;
    m.add_function(wrap_pyfunction!(lookahead_limit, m)?)?;
    Ok(())
}
