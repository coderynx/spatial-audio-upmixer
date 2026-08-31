//! Ambisonic, voicing, decode, and crosstalk exports.
//!
//! Filter sets are loaded (and resampled) by `packages/core`; only their taps
//! cross the boundary, flattened, since the core has no file I/O.

use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::prelude::*;

use upmixer_dsp_core::kernels::biquad::sosfilt;
use upmixer_dsp_core::kernels::butter::{butter_sos, linkwitz_riley_lowpass_sos, BandType};
use upmixer_dsp_core::spatial::{
    ambisonics::{self, DecodeFilterSet, HoaBus, N_ACN_CHANNELS},
    voicing::{self, VoicingParams},
    xtc::{self, XtcFilterSet},
};

use crate::to_bed;

#[pyfunction]
fn ambisonic_encode_gains(
    py: Python<'_>,
    azimuth_rad: f64,
    elevation_rad: f64,
) -> Bound<'_, PyArray1<f64>> {
    PyArray1::from_slice(py, &ambisonics::encode_gains(azimuth_rad, elevation_rad))
}

fn build_decode_filters(flat: &[f64], n_taps: usize) -> DecodeFilterSet {
    let taps = (0..N_ACN_CHANNELS)
        .map(|acn| {
            let base = acn * 2 * n_taps;
            [
                flat[base..base + n_taps].to_vec(),
                flat[base + n_taps..base + 2 * n_taps].to_vec(),
            ]
        })
        .collect();
    DecodeFilterSet { taps }
}

/// Decode an already-encoded 16-channel HOA bus to stereo.
#[pyfunction]
#[pyo3(signature = (hoa, decode_taps, n_taps))]
fn decode_hoa_to_binaural<'py>(
    py: Python<'py>,
    hoa: Vec<PyReadonlyArray1<'py, f64>>,
    decode_taps: PyReadonlyArray1<'py, f64>,
    n_taps: usize,
) -> (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>) {
    let channels = to_bed(hoa);
    let flat = decode_taps.as_array().to_vec();
    let (left, right) = py.detach(|| {
        let bus = HoaBus { channels };
        ambisonics::decode_to_binaural(&bus, &build_decode_filters(&flat, n_taps))
    });
    (PyArray1::from_vec(py, left), PyArray1::from_vec(py, right))
}

/// Encode every positional speaker feed and decode the resulting HOA bus.
///
/// `directions` is one `(azimuth_rad, elevation_rad)` per entry in
/// `channels`; `decode_taps` is the flattened `[acn][ear][tap]` bank.
#[pyfunction]
#[pyo3(signature = (channels, directions, decode_taps, n_taps))]
fn render_hoa_to_binaural<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    directions: Vec<(f64, f64)>,
    decode_taps: PyReadonlyArray1<'py, f64>,
    n_taps: usize,
) -> (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>) {
    let bed = to_bed(channels);
    let flat = decode_taps.as_array().to_vec();
    let n_samples = bed.first().map(|c| c.len()).unwrap_or(0);

    let (left, right) = py.detach(|| {
        let mut hoa = HoaBus::new(n_samples);
        for (signal, (azimuth, elevation)) in bed.iter().zip(directions.iter()) {
            hoa.add_source(signal, *azimuth, *elevation);
        }
        ambisonics::decode_to_binaural(&hoa, &build_decode_filters(&flat, n_taps))
    });
    (PyArray1::from_vec(py, left), PyArray1::from_vec(py, right))
}

#[pyfunction]
#[pyo3(signature = (left, right, sample_rate, crossfeed_amount, crossfeed_cutoff_hz,
                    bass_shelf_hz, bass_shelf_gain_db, air_shelf_hz, air_shelf_gain_db,
                    presence_hz, presence_gain_db, presence_q, stereo_widen))]
