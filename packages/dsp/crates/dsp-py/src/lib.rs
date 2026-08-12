//! Python bindings for the shared DSP core. Every export stays a thin
//! adapter: validation, profile resolution, and logging stay in Python.

use pyo3::prelude::*;

#[pyfunction]
fn dsp_core_version() -> &'static str {
    upmixer_dsp_core::version()
}

#[pymodule]
fn upmixer_dsp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(dsp_core_version, m)?)?;
    Ok(())
}
