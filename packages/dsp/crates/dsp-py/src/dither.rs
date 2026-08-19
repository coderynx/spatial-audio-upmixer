//! Export-tail quantizer export.

use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use upmixer_dsp_core::dither::{channel_seed, quantize, DitherMode};

use crate::{from_bed, to_bed};

/// Quantize each channel onto the `bits`-deep PCM lattice, one independent
/// dither stream per channel.
#[pyfunction]
#[pyo3(signature = (channels, bits, mode, seed))]
fn quantize_pcm<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    bits: u32,
    mode: &str,
    seed: u64,
) -> PyResult<Vec<Bound<'py, PyArray1<f64>>>> {
    let mode = DitherMode::parse(mode)
        .ok_or_else(|| PyValueError::new_err(format!("Unknown dither mode '{mode}'")))?;
    let mut bed = to_bed(channels);
    py.detach(|| {
        for (index, channel) in bed.iter_mut().enumerate() {
            quantize(channel, bits, mode, channel_seed(seed, index));
        }
    });
    Ok(from_bed(py, bed))
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(quantize_pcm, m)?)?;
    Ok(())
}
