//! Mastering-bus stage exports.

use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::prelude::*;

use upmixer_dsp_core::mastering::{bass, clip, compressor, dyneq, eq, head, limiter};

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
                    release_ms, knee_db, makeup_db, sidechain_hpf_hz))]
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
    sidechain_hpf_hz: Option<f64>,
) -> (Vec<Bound<'py, PyArray1<f64>>>, f64, f64) {
    let mut bed = to_bed(channels);
    let info = py.detach(|| {
        compressor::bus_compress(
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
                sidechain_hpf_hz,
            },
        )
    });
    (from_bed(py, bed), info.max_gr_db, info.avg_gr_db)
}

#[pyfunction]
#[pyo3(signature = (channels, lfe_index, detector_channels, detector_lfe_index, sample_rate,
                    threshold_db, ratio, attack_ms, release_ms, knee_db, makeup_db,
                    sidechain_hpf_hz))]
#[allow(clippy::too_many_arguments)]
fn bus_compress_linked<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    lfe_index: Option<usize>,
    detector_channels: Vec<PyReadonlyArray1<'py, f64>>,
    detector_lfe_index: Option<usize>,
    sample_rate: u32,
    threshold_db: f64,
    ratio: f64,
    attack_ms: f64,
    release_ms: f64,
    knee_db: f64,
    makeup_db: f64,
    sidechain_hpf_hz: Option<f64>,
) -> (Vec<Bound<'py, PyArray1<f64>>>, f64, f64) {
    let mut targets = to_bed(channels);
    let detector = to_bed(detector_channels);
    let info = py.detach(|| {
        compressor::bus_compress_linked(
            &mut targets,
            lfe_index,
            &detector,
            detector_lfe_index,
            sample_rate,
            &compressor::CompParams {
                threshold_db,
                ratio,
                attack_ms,
                release_ms,
                knee_db,
                makeup_db,
                sidechain_hpf_hz,
            },
        )
    });
    (from_bed(py, targets), info.max_gr_db, info.avg_gr_db)
}

#[pyfunction]
#[pyo3(signature = (channels, lfe_index, spatial_channels, lf_targets, sample_rate, sub_gain_db, mid_gain_db,
                    unify_hz, punch, excite, lfe_gain_db, sub_cutoff_hz, mid_cutoff_hz,
                    excite_blend, excite_drive, punch_fast_ms, punch_slow_ms, punch_max_db,
                    decorrelate, decorr_low_hz, decorr_high_hz, decorr_sections,
                    decorr_max_delay_ms, decorr_fast_ms, decorr_slow_ms))]
#[allow(clippy::too_many_arguments)]
fn bass_control<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    lfe_index: Option<usize>,
    spatial_channels: usize,
    lf_targets: Vec<(usize, f64)>,
    sample_rate: u32,
    sub_gain_db: f64,
    mid_gain_db: f64,
    unify_hz: Option<f64>,
    punch: f64,
    excite: bool,
    lfe_gain_db: f64,
    sub_cutoff_hz: f64,
    mid_cutoff_hz: f64,
    excite_blend: f64,
    excite_drive: f64,
    punch_fast_ms: f64,
    punch_slow_ms: f64,
    punch_max_db: f64,
    decorrelate: f64,
    decorr_low_hz: f64,
    decorr_high_hz: f64,
    decorr_sections: usize,
    decorr_max_delay_ms: f64,
    decorr_fast_ms: f64,
    decorr_slow_ms: f64,
) -> Vec<Bound<'py, PyArray1<f64>>> {
    let mut bed = to_bed(channels);
    py.detach(|| {
        bass::bass_control_sources(
            &mut bed,
            lfe_index,
            spatial_channels,
            &lf_targets,
            sample_rate,
            &bass::BassParams {
                sub_gain_db,
                mid_gain_db,
                unify_hz,
                punch,
                excite,
                lfe_gain_db,
                sub_cutoff_hz,
                mid_cutoff_hz,
                excite_blend,
                excite_drive,
                punch_fast_ms,
                punch_slow_ms,
                punch_max_db,
                decorrelate,
                decorr_low_hz,
                decorr_high_hz,
                decorr_sections,
                decorr_max_delay_ms,
                decorr_fast_ms,
                decorr_slow_ms,
            },
        )
    });
    from_bed(py, bed)
}

#[pyfunction]
#[pyo3(signature = (channels, lfe_index, sample_rate, cutoff_hz))]
fn chain_head<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    lfe_index: Option<usize>,
    sample_rate: u32,
    cutoff_hz: f64,
) -> Vec<Bound<'py, PyArray1<f64>>> {
    let mut bed = to_bed(channels);
    py.detach(|| {
        head::chain_head(
            &mut bed,
            lfe_index,
            sample_rate,
            &head::HeadParams { cutoff_hz },
        )
    });
    from_bed(py, bed)
}

