//! One-shot entry points: master a bed, measure it, collapse to binaural.

use upmixer_dsp_core::loudness;
use upmixer_dsp_core::mastering::{bass, compressor, eq, limiter};

use crate::params::{CollapseParams, MasterParams};

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
        bass::bass_control(&mut bed, params.lfe_index, &params.lf_targets, sample_rate, &bass);
    }
    let reduction = match params.limiter {
        Some(l) => limiter::lookahead_limit(&mut bed, sample_rate, &l).max_gr_db,
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
    use upmixer_dsp_core::kernels::butter::linkwitz_riley_lowpass_sos;
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
        let sos = linkwitz_riley_lowpass_sos(params.lfe_filter_order, params.lfe_cutoff_hz / nyq);
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
