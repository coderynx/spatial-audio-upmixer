//! The per-stem route-scale measurement, advanced in slices.

use upmixer_dsp_core::stream::engine::PreviewEngine;
use upmixer_dsp_core::stream::scale::RouteScalePass;

/// Whether the engine is rendering on the host's estimated route scales and
/// wants a measurement — true after load and after any parameter the routing
/// reads has moved.
///
/// # Safety
/// `engine` must come from `dsp_engine_new`.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_wants_route_scale(engine: *const PreviewEngine) -> i32 {
    let Some(engine) = engine.as_ref() else {
        return 0;
    };
    i32::from(engine.stem_count() > 0 && !engine.has_route_scales())
}

/// Begin measuring every stem's route scale over the whole programme, which
/// is what the export measures; returns a pass handle, or null.
///
/// # Safety
/// `engine` must come from `dsp_engine_new`.
#[no_mangle]
pub unsafe extern "C" fn dsp_scale_begin(engine: *const PreviewEngine) -> *mut RouteScalePass {
    let Some(engine) = engine.as_ref() else {
        return std::ptr::null_mut();
    };
    Box::into_raw(Box::new(RouteScalePass::new(engine)))
}

/// Begin measuring every stem over `count` excerpts — a first answer in a
/// fraction of the time, which a whole-programme pass then replaces.
///
/// # Safety
/// `engine` must come from `dsp_engine_new`.
#[no_mangle]
pub unsafe extern "C" fn dsp_scale_begin_excerpts(
    engine: *const PreviewEngine,
    count: usize,
    excerpt_frames: usize,
    preroll_frames: usize,
) -> *mut RouteScalePass {
    let Some(engine) = engine.as_ref() else {
        return std::ptr::null_mut();
    };
    Box::into_raw(Box::new(RouteScalePass::new_excerpts(
        engine,
        count,
        excerpt_frames,
        preroll_frames,
    )))
}

/// Measure up to `frames` more. Returns 1 once every stem is measured, having
/// handed the result to `engine`, which renders on it from the next block.
///
/// # Safety
/// `pass` must come from [`dsp_scale_begin`] and `engine` from
/// `dsp_engine_new`.
#[no_mangle]
pub unsafe extern "C" fn dsp_scale_advance(
    pass: *mut RouteScalePass,
    engine: *mut PreviewEngine,
    frames: usize,
) -> i32 {
    let Some(pass) = pass.as_mut() else { return 0 };
    let Some(scales) = pass.advance(frames) else {
        return 0;
    };
    if let Some(engine) = engine.as_mut() {
        engine.set_route_scales(scales);
    }
    1
}

/// Fraction of the stems measured so far, for a progress indicator.
///
/// # Safety
/// `pass` must come from [`dsp_scale_begin`].
#[no_mangle]
pub unsafe extern "C" fn dsp_scale_progress(pass: *const RouteScalePass) -> f64 {
    pass.as_ref().map(|p| p.progress()).unwrap_or(0.0)
}

/// Release a route-scale pass.
///
/// # Safety
/// `pass` must come from [`dsp_scale_begin`] and must not be used after.
#[no_mangle]
pub unsafe extern "C" fn dsp_scale_free(pass: *mut RouteScalePass) {
    if !pass.is_null() {
        drop(Box::from_raw(pass));
    }
}

/// The normalization one stem currently renders at — the measurement once it
/// has landed, the host's estimate until then.
///
/// # Safety
/// `engine` must come from `dsp_engine_new`.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_route_scale(engine: *const PreviewEngine, index: usize) -> f64 {
    engine.as_ref().map(|e| e.route_scale(index)).unwrap_or(1.0)
}