/// `bands` is one `(freq_hz, q, threshold_db, ratio, attack_ms, release_ms)`
/// tuple per band; returns the bed and each band's deepest cut in dB.
#[pyfunction]
#[pyo3(signature = (channels, lfe_index, sample_rate, bands))]
fn dynamic_eq<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    lfe_index: Option<usize>,
    sample_rate: u32,
    bands: Vec<(f64, f64, f64, f64, f64, f64)>,
) -> (Vec<Bound<'py, PyArray1<f64>>>, Vec<f64>) {
    let bands: Vec<dyneq::BandParams> = bands
        .into_iter()
        .map(
            |(freq_hz, q, threshold_db, ratio, attack_ms, release_ms)| dyneq::BandParams {
                freq_hz,
                q,
                threshold_db,
                ratio,
                attack_ms,
                release_ms,
            },
        )
        .collect();
    let mut bed = to_bed(channels);
    let cuts = py.detach(|| dyneq::dynamic_eq(&mut bed, lfe_index, sample_rate, &bands));
    (from_bed(py, bed), cuts)
}

#[pyfunction]
#[pyo3(signature = (channels, lfe_index, detector_channels, detector_lfe_index, sample_rate, bands))]
fn dynamic_eq_linked<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    lfe_index: Option<usize>,
    detector_channels: Vec<PyReadonlyArray1<'py, f64>>,
    detector_lfe_index: Option<usize>,
    sample_rate: u32,
    bands: Vec<(f64, f64, f64, f64, f64, f64)>,
) -> (Vec<Bound<'py, PyArray1<f64>>>, Vec<f64>) {
    let bands: Vec<dyneq::BandParams> = bands
        .into_iter()
        .map(
            |(freq_hz, q, threshold_db, ratio, attack_ms, release_ms)| dyneq::BandParams {
                freq_hz,
                q,
                threshold_db,
                ratio,
                attack_ms,
                release_ms,
            },
        )
        .collect();
    let mut targets = to_bed(channels);
    let detector = to_bed(detector_channels);
    let cuts = py.detach(|| {
        dyneq::dynamic_eq_linked(
            &mut targets,
            lfe_index,
            &detector,
            detector_lfe_index,
            sample_rate,
            &bands,
        )
    });
    (from_bed(py, targets), cuts)
}

#[pyfunction]
#[pyo3(signature = (channels, lfe_index, ceiling_dbtp, clip_db, knee))]
fn soft_clip<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    lfe_index: Option<usize>,
    ceiling_dbtp: f64,
    clip_db: f64,
    knee: f64,
) -> Vec<Bound<'py, PyArray1<f64>>> {
    let mut bed = to_bed(channels);
    py.detach(|| {
        clip::soft_clip(
            &mut bed,
            lfe_index,
            &clip::ClipParams {
                ceiling_dbtp,
                clip_db,
                knee,
            },
        )
    });
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
#[pyo3(signature = (channels, lfe_index, sample_rate, ceiling_dbtp, lookahead_ms, release_ms,
                    safety_margin_db))]
#[allow(clippy::too_many_arguments)]
fn lookahead_limit<'py>(
    py: Python<'py>,
    channels: Vec<PyReadonlyArray1<'py, f64>>,
    lfe_index: Option<usize>,
    sample_rate: u32,
    ceiling_dbtp: f64,
    lookahead_ms: f64,
    release_ms: f64,
    safety_margin_db: f64,
) -> (Vec<Bound<'py, PyArray1<f64>>>, f64, f64, f64) {
    let mut bed = to_bed(channels);
    let info = py.detach(|| {
        limiter::lookahead_limit(
            &mut bed,
            lfe_index,
            sample_rate,
            &limiter::LimiterParams {
                ceiling_dbtp,
                lookahead_ms,
                release_ms,
                safety_margin_db,
            },
        )
    });
    (
        from_bed(py, bed),
        info.max_gr_db,
        info.duty,
        info.lfe_max_gr_db,
    )
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(build_eq_fir, m)?)?;
    m.add_function(wrap_pyfunction!(apply_fir, m)?)?;
    m.add_function(wrap_pyfunction!(bus_compress, m)?)?;
    m.add_function(wrap_pyfunction!(bus_compress_linked, m)?)?;
    m.add_function(wrap_pyfunction!(bass_control, m)?)?;
    m.add_function(wrap_pyfunction!(chain_head, m)?)?;
    m.add_function(wrap_pyfunction!(dynamic_eq, m)?)?;
    m.add_function(wrap_pyfunction!(dynamic_eq_linked, m)?)?;
    m.add_function(wrap_pyfunction!(soft_clip, m)?)?;
    m.add_function(wrap_pyfunction!(forward_window_min, m)?)?;
    m.add_function(wrap_pyfunction!(lookahead_limit, m)?)?;
    Ok(())
}
