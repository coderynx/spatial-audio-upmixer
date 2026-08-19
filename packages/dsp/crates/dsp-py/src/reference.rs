//! Reference-matching exports.
//!
//! The analysis constants stay in
//! `packages/core/src/mastering/match_reference/curve.py`, which owns them,
//! and arrive here as parameters.

use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::prelude::*;

use upmixer_dsp_core::match_reference::{curve, spectrum};

use crate::to_bed;

#[allow(clippy::too_many_arguments)]
fn params(
    min_freq_hz: f64,
    max_freq_hz: f64,
    grid_step_oct: f64,
    norm_low_hz: f64,
    norm_high_hz: f64,
    confidence_floor_db: f64,
    taper_low: (f64, f64),
    taper_high: (f64, f64),
) -> curve::CurveParams {
    curve::CurveParams {
        min_freq_hz,
        max_freq_hz,
        grid_step_oct,
        norm_low_hz,
        norm_high_hz,
        confidence_floor_db,
        taper: curve::TaperBand {
            low_start: taper_low.0,
            low_end: taper_low.1,
            high_start: taper_high.0,
            high_end: taper_high.1,
        },
    }
}

#[pyfunction]
#[pyo3(signature = (channels, weights, sample_rate, n_fft, absolute_gate_db,
                    relative_gate_offset_db, epsilon))]
fn weighted_power_spectrum<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    weights: Vec<f64>,
    sample_rate: u32,
    n_fft: usize,
    absolute_gate_db: f64,
    relative_gate_offset_db: f64,
    epsilon: f64,
) -> (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>) {
    let bed = to_bed(channels);
    let gate = spectrum::GateParams {
        absolute_db: absolute_gate_db,
        relative_offset_db: relative_gate_offset_db,
        epsilon,
    };
    let (freqs, power) = py.detach(|| {
        let refs: Vec<&[f64]> = bed.iter().map(|c| c.as_slice()).collect();
        spectrum::weighted_power_spectrum(&refs, &weights, sample_rate, n_fft, &gate)
    });
    (PyArray1::from_vec(py, freqs), PyArray1::from_vec(py, power))
}

#[pyfunction]
fn log_grid(
    py: Python<'_>,
    high_hz: f64,
    min_freq_hz: f64,
    step_oct: f64,
) -> Bound<'_, PyArray1<f64>> {
    PyArray1::from_vec(py, curve::log_grid(high_hz, min_freq_hz, step_oct))
}

#[pyfunction]
fn smooth_log_grid<'py>(
    py: Python<'py>,
    values: PyReadonlyArray1<'py, f64>,
    sigma_oct: f64,
    step_oct: f64,
) -> Bound<'py, PyArray1<f64>> {
    let out = curve::smooth_log_grid(values.as_array().to_vec().as_slice(), sigma_oct, step_oct);
    PyArray1::from_vec(py, out)
}

#[pyfunction]
fn confidence_taper<'py>(
    py: Python<'py>,
    correction_db: PyReadonlyArray1<'py, f64>,
    ref_power_db: PyReadonlyArray1<'py, f64>,
    floor_db: f64,
) -> Bound<'py, PyArray1<f64>> {
    let out = curve::confidence_taper(
        correction_db.as_array().to_vec().as_slice(),
        ref_power_db.as_array().to_vec().as_slice(),
        floor_db,
    );
    PyArray1::from_vec(py, out)
}

#[pyfunction]
fn band_edge_taper<'py>(
    py: Python<'py>,
    correction_db: PyReadonlyArray1<'py, f64>,
    freqs: PyReadonlyArray1<'py, f64>,
    taper_low: (f64, f64),
    taper_high: (f64, f64),
) -> Bound<'py, PyArray1<f64>> {
    let band = curve::TaperBand {
        low_start: taper_low.0,
        low_end: taper_low.1,
        high_start: taper_high.0,
        high_end: taper_high.1,
    };
    let out = curve::band_edge_taper(
        correction_db.as_array().to_vec().as_slice(),
        freqs.as_array().to_vec().as_slice(),
        &band,
    );
    PyArray1::from_vec(py, out)
}

