//! BS.1770 loudness and true-peak exports.

use numpy::{PyArray1, PyArray2, PyArrayMethods, PyReadonlyArray1, ToPyArray};
use pyo3::prelude::*;

use upmixer_dsp_core::loudness;

use crate::to_bed;

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
    py: Python<'_>,
    weights: Vec<f64>,
    channels: Vec<PyReadonlyArray1<f64>>,
    sample_rate: u32,
) -> f64 {
    let bed = to_bed(channels);
    py.detach(|| {
        let weighted: Vec<(f64, &[f64])> = weights
            .iter()
            .zip(bed.iter())
            .map(|(w, c)| (*w, c.as_slice()))
            .collect();
        loudness::measure_integrated_loudness(&weighted, sample_rate)
    })
}

#[pyfunction]
fn true_peak_dbtp(py: Python<'_>, channels: Vec<PyReadonlyArray1<f64>>) -> f64 {
    let bed = to_bed(channels);
    py.detach(|| {
        let refs: Vec<&[f64]> = bed.iter().map(|c| c.as_slice()).collect();
        loudness::measure_true_peak(&refs)
    })
}

#[pyfunction]
fn true_peak_per_channel(py: Python<'_>, channels: Vec<PyReadonlyArray1<f64>>) -> Vec<f64> {
    let bed = to_bed(channels);
    py.detach(|| {
        let refs: Vec<&[f64]> = bed.iter().map(|c| c.as_slice()).collect();
        loudness::measure_true_peak_per_channel(&refs)
    })
}

/// `(integrated_lkfs, lra_lu, max_momentary_lkfs, max_short_term_lkfs)`.
#[pyfunction]
fn loudness_stats(
    py: Python<'_>,
    weights: Vec<f64>,
    channels: Vec<PyReadonlyArray1<f64>>,
    sample_rate: u32,
) -> (f64, f64, f64, f64) {
    let bed = to_bed(channels);
    py.detach(|| {
        let weighted: Vec<(f64, &[f64])> = weights
            .iter()
            .zip(bed.iter())
            .map(|(w, c)| (*w, c.as_slice()))
            .collect();
        let s = loudness::measure_loudness_stats(&weighted, sample_rate);
        (
            s.integrated_lkfs,
            s.lra_lu,
            s.max_momentary_lkfs,
            s.max_short_term_lkfs,
        )
    })
}

/// BS.1770-5 Annex 2 order-48 four-phase true-peak interpolation FIR.
#[pyfunction]
fn true_peak_fir(py: Python<'_>) -> Bound<'_, PyArray1<f64>> {
    PyArray1::from_slice(py, &loudness::TRUE_PEAK_FIR_4X)
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(k_weighting_sos, m)?)?;
    m.add_function(wrap_pyfunction!(integrated_loudness, m)?)?;
    m.add_function(wrap_pyfunction!(true_peak_dbtp, m)?)?;
    m.add_function(wrap_pyfunction!(true_peak_per_channel, m)?)?;
    m.add_function(wrap_pyfunction!(loudness_stats, m)?)?;
    m.add_function(wrap_pyfunction!(true_peak_fir, m)?)?;
    Ok(())
}
