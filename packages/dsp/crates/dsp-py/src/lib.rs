//! Python bindings for the shared DSP core.
//!
//! Every export is a thin adapter: validation, profile resolution, logging,
//! and channel naming stay in `packages/core`. Channel dictionaries cross the
//! boundary as ordered lists so the core never needs the layout tables.

use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::prelude::*;

mod loudness;
mod mastering;
mod reference;
mod spatial;

pub(crate) type Bed = Vec<Vec<f64>>;

pub(crate) fn to_bed(channels: Vec<PyReadonlyArray1<f64>>) -> Bed {
    channels.iter().map(|c| c.as_array().to_vec()).collect()
}

pub(crate) fn from_bed<'py>(py: Python<'py>, bed: Bed) -> Vec<Bound<'py, PyArray1<f64>>> {
    bed.into_iter().map(|c| PyArray1::from_vec(py, c)).collect()
}

#[pyfunction]
fn dsp_core_version() -> &'static str {
    upmixer_dsp_core::version()
}

#[pymodule]
fn upmixer_dsp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(dsp_core_version, m)?)?;
    loudness::register(m)?;
    mastering::register(m)?;
    reference::register(m)?;
    spatial::register(m)?;
    Ok(())
}
