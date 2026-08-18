//! ITU-R BS.1770-4/5 integrated loudness and true-peak metering.
//!
//! Channel weights and the format's channel order stay with the caller —
//! they are configuration, owned by `packages/core/src/loudness.py` and
//! served to the web (see `docs/contracts/preview_export_parity.md` §2).

use crate::kernels::biquad::sosfilt;
use crate::kernels::sum::pairwise_sum;
use crate::kernels::upfirdn::upfirdn_up;

pub const BLOCK_S: f64 = 0.400;
pub const HOP_S: f64 = 0.100;
pub const ABS_GATE: f64 = -70.0;
pub const REL_GATE_OFFSET: f64 = -10.0;
pub const LKFS_OFFSET: f64 = -0.691;

/// BS.1770-4 Annex 1 Tables 1-2, exact at 48 kHz.
pub const K_STAGE1_48K: [f64; 6] = [
    1.53512485958697, -2.69169618940638, 1.19839281085285,
    1.0, -1.69065929318241, 0.73248077421585,
];
pub const K_STAGE2_48K: [f64; 6] = [
    1.0, -2.0, 1.0,
    1.0, -1.99004745483398, 0.99007225036621,
];

/// BS.1770-5 Annex 2 order-48 four-phase true-peak interpolation FIR.
pub const TRUE_PEAK_FIR_4X: [f64; 48] = [
    0.0017089843750, -0.0291748046875, -0.0189208984375, -0.0083007812500,
    0.0109863281250, 0.0292968750000, 0.0330810546875, 0.0148925781250,
    -0.0196533203125, -0.0517578125000, -0.0582275390625, -0.0266113281250,
    0.0332031250000, 0.0891113281250, 0.1015625000000, 0.0476074218750,
    -0.0594482421875, -0.1665039062500, -0.2003173828125, -0.1022949218750,
    0.1373291015625, 0.4650878906250, 0.7797851562500, 0.9721679687500,
    0.9721679687500, 0.7797851562500, 0.4650878906250, 0.1373291015625,
    -0.1022949218750, -0.2003173828125, -0.1665039062500, -0.0594482421875,
    0.0476074218750, 0.1015625000000, 0.0891113281250, 0.0332031250000,
    -0.0266113281250, -0.0582275390625, -0.0517578125000, -0.0196533203125,
    0.0148925781250, 0.0330810546875, 0.0292968750000, 0.0109863281250,
    -0.0083007812500, -0.0189208984375, -0.0291748046875, 0.0017089843750,
];

pub const TRUE_PEAK_OVERSAMPLE: usize = 4;

/// Map an exact 48 kHz digital biquad onto another rate by inverting the
/// bilinear transform and re-applying it, matching `_retarget_biquad`.
pub fn retarget_biquad(section: [f64; 6], sample_rate: u32) -> [f64; 6] {
    let k = 2.0 * 48_000.0;
    let to_analog = |c0: f64, c1: f64, c2: f64| {
        [(c0 - c1 + c2) / (k * k), 2.0 * (c0 - c2) / k, c0 + c1 + c2]
    };
    let b_a = to_analog(section[0], section[1], section[2]);
    let a_a = to_analog(1.0, section[4], section[5]);

    // scipy.signal.bilinear at fs = sample_rate, for the M = 2 case.
    let kk = 2.0 * sample_rate as f64;
    let expand = |c: [f64; 3]| {
        let s2 = c[0] * kk * kk;
        let s1 = c[1] * kk;
        let s0 = c[2];
        [s2 + s1 + s0, 2.0 * (s0 - s2), s2 - s1 + s0]
    };
    let b_z = expand(b_a);
    let a_z = expand(a_a);
    [
        b_z[0] / a_z[0], b_z[1] / a_z[0], b_z[2] / a_z[0],
        1.0, a_z[1] / a_z[0], a_z[2] / a_z[0],
    ]
}

/// K-weighting as two second-order sections at the given rate.
pub fn k_weighting_sos(sample_rate: u32) -> Vec<[f64; 6]> {
    if sample_rate == 48_000 {
        return vec![K_STAGE1_48K, K_STAGE2_48K];
    }
    vec![
        retarget_biquad(K_STAGE1_48K, sample_rate),
        retarget_biquad(K_STAGE2_48K, sample_rate),
    ]
}

