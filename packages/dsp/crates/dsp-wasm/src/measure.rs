//! Incremental whole-programme and excerpt measurement passes.

use upmixer_dsp_core::stream::engine::PreviewEngine;
use upmixer_dsp_core::stream::measure::MeasurementPass;

/// Begin measuring the collapsed programme; returns a pass handle, or null.
///
/// The measurement renders the whole programme, which costs far more than one
/// render quantum allows, so the host advances the pass in slices rather than
/// blocking the audio thread — see `stream::measure`.
///
/// # Safety
/// `weights` must address `n_channels` readable f64 values; `engine` must come
/// from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_measure_begin(
    engine: *const PreviewEngine,
    weights: *const f64,
    n_channels: usize,
) -> *mut MeasurementPass {
    let Some(engine) = engine.as_ref() else {
        return std::ptr::null_mut();
    };
    let w = if weights.is_null() {
        Vec::new()
    } else {
        std::slice::from_raw_parts(weights, n_channels).to_vec()
    };
    Box::into_raw(Box::new(MeasurementPass::new(engine, &w)))
}

/// Begin measuring `count` excerpts of `excerpt_frames` each, spread evenly
/// across the programme, each preceded by `preroll_frames` of discarded
/// warm-up. Falls back to the whole programme when it is shorter than the
/// plan needs. Used for a fast first correction; a full [`dsp_measure_begin`]
/// pass then refines it in the background — see `stream::measure`.
///
/// # Safety
/// `weights` must address `n_channels` readable f64 values; `engine` must come
/// from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_measure_begin_excerpts(
    engine: *const PreviewEngine,
    weights: *const f64,
    n_channels: usize,
    count: usize,
    excerpt_frames: usize,
    preroll_frames: usize,
) -> *mut MeasurementPass {
    let Some(engine) = engine.as_ref() else {
        return std::ptr::null_mut();
    };
    let w = if weights.is_null() {
        Vec::new()
    } else {
        std::slice::from_raw_parts(weights, n_channels).to_vec()
    };
    Box::into_raw(Box::new(MeasurementPass::new_excerpts(
        engine,
        &w,
        count,
        excerpt_frames,
        preroll_frames,
    )))
}

/// Measure up to `frames` more. Returns 1 and writes
/// `[lkfs, dbtp, monitor_lkfs, monitor_dbtp]` into `out` once the programme
/// is exhausted, 0 while there is more to do.
///
/// # Safety
/// `pass` must come from [`dsp_measure_begin`] and `out` must address four
/// writable f64 values.
#[no_mangle]
pub unsafe extern "C" fn dsp_measure_advance(
    pass: *mut MeasurementPass,
    frames: usize,
    out: *mut f64,
) -> i32 {
    let Some(pass) = pass.as_mut() else { return 0 };
    match pass.advance(frames) {
        None => 0,
        Some(result) => {
            let dst = std::slice::from_raw_parts_mut(out, 4);
            dst.copy_from_slice(&result);
            1
        }
    }
}

/// Fraction of the programme measured so far, for a progress indicator.
///
/// # Safety
/// `pass` must come from [`dsp_measure_begin`].
#[no_mangle]
pub unsafe extern "C" fn dsp_measure_progress(pass: *const MeasurementPass) -> f64 {
    pass.as_ref().map(|p| p.progress()).unwrap_or(0.0)
}

/// Release a measurement pass.
///
/// # Safety
/// `pass` must come from [`dsp_measure_begin`] and must not be used after.
#[no_mangle]
pub unsafe extern "C" fn dsp_measure_free(pass: *mut MeasurementPass) {
    if !pass.is_null() {
        drop(Box::from_raw(pass));
    }
}