#[allow(clippy::too_many_arguments)]
fn apply_voicing<'py>(
    py: Python<'py>,
    left: PyReadonlyArray1<'py, f64>,
    right: PyReadonlyArray1<'py, f64>,
    sample_rate: u32,
    crossfeed_amount: f64,
    crossfeed_cutoff_hz: f64,
    bass_shelf_hz: f64,
    bass_shelf_gain_db: f64,
    air_shelf_hz: f64,
    air_shelf_gain_db: f64,
    presence_hz: f64,
    presence_gain_db: f64,
    presence_q: f64,
    stereo_widen: f64,
) -> (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>) {
    let l = left.as_array().to_vec();
    let r = right.as_array().to_vec();
    let params = VoicingParams {
        crossfeed_amount,
        crossfeed_cutoff_hz,
        bass_shelf_hz,
        bass_shelf_gain_db,
        air_shelf_hz,
        air_shelf_gain_db,
        presence_hz,
        presence_gain_db,
        presence_q,
        stereo_widen,
    };
    let (out_l, out_r) = py.detach(|| voicing::apply_voicing(&l, &r, sample_rate, &params));
    (PyArray1::from_vec(py, out_l), PyArray1::from_vec(py, out_r))
}

#[pyfunction]
#[pyo3(signature = (left, right, taps, n_taps))]
fn apply_xtc<'py>(
    py: Python<'py>,
    left: PyReadonlyArray1<'py, f64>,
    right: PyReadonlyArray1<'py, f64>,
    taps: PyReadonlyArray1<'py, f64>,
    n_taps: usize,
) -> (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>) {
    let l = left.as_array().to_vec();
    let r = right.as_array().to_vec();
    let flat = taps.as_array().to_vec();
    let (out_l, out_r) = py.detach(|| {
        let tap = |i: usize| flat[i * n_taps..(i + 1) * n_taps].to_vec();
        let filters = XtcFilterSet {
            taps: [[tap(0), tap(1)], [tap(2), tap(3)]],
        };
        xtc::apply_xtc(&l, &r, &filters)
    });
    (PyArray1::from_vec(py, out_l), PyArray1::from_vec(py, out_r))
}

/// Linkwitz-Riley low-pass — the LFE feed for binaural, transaural, and the
/// stem router's LFE bus. `order` must be even; see
/// `docs/standards/spatial_layouts_bs775_bs2051.md` § "LFE lowpass".
#[pyfunction]
fn lfe_lowpass<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f64>,
    sample_rate: u32,
    cutoff_hz: f64,
    order: usize,
) -> Bound<'py, PyArray1<f64>> {
    let input = signal.as_array().to_vec();
    let out = py.detach(|| {
        let sos = linkwitz_riley_lowpass_sos(order, cutoff_hz / (sample_rate as f64 / 2.0));
        sosfilt(&sos, &input)
    });
    PyArray1::from_vec(py, out)
}

/// Butterworth high-pass — the pre-Haas surround send.
#[pyfunction]
fn highpass<'py>(
    py: Python<'py>,
    signal: PyReadonlyArray1<'py, f64>,
    sample_rate: u32,
    cutoff_hz: f64,
    order: usize,
) -> Bound<'py, PyArray1<f64>> {
    let input = signal.as_array().to_vec();
    let out = py.detach(|| {
        let sos = butter_sos(
            order,
            cutoff_hz / (sample_rate as f64 / 2.0),
            BandType::High,
        );
        sosfilt(&sos, &input)
    });
    PyArray1::from_vec(py, out)
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ambisonic_encode_gains, m)?)?;
    m.add_function(wrap_pyfunction!(decode_hoa_to_binaural, m)?)?;
    m.add_function(wrap_pyfunction!(render_hoa_to_binaural, m)?)?;
    m.add_function(wrap_pyfunction!(apply_voicing, m)?)?;
    m.add_function(wrap_pyfunction!(apply_xtc, m)?)?;
    m.add_function(wrap_pyfunction!(lfe_lowpass, m)?)?;
    m.add_function(wrap_pyfunction!(highpass, m)?)?;
    Ok(())
}