/// Weighted mean-square per gating block for one channel, or `None` when the
/// channel is shorter than a single block.
fn channel_weighted_blocks(
    audio: &[f64],
    weight: f64,
    sos: &[[f64; 6]],
    block_len: usize,
    hop_len: usize,
) -> Option<Vec<f64>> {
    if audio.len() < block_len {
        return None;
    }
    let filtered = sosfilt(sos, audio);
    let squared: Vec<f64> = filtered.iter().map(|v| v * v).collect();
    let n_blocks = (audio.len() - block_len) / hop_len + 1;
    let scale = weight / block_len as f64;
    Some(
        (0..n_blocks)
            .map(|b| {
                let start = b * hop_len;
                pairwise_sum(&squared[start..start + block_len]) * scale
            })
            .collect(),
    )
}

/// BS.1770-4 integrated loudness with absolute + relative gating.
///
/// `channels` pairs each channel's BS.1770 weight with its samples; zero-
/// weight channels (LFE) must be omitted by the caller, exactly as the
/// Python implementation skips them.
pub fn measure_integrated_loudness(channels: &[(f64, &[f64])], sample_rate: u32) -> f64 {
    let block_len = (BLOCK_S * sample_rate as f64) as usize;
    let hop_len = (HOP_S * sample_rate as f64) as usize;
    if block_len == 0 || hop_len == 0 {
        return ABS_GATE;
    }
    let sos = k_weighting_sos(sample_rate);

    let mut power_blocks: Option<Vec<f64>> = None;
    for (weight, audio) in channels {
        if *weight == 0.0 {
            continue;
        }
        let Some(meansq) = channel_weighted_blocks(audio, *weight, &sos, block_len, hop_len)
        else {
            continue;
        };
        power_blocks = Some(match power_blocks {
            None => meansq,
            Some(acc) => {
                let n = acc.len().min(meansq.len());
                acc[..n].iter().zip(meansq[..n].iter()).map(|(a, b)| a + b).collect()
            }
        });
    }

    let Some(power_blocks) = power_blocks else {
        return ABS_GATE;
    };
    if power_blocks.is_empty() {
        return ABS_GATE;
    }

    let block_lkfs: Vec<f64> = power_blocks
        .iter()
        .map(|p| LKFS_OFFSET + 10.0 * p.max(1e-30).log10())
        .collect();
    let above_abs: Vec<usize> = (0..block_lkfs.len())
        .filter(|&i| block_lkfs[i] >= ABS_GATE)
        .collect();
    if above_abs.is_empty() {
        return ABS_GATE;
    }

    let mean_of = |idx: &[usize]| {
        let vals: Vec<f64> = idx.iter().map(|&i| power_blocks[i]).collect();
        pairwise_sum(&vals) / vals.len() as f64
    };
    let ungated = LKFS_OFFSET + 10.0 * mean_of(&above_abs).max(1e-30).log10();
    let above_rel: Vec<usize> = above_abs
        .iter()
        .copied()
        .filter(|&i| block_lkfs[i] >= ungated + REL_GATE_OFFSET)
        .collect();

    let gated = if above_rel.is_empty() { &above_abs } else { &above_rel };
    LKFS_OFFSET + 10.0 * mean_of(gated).max(1e-30).log10()
}

/// Linear true peak of one channel via the 4x BS.1770 interpolator.
pub fn true_peak_channel(audio: &[f64]) -> f64 {
    if audio.is_empty() {
        return 0.0;
    }
    upfirdn_up(&TRUE_PEAK_FIR_4X, audio, TRUE_PEAK_OVERSAMPLE)
        .iter()
        .fold(0.0_f64, |m, v| m.max(v.abs()))
}

/// True peak in dBTP across every channel, LFE included per BS.1770-5.
pub fn measure_true_peak(channels: &[&[f64]]) -> f64 {
    if channels.is_empty() {
        return -120.0;
    }
    let peak = channels
        .iter()
        .fold(1e-30_f64, |m, ch| m.max(true_peak_channel(ch)));
    20.0 * peak.log10()
}
