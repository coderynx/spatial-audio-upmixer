//! Lifecycle and per-block calls for a streaming `PreviewEngine`.

use upmixer_dsp_core::stream::engine::{PreviewEngine, StemSource};
use upmixer_dsp_core::stream::params::EngineParams;

/// Create a preview engine from a JSON parameter block.
///
/// Returns null if the JSON does not parse. Stems are added separately, in
/// the order their entries appear in `params.stems`.
///
/// # Safety
/// `params_ptr`/`params_len` must address a UTF-8 JSON object.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_new(
    sample_rate: u32,
    params_ptr: *const u8,
    params_len: usize,
) -> *mut PreviewEngine {
    let json = std::slice::from_raw_parts(params_ptr, params_len);
    let Ok(params) = serde_json::from_slice::<EngineParams>(json) else {
        return std::ptr::null_mut();
    };
    Box::into_raw(Box::new(PreviewEngine::new(
        sample_rate,
        params,
        Vec::new(),
    )))
}

/// Copy one stem's decoded PCM into the engine's heap.
///
/// # Safety
/// `left` and `right` must each address `n_frames` readable f32 samples, and
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_add_stem(
    engine: *mut PreviewEngine,
    left: *const f32,
    right: *const f32,
    n_frames: usize,
) {
    let Some(engine) = engine.as_mut() else {
        return;
    };
    engine.push_stem(StemSource {
        left: std::slice::from_raw_parts(left, n_frames).to_vec(),
        right: std::slice::from_raw_parts(right, n_frames).to_vec(),
    });
}

/// Replace the binaural decode bank on a live engine.
///
/// Ships separately from the JSON parameter block: order-3 ambisonics is 16
/// channels x 2 ears x several thousand taps, and the bank changes only when
/// the spatial profile does, so folding it into every `dsp_engine_set_params`
/// call would re-encode and re-parse megabytes of float text for edits that
/// never touch it.
///
/// # Safety
/// `taps_ptr` must address `n_taps` readable f64 samples, and `engine` must
/// come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_set_decode_taps(
    engine: *mut PreviewEngine,
    taps_ptr: *const f64,
    n_taps: usize,
) {
    let Some(engine) = engine.as_mut() else {
        return;
    };
    engine.set_decode_taps(std::slice::from_raw_parts(taps_ptr, n_taps).to_vec());
}

/// Replace the crosstalk-cancellation matrix on a live engine. See
/// [`dsp_engine_set_decode_taps`].
///
/// # Safety
/// `taps_ptr` must address `n_taps` readable f64 samples, and `engine` must
/// come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_set_xtc_taps(
    engine: *mut PreviewEngine,
    taps_ptr: *const f64,
    n_taps: usize,
) {
    let Some(engine) = engine.as_mut() else {
        return;
    };
    engine.set_xtc_taps(std::slice::from_raw_parts(taps_ptr, n_taps).to_vec());
}

/// Replace one stem's EQ FIR through the binary tap channel.
///
/// # Safety
/// `taps_ptr` must address `n_taps` readable f64 samples.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_set_stem_eq_taps(
    engine: *mut PreviewEngine,
    index: usize,
    taps_ptr: *const f64,
    n_taps: usize,
) {
    if let Some(engine) = engine.as_mut() {
        let taps = if n_taps == 0 {
            Vec::new()
        } else {
            std::slice::from_raw_parts(taps_ptr, n_taps).to_vec()
        };
        engine.set_stem_eq_taps(index, taps);
    }
}

/// Replace the master EQ FIR through the binary tap channel.
///
/// # Safety
/// `taps_ptr` must address `n_taps` readable f64 samples.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_set_master_eq_taps(
    engine: *mut PreviewEngine,
    taps_ptr: *const f64,
    n_taps: usize,
) {
    if let Some(engine) = engine.as_mut() {
        let taps = if n_taps == 0 {
            Vec::new()
        } else {
            std::slice::from_raw_parts(taps_ptr, n_taps).to_vec()
        };
        engine.set_master_eq_taps(taps);
    }
}

/// Replace the reference-match FIR through the binary tap channel.
///
/// # Safety
/// `taps_ptr` must address `n_taps` readable f64 samples.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_set_reference_taps(
    engine: *mut PreviewEngine,
    taps_ptr: *const f64,
    n_taps: usize,
) {
    if let Some(engine) = engine.as_mut() {
        let taps = if n_taps == 0 {
            Vec::new()
        } else {
            std::slice::from_raw_parts(taps_ptr, n_taps).to_vec()
        };
        engine.set_reference_taps(taps);
    }
}

