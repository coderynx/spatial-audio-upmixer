//! Ordered-array binding for the two-child stem cleanup processor.

use numpy::{ndarray::Array2, PyArray2, PyReadonlyArray2, ToPyArray};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

use upmixer_dsp_core::stem_cleanup::{
    StemCleanup as CoreStemCleanup, StemCleanupError, StemCleanupPolicy, StereoBlock, StereoBuffer,
};

#[pyclass]
struct StemCleanup {
    inner: CoreStemCleanup,
}

#[pymethods]
impl StemCleanup {
    #[new]
    #[pyo3(signature = (sample_rate, relative_energy_floor, relative_leakage_floor, coherence_floor, dominance_ratio, transfer_cap))]
    fn new(
        sample_rate: u32,
        relative_energy_floor: f64,
        relative_leakage_floor: f64,
        coherence_floor: f64,
        dominance_ratio: f64,
        transfer_cap: f64,
    ) -> PyResult<Self> {
        let policy = StemCleanupPolicy {
            relative_energy_floor,
            relative_leakage_floor,
            coherence_floor,
            dominance_ratio,
            transfer_cap,
        };
        CoreStemCleanup::new(sample_rate, policy)
            .map(|inner| Self { inner })
            .map_err(py_error)
    }

    #[getter]
    fn latency_samples(&self) -> usize {
        self.inner.latency_samples()
    }

    fn process<'py>(
        &mut self,
        py: Python<'py>,
        parent: PyReadonlyArray2<'py, f64>,
        child_a: PyReadonlyArray2<'py, f64>,
        child_b: PyReadonlyArray2<'py, f64>,
    ) -> PyResult<(Bound<'py, PyArray2<f64>>, Bound<'py, PyArray2<f64>>)> {
        let parent = stereo(parent)?;
        let child_a = stereo(child_a)?;
        let child_b = stereo(child_b)?;
        let (a, b) = py
            .detach(|| {
                self.inner.process(
                    StereoBlock {
                        left: &parent.0,
                        right: &parent.1,
                    },
                    StereoBlock {
                        left: &child_a.0,
                        right: &child_a.1,
                    },
                    StereoBlock {
                        left: &child_b.0,
                        right: &child_b.1,
                    },
                )
            })
            .map_err(py_error)?;
        Ok((to_array(py, a), to_array(py, b)))
    }

    fn flush<'py>(
        &mut self,
        py: Python<'py>,
    ) -> PyResult<(Bound<'py, PyArray2<f64>>, Bound<'py, PyArray2<f64>>)> {
        let (a, b) = py.detach(|| self.inner.flush()).map_err(py_error)?;
        Ok((to_array(py, a), to_array(py, b)))
    }
}

fn stereo(array: PyReadonlyArray2<'_, f64>) -> PyResult<(Vec<f64>, Vec<f64>)> {
    let view = array.as_array();
    if view.ncols() != 2 {
        return Err(PyValueError::new_err(
            "stem cleanup blocks must have shape (frames, 2)",
        ));
    }
    let mut left = Vec::with_capacity(view.nrows());
    let mut right = Vec::with_capacity(view.nrows());
    for row in view.rows() {
        left.push(row[0]);
        right.push(row[1]);
    }
    Ok((left, right))
}

fn to_array<'py>(py: Python<'py>, stereo: StereoBuffer) -> Bound<'py, PyArray2<f64>> {
    Array2::from_shape_fn((stereo.left.len(), 2), |(row, channel)| {
        if channel == 0 {
            stereo.left[row]
        } else {
            stereo.right[row]
        }
    })
    .to_pyarray(py)
}

fn py_error(error: StemCleanupError) -> PyErr {
    match error {
        StemCleanupError::AlreadyFlushed => PyRuntimeError::new_err(error.to_string()),
        _ => PyValueError::new_err(error.to_string()),
    }
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<StemCleanup>()?;
    Ok(())
}
