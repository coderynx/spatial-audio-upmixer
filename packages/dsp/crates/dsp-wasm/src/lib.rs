//! WebAssembly bindings for the shared DSP core.
//!
//! Deliberately a plain C ABI rather than wasm-bindgen: this module is
//! instantiated inside an `AudioWorkletGlobalScope`, which has no module
//! loader for wasm-bindgen's generated glue. The worklet shim owns the
//! marshalling instead.

use std::alloc::{alloc, dealloc, Layout};

use serde::Deserialize;
use upmixer_dsp_core::loudness;
use upmixer_dsp_core::mastering::{bass, compressor, eq, limiter};
use upmixer_dsp_core::stream::engine::{PreviewEngine, StemSource};
use upmixer_dsp_core::stream::measure::MeasurementPass;
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
    /// Reference-match level gain, applied before the correction FIR.
    #[serde(default = "unit")]
    reference_gain: f64,
    #[serde(default)]
    reference_fir: Vec<f64>,
    #[serde(default)]
    eq_fir: Vec<f64>,
    #[serde(default = "unit")]
    eq_strength: f64,
    #[serde(default)]
    compressor: Option<compressor::CompParams>,
    #[serde(default)]
    bass: Option<bass::BassParams>,
    #[serde(default)]
    limiter: Option<limiter::LimiterParams>,
}

fn unit() -> f64 {
    1.0
}

/// Binaural collapse parameters for the offline harness.
#[derive(Deserialize)]
struct CollapseParams {
    directions: Vec<(f64, f64)>,
    #[serde(default)]
    lfe_index: Option<usize>,
    #[serde(default)]
    lfe_gain: f64,
    #[serde(default)]
    lfe_cutoff_hz: f64,
    #[serde(default)]
    lfe_filter_order: usize,
    decode_taps: Vec<f64>,
    n_taps: usize,
    #[serde(default)]
    voicing: Option<upmixer_dsp_core::spatial::voicing::VoicingParams>,
    /// The delivery tail: BS.1770 normalization then a soft limit, matching
    /// `render_binaural_delivery`. Absent means stop after voicing.
    #[serde(default)]
    delivery: Option<DeliveryParams>,
}

#[derive(Deserialize)]
struct DeliveryParams {
    target_lkfs: f64,
    max_tp_dbtp: f64,
    max_gain_db: f64,
    soft_limit_threshold: f64,
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