/// Render `n_frames` into `out`, channel-major, as f32 for Web Audio.
///
/// Returns the number of frames written; a short count means the programme
/// ended.
///
/// # Safety
/// `out` must address `n_channels * n_frames` writable f32 samples.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_render(
    engine: *mut PreviewEngine,
    out: *mut f32,
    n_channels: usize,
    n_frames: usize,
) -> usize {
    let Some(engine) = engine.as_mut() else {
        return 0;
    };
    let dst = std::slice::from_raw_parts_mut(out, n_channels * n_frames);
    engine.render_f32(dst, n_frames)
}

/// Total frames the loaded stems span.
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_total_frames(engine: *const PreviewEngine) -> usize {
    engine.as_ref().map(|e| e.total_frames()).unwrap_or(0)
}

/// Replace the parameter block, keeping the stems and playhead.
///
/// Returns 1 on success, 0 if the JSON does not parse — in which case the
/// engine keeps rendering with the parameters it already had.
///
/// # Safety
/// `params_ptr`/`params_len` must address a UTF-8 JSON object and `engine`
/// must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_set_params(
    engine: *mut PreviewEngine,
    params_ptr: *const u8,
    params_len: usize,
) -> u32 {
    let Some(engine) = engine.as_mut() else {
        return 0;
    };
    let json = std::slice::from_raw_parts(params_ptr, params_len);
    match serde_json::from_slice::<EngineParams>(json) {
        Ok(params) => {
            engine.update_params(params);
            1
        }
        Err(_) => 0,
    }
}

/// Move the playhead, warming filter states up from before the target.
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_seek(engine: *mut PreviewEngine, frame: usize) {
    if let Some(engine) = engine.as_mut() {
        engine.seek(frame);
    }
}

/// Begin a seek whose discarded run-up is advanced separately.
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_begin_seek(engine: *mut PreviewEngine, frame: usize) {
    if let Some(engine) = engine.as_mut() {
        engine.begin_seek(frame);
    }
}

/// Advance a pending seek by at most `frames`; returns 1 once it is ready.
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_advance_seek(engine: *mut PreviewEngine, frames: usize) -> i32 {
    engine
        .as_mut()
        .is_some_and(|engine| engine.advance_seek(frames)) as i32
}

/// Whether a seek's discarded run-up is still in progress.
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_is_seeking(engine: *const PreviewEngine) -> i32 {
    engine.as_ref().is_some_and(PreviewEngine::is_seeking) as i32
}

/// Frames emitted so far, which is the playhead.
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_position(engine: *const PreviewEngine) -> usize {
    engine.as_ref().map(|e| e.position()).unwrap_or(0)
}

/// Channels the collapse writes: two for every mode but native.
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_output_channels(engine: *const PreviewEngine) -> usize {
    engine.as_ref().map(|e| e.output_channels()).unwrap_or(0)
}

/// Copy the latest levels into `out` as `[rms, peak]` pairs — stems (each as
/// a left/right pair), then bed channels, then the output pair. Returns the
/// number of floats written.
///
/// # Safety
/// `out` must address `capacity` writable f32 values.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_meters(
    engine: *mut PreviewEngine,
    out: *mut f32,
    capacity: usize,
) -> usize {
    let Some(engine) = engine.as_mut() else {
        return 0;
    };
    let dst = std::slice::from_raw_parts_mut(out, capacity);
    engine.meters().write(dst).min(capacity)
}

/// Copy each stem's `[level, centroid]` pair for the haze/elevation
/// displays. Returns the number of floats written.
///
/// # Safety
/// `out` must address `capacity` writable f32 values.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_stem_spectrum(
    engine: *mut PreviewEngine,
    out: *mut f32,
    capacity: usize,
) -> usize {
    let Some(engine) = engine.as_mut() else {
        return 0;
    };
    let dst = std::slice::from_raw_parts_mut(out, capacity);
    let mut i = 0;
    for (level, centroid) in engine.stem_spectrum() {
        if i + 1 < dst.len() {
            dst[i] = level as f32;
            dst[i + 1] = centroid as f32;
        }
        i += 2;
    }
    i.min(capacity)
}

/// Reset the transport and every filter state to the top of the programme.
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_rewind(engine: *mut PreviewEngine) {
    if let Some(engine) = engine.as_mut() {
        engine.rewind();
    }
}

/// Destroy an engine created by [`dsp_engine_new`].
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`] and not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_free(engine: *mut PreviewEngine) {
    if !engine.is_null() {
        drop(Box::from_raw(engine));
    }
}
