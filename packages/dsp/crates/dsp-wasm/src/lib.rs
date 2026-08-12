//! WebAssembly bindings for the shared DSP core.
//!
//! Deliberately a plain C ABI rather than wasm-bindgen: this module is
//! instantiated inside an `AudioWorkletGlobalScope`, which has no module
//! loader for wasm-bindgen's generated glue. The worklet shim owns the
//! marshalling instead.

use std::alloc::{alloc, dealloc, Layout};

use serde::Deserialize;
use upmixer_dsp_core::loudness;
use upmixer_dsp_core::mastering::{bass, compressor, limiter};
use upmixer_dsp_core::stream::engine::{PreviewEngine, StemSource};
use upmixer_dsp_core::stream::params::EngineParams;

/// Mastering parameters as the host sends them. Every value is owned by
/// `packages/core/src/config.py` and the profile tables and is served to the
/// browser — nothing here has a default of its own.
#[derive(Deserialize)]
struct MasterParams {
    #[serde(default)]
    lfe_index: Option<usize>,
    #[serde(default)]
    stereo_pairs: Vec<(usize, usize)>,
    #[serde(default)]
    compressor: Option<compressor::CompParams>,
    #[serde(default)]
    bass: Option<bass::BassParams>,
    #[serde(default)]
    limiter: Option<limiter::LimiterParams>,
}

/// Allocate `bytes` of linear memory for the host to write into.
///
/// # Safety
/// The caller must return the pointer to [`dsp_free`] with the same size.
#[no_mangle]
pub unsafe extern "C" fn dsp_alloc(bytes: usize) -> *mut u8 {
    if bytes == 0 {
        return std::ptr::null_mut();
    }
    let layout = Layout::from_size_align(bytes, 8).expect("invalid allocation layout");
    alloc(layout)
}

/// Release memory previously handed out by [`dsp_alloc`].
///
/// # Safety
/// `ptr` must come from [`dsp_alloc`] with the same `bytes`.
#[no_mangle]
pub unsafe extern "C" fn dsp_free(ptr: *mut u8, bytes: usize) {
    if ptr.is_null() || bytes == 0 {
        return;
    }
    let layout = Layout::from_size_align(bytes, 8).expect("invalid allocation layout");
    dealloc(ptr, layout);
}

/// Master a de-interleaved bed in place.
///
/// `channels` points at `n_channels` consecutive blocks of `n_frames` f64
/// samples. Runs the same chain the export does, in the contracted order, so
/// the preview is the export rather than an approximation of it. Returns the
/// peak gain reduction the limiter applied, in dB.
///
/// # Safety
/// `channels` must address `n_channels * n_frames` writable f64 samples, and
/// `params_ptr`/`params_len` a UTF-8 JSON object.
#[no_mangle]
pub unsafe extern "C" fn dsp_master_bed(
    channels: *mut f64,
    n_channels: usize,
    n_frames: usize,
    sample_rate: u32,
    params_ptr: *const u8,
    params_len: usize,
) -> f64 {
    if channels.is_null() || n_channels == 0 || n_frames == 0 {
        return 0.0;
    }
    let json = std::slice::from_raw_parts(params_ptr, params_len);
    let Ok(params) = serde_json::from_slice::<MasterParams>(json) else {
        return f64::NAN;
    };

    let flat = std::slice::from_raw_parts_mut(channels, n_channels * n_frames);
    let mut bed: Vec<Vec<f64>> = flat
        .chunks_exact(n_frames)
        .map(|c| c.to_vec())
        .collect();

    if let Some(comp) = params.compressor {
        compressor::bus_compress(&mut bed, params.lfe_index, sample_rate, &comp);
    }
    if let Some(bass) = params.bass {
        bass::bass_control(&mut bed, params.lfe_index, &params.stereo_pairs, sample_rate, &bass);
    }
    let reduction = match params.limiter {
        Some(l) => limiter::lookahead_limit(&mut bed, sample_rate, &l),
        None => 0.0,
    };

    for (i, channel) in bed.iter().enumerate() {
        flat[i * n_frames..(i + 1) * n_frames].copy_from_slice(channel);
    }
    reduction
}

/// Integrated loudness of a de-interleaved bed, in LKFS.
///
/// # Safety
/// `channels` must address `n_channels * n_frames` readable f64 samples and
/// `weights` `n_channels` readable f64 values.
#[no_mangle]
pub unsafe extern "C" fn dsp_integrated_loudness(
    channels: *const f64,
    weights: *const f64,
    n_channels: usize,
    n_frames: usize,
    sample_rate: u32,
) -> f64 {
    if channels.is_null() || n_channels == 0 || n_frames == 0 {
        return -70.0;
    }
    let flat = std::slice::from_raw_parts(channels, n_channels * n_frames);
    let w = std::slice::from_raw_parts(weights, n_channels);
    let weighted: Vec<(f64, &[f64])> = w
        .iter()
        .enumerate()
        .map(|(i, weight)| (*weight, &flat[i * n_frames..(i + 1) * n_frames]))
        .collect();
    loudness::measure_integrated_loudness(&weighted, sample_rate)
}

/// True peak of a de-interleaved bed, in dBTP.
///
/// # Safety
/// `channels` must address `n_channels * n_frames` readable f64 samples.
#[no_mangle]
pub unsafe extern "C" fn dsp_true_peak_dbtp(
    channels: *const f64,
    n_channels: usize,
    n_frames: usize,
) -> f64 {
    if channels.is_null() || n_channels == 0 || n_frames == 0 {
        return -120.0;
    }
    let flat = std::slice::from_raw_parts(channels, n_channels * n_frames);
    let refs: Vec<&[f64]> = (0..n_channels)
        .map(|i| &flat[i * n_frames..(i + 1) * n_frames])
        .collect();
    loudness::measure_true_peak(&refs)
}

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
    Box::into_raw(Box::new(PreviewEngine::new(sample_rate, params, Vec::new())))
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
    let Some(engine) = engine.as_mut() else { return };
    engine.push_stem(StemSource {
        left: std::slice::from_raw_parts(left, n_frames).to_vec(),
        right: std::slice::from_raw_parts(right, n_frames).to_vec(),
    });
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
    let Some(engine) = engine.as_mut() else { return 0 };
    let mut scratch = vec![0.0_f64; n_channels * n_frames];
    let written = engine.render(&mut scratch, n_frames);
    let dst = std::slice::from_raw_parts_mut(out, n_channels * n_frames);
    for (d, s) in dst.iter_mut().zip(scratch.iter()) {
        *d = *s as f32;
    }
    written
}

/// Total frames the loaded stems span.
///
/// # Safety
/// `engine` must come from [`dsp_engine_new`].
#[no_mangle]
pub unsafe extern "C" fn dsp_engine_total_frames(engine: *const PreviewEngine) -> usize {
    engine.as_ref().map(|e| e.total_frames()).unwrap_or(0)
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

/// Core revision, encoded so the host can assert wheel/wasm provenance.
#[no_mangle]
pub extern "C" fn dsp_core_version_len() -> usize {
    upmixer_dsp_core::version().len()
}

/// Pointer to the version string bytes (length from [`dsp_core_version_len`]).
#[no_mangle]
pub extern "C" fn dsp_core_version_ptr() -> *const u8 {
    upmixer_dsp_core::version().as_ptr()
}