    // Reference match, then EQ — the head of the contracted stage order.
    // The level gain reaches every channel; the correction curve and the
    // named EQ skip LFE (ledger D21).
    if params.reference_gain != 1.0 {
        for channel in bed.iter_mut() {
            for v in channel.iter_mut() {
                *v *= params.reference_gain;
            }
        }
    }
    let non_lfe: Vec<usize> = (0..n_channels).filter(|i| params.lfe_index != Some(*i)).collect();
    for &i in &non_lfe {
        if !params.reference_fir.is_empty() {
            bed[i] = eq::apply_fir(&bed[i], &params.reference_fir, 1.0);
        }
        if !params.eq_fir.is_empty() {
            bed[i] = eq::apply_fir(&bed[i], &params.eq_fir, params.eq_strength);
        }
    }

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

/// Collapse a mastered bed to binaural stereo, writing interleaved L/R.
///
/// The offline counterpart of the worklet's collapse, for the golden harness.
///
/// # Safety
/// `channels` must address `n_channels * n_frames` readable f64 samples and
/// `out` `2 * n_frames` writable ones.
#[no_mangle]
pub unsafe extern "C" fn dsp_render_binaural(
    channels: *const f64,
    n_channels: usize,
    n_frames: usize,
    sample_rate: u32,
    params_ptr: *const u8,
    params_len: usize,
    out: *mut f64,
) -> u32 {
    use upmixer_dsp_core::kernels::biquad::sosfilt;
    use upmixer_dsp_core::kernels::butter::{butter_sos, BandType};
    use upmixer_dsp_core::spatial::ambisonics::{decode_to_binaural, DecodeFilterSet, HoaBus, N_ACN_CHANNELS};
    use upmixer_dsp_core::spatial::voicing::apply_voicing;

    let json = std::slice::from_raw_parts(params_ptr, params_len);
    let Ok(params) = serde_json::from_slice::<CollapseParams>(json) else {
        return 0;
    };
    let flat = std::slice::from_raw_parts(channels, n_channels * n_frames);

    let mut hoa = HoaBus::new(n_frames);
    for (i, (azimuth, elevation)) in params.directions.iter().enumerate() {
        if params.lfe_index == Some(i) || i >= n_channels {
            continue;
        }
        hoa.add_source(&flat[i * n_frames..(i + 1) * n_frames], *azimuth, *elevation);
    }

    let taps = (0..N_ACN_CHANNELS)
        .map(|acn| {
            let base = acn * 2 * params.n_taps;
            [
                params.decode_taps[base..base + params.n_taps].to_vec(),
                params.decode_taps[base + params.n_taps..base + 2 * params.n_taps].to_vec(),
            ]
        })
        .collect();
    let (mut left, mut right) = decode_to_binaural(&hoa, &DecodeFilterSet { taps });

    // LFE joins before voicing, matching render_binaural (ledger D11).
    if let Some(lfe) = params.lfe_index {
        let nyq = sample_rate as f64 / 2.0;
        let sos = butter_sos(params.lfe_filter_order, params.lfe_cutoff_hz / nyq, BandType::Low);
        let filtered = sosfilt(&sos, &flat[lfe * n_frames..(lfe + 1) * n_frames]);
        for i in 0..n_frames {
            let v = filtered[i] * params.lfe_gain;
            left[i] += v;
            right[i] += v;
        }
    }

    if let Some(voicing) = params.voicing {
        let (l, r) = apply_voicing(&left, &right, sample_rate, &voicing);
        left = l;
        right = r;
    }

    if let Some(delivery) = params.delivery {
        // normalize_loudness's two stages, then the soft limit last — the
        // order render_binaural_delivery uses so the limiter only ever acts
        // as a true-peak safety net on an already-corrected signal.
        let weighted = [(1.0_f64, left.as_slice()), (1.0_f64, right.as_slice())];
        let measured = loudness::measure_integrated_loudness(&weighted, sample_rate);
        let gain_db = if measured > loudness::ABS_GATE {
            (delivery.target_lkfs - measured).min(delivery.max_gain_db)
        } else {
            0.0
        };
        let gain = 10.0_f64.powf(gain_db / 20.0);
        for v in left.iter_mut().chain(right.iter_mut()) {
            *v *= gain;
        }

        let peak = loudness::measure_true_peak(&[&left, &right]);
        if peak > delivery.max_tp_dbtp {
            let trim = 10.0_f64.powf((delivery.max_tp_dbtp - peak) / 20.0);
            for v in left.iter_mut().chain(right.iter_mut()) {
                *v *= trim;
            }
        }

        upmixer_dsp_core::spatial::downmix::soft_limit(&mut left, delivery.soft_limit_threshold);
        upmixer_dsp_core::spatial::downmix::soft_limit(&mut right, delivery.soft_limit_threshold);
    }

    let dst = std::slice::from_raw_parts_mut(out, 2 * n_frames);
    dst[..n_frames].copy_from_slice(&left);
    dst[n_frames..].copy_from_slice(&right);
    1
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
    let Some(engine) = engine.as_mut() else { return };
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
    let Some(engine) = engine.as_mut() else { return };
    engine.set_xtc_taps(std::slice::from_raw_parts(taps_ptr, n_taps).to_vec());
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
    let Some(engine) = engine.as_mut() else { return 0 };
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
    engine: *const PreviewEngine,
    out: *mut f32,
    capacity: usize,
) -> usize {
    let Some(engine) = engine.as_ref() else { return 0 };
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
    engine: *const PreviewEngine,
    out: *mut f32,
    capacity: usize,
) -> usize {
    let Some(engine) = engine.as_ref() else { return 0 };
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
    let Some(engine) = engine.as_ref() else { return std::ptr::null_mut() };
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
    let Some(engine) = engine.as_ref() else { return std::ptr::null_mut() };
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

/// Measure up to `frames` more. Returns 1 and writes `[lkfs, dbtp]` into `out`
/// once the programme is exhausted, 0 while there is more to do.
///
/// # Safety
/// `pass` must come from [`dsp_measure_begin`] and `out` must address two
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
        Some((lkfs, dbtp)) => {
            let dst = std::slice::from_raw_parts_mut(out, 2);
            dst[0] = lkfs;
            dst[1] = dbtp;
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