#[pyfunction]
fn soft_clamp<'py>(
    py: Python<'py>,
    db: PyReadonlyArray1<'py, f64>,
    limit_db: f64,
    knee_db: f64,
) -> Bound<'py, PyArray1<f64>> {
    let out = curve::soft_clamp(db.as_array().to_vec().as_slice(), limit_db, knee_db);
    PyArray1::from_vec(py, out)
}

#[pyfunction]
#[pyo3(signature = (freqs_t, power_t, freqs_r, power_r, sample_rate, min_freq_hz, max_freq_hz,
                    grid_step_oct, norm_low_hz, norm_high_hz,
                    confidence_floor_db, taper_low, taper_high))]
#[allow(clippy::too_many_arguments)]
fn correction_curve<'py>(
    freqs_t: PyReadonlyArray1<'py, f64>,
    power_t: PyReadonlyArray1<'py, f64>,
    freqs_r: PyReadonlyArray1<'py, f64>,
    power_r: PyReadonlyArray1<'py, f64>,
    sample_rate: u32,
    min_freq_hz: f64,
    max_freq_hz: f64,
    grid_step_oct: f64,
    norm_low_hz: f64,
    norm_high_hz: f64,
    confidence_floor_db: f64,
    taper_low: (f64, f64),
    taper_high: (f64, f64),
) -> Vec<(f64, f64)> {
    curve::correction_curve(
        freqs_t.as_array().to_vec().as_slice(),
        power_t.as_array().to_vec().as_slice(),
        freqs_r.as_array().to_vec().as_slice(),
        power_r.as_array().to_vec().as_slice(),
        sample_rate,
        &params(
            min_freq_hz,
            max_freq_hz,
            grid_step_oct,
            norm_low_hz,
            norm_high_hz,
            confidence_floor_db,
            taper_low,
            taper_high,
        ),
    )
}

#[pyfunction]
#[pyo3(signature = (freqs, correction_db, strength, max_correction_db, clamp_knee_db,
                    smooth_oct, grid_step_oct, low_hz, high_hz, mask_ease_oct,
                    bass_clamp_hz, bass_clamp_db))]
#[allow(clippy::too_many_arguments)]
fn realize_curve<'py>(
    py: Python<'py>,
    freqs: PyReadonlyArray1<'py, f64>,
    correction_db: PyReadonlyArray1<'py, f64>,
    strength: f64,
    max_correction_db: f64,
    clamp_knee_db: f64,
    smooth_oct: f64,
    grid_step_oct: f64,
    low_hz: f64,
    high_hz: f64,
    mask_ease_oct: f64,
    bass_clamp_hz: f64,
    bass_clamp_db: f64,
) -> Bound<'py, PyArray1<f64>> {
    let out = curve::realize_curve(
        freqs.as_array().to_vec().as_slice(),
        correction_db.as_array().to_vec().as_slice(),
        &curve::RealizeParams {
            strength,
            max_correction_db,
            clamp_knee_db,
            smooth_oct,
            grid_step_oct,
            low_hz,
            high_hz,
            mask_ease_oct,
            bass_clamp_hz,
            bass_clamp_db,
        },
    );
    PyArray1::from_vec(py, out)
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(weighted_power_spectrum, m)?)?;
    m.add_function(wrap_pyfunction!(log_grid, m)?)?;
    m.add_function(wrap_pyfunction!(smooth_log_grid, m)?)?;
    m.add_function(wrap_pyfunction!(confidence_taper, m)?)?;
    m.add_function(wrap_pyfunction!(band_edge_taper, m)?)?;
    m.add_function(wrap_pyfunction!(soft_clamp, m)?)?;
    m.add_function(wrap_pyfunction!(correction_curve, m)?)?;
    m.add_function(wrap_pyfunction!(realize_curve, m)?)?;
    Ok(())
}
