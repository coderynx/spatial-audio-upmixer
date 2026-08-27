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

/// EBU Tech 3341 short-term window; Tech 3342 measures LRA over these.
pub const SHORT_TERM_S: f64 = 3.000;
/// EBU Tech 3342 relative gate for the loudness-range distribution.
pub const LRA_GATE_OFFSET: f64 = -20.0;
pub const LRA_LOW_PERCENTILE: f64 = 0.10;
pub const LRA_HIGH_PERCENTILE: f64 = 0.95;

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

/// One channel's gated K-weighted mean square — BS.1770's `z_i`, from that
/// channel's own integrated loudness. Zero when the material is too short or
/// too quiet to gate, which is what `loudness.py::k_weighted_power` returns.
pub fn gated_power(lkfs: f64) -> f64 {
    if lkfs > ABS_GATE {
        10.0_f64.powf((lkfs - LKFS_OFFSET) / 10.0)
    } else {
        0.0
    }
}

/// BS.1770-4 Annex 1 Table 3's literal surround weight, +1.5 dB.
const SURROUND_WEIGHT: f64 = 1.41;

/// BS.1770-5 Annex 3 Table 5 channel weight, by speaker name. Only the
/// ear-level side channels take the +1.5 dB; rear and upper channels are
/// unity and LFE is excluded from the sum entirely.
pub fn loudness_channel_weight(name: &str) -> f64 {
    match name {
        "SL" | "SR" => SURROUND_WEIGHT,
        "LFE" => 0.0,
        _ => 1.0,
    }
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
    if block_len % hop_len != 0 {
        return Some(
            (0..n_blocks)
                .map(|b| {
                    let start = b * hop_len;
                    pairwise_sum(&squared[start..start + block_len]) * scale
                })
                .collect(),
        );
    }
    let hops_per_block = block_len / hop_len;
    let hop_sums: Vec<f64> = (0..n_blocks + hops_per_block - 1)
        .map(|hop| {
            let start = hop * hop_len;
            pairwise_sum(&squared[start..start + hop_len])
        })
        .collect();
    Some(
        (0..n_blocks)
            .map(|b| pairwise_sum(&hop_sums[b..b + hops_per_block]) * scale)
            .collect(),
    )
}

/// Summed weighted mean-square per window across every present channel, for a
/// window of `window_s` seconds advanced by `HOP_S`.
///
/// One K-weighting pass feeds the integrated, momentary, short-term and LRA
/// statistics; they differ only in window length and how the blocks are gated.
fn weighted_power_blocks(
    channels: &[(f64, &[f64])],
    sample_rate: u32,
    window_s: f64,
) -> Option<Vec<f64>> {
    let block_len = (window_s * sample_rate as f64) as usize;
    let hop_len = (HOP_S * sample_rate as f64) as usize;
    if block_len == 0 || hop_len == 0 {
        return None;
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
    power_blocks.filter(|b| !b.is_empty())
}

fn block_loudness(power_blocks: &[f64]) -> Vec<f64> {
    power_blocks
        .iter()
        .map(|p| LKFS_OFFSET + 10.0 * p.max(1e-30).log10())
        .collect()
}

fn mean_loudness(power_blocks: &[f64], idx: &[usize]) -> f64 {
    let vals: Vec<f64> = idx.iter().map(|&i| power_blocks[i]).collect();
    LKFS_OFFSET + 10.0 * (pairwise_sum(&vals) / vals.len() as f64).max(1e-30).log10()
}

/// Indices surviving the absolute gate, then a gate `offset` LU below the
/// ungated mean of those — the two-pass shape both BS.1770 (−10 LU) and
/// EBU Tech 3342 (−20 LU) use.
fn gated_indices(power_blocks: &[f64], block_lkfs: &[f64], offset: f64) -> Vec<usize> {
    let above_abs: Vec<usize> = (0..block_lkfs.len())
        .filter(|&i| block_lkfs[i] >= ABS_GATE)
        .collect();
    if above_abs.is_empty() {
        return above_abs;
    }
    let ungated = mean_loudness(power_blocks, &above_abs);
    let above_rel: Vec<usize> = above_abs
        .iter()
        .copied()
        .filter(|&i| block_lkfs[i] >= ungated + offset)
        .collect();
    if above_rel.is_empty() { above_abs } else { above_rel }
}

/// BS.1770-4 integrated loudness with absolute + relative gating.
///
/// `channels` pairs each channel's BS.1770 weight with its samples; zero-
/// weight channels (LFE) must be omitted by the caller, exactly as the
/// Python implementation skips them.
pub fn measure_integrated_loudness(channels: &[(f64, &[f64])], sample_rate: u32) -> f64 {
    let Some(power_blocks) = weighted_power_blocks(channels, sample_rate, BLOCK_S) else {
        return ABS_GATE;
    };
    let block_lkfs = block_loudness(&power_blocks);
    let gated = gated_indices(&power_blocks, &block_lkfs, REL_GATE_OFFSET);
    if gated.is_empty() {
        return ABS_GATE;
    }
    mean_loudness(&power_blocks, &gated)
}

/// Loudness statistics sharing one K-weighting pass over the programme.
#[derive(Clone, Copy, Debug)]
pub struct LoudnessStats {
    pub integrated_lkfs: f64,
    /// EBU Tech 3342 loudness range, in LU. `0.0` when the programme is
    /// shorter than one short-term window or never clears the gates.
    pub lra_lu: f64,
    /// Loudest 400 ms window (Tech 3341 momentary maximum), in LKFS.
    pub max_momentary_lkfs: f64,
    /// Loudest 3 s window (Tech 3341 short-term maximum), in LKFS.
    pub max_short_term_lkfs: f64,
}

/// EBU Tech 3342 loudness range: the 10th-to-95th percentile spread of the
/// short-term distribution, after the absolute and −20 LU relative gates.
fn loudness_range(power_blocks: &[f64], short_term: &[f64]) -> f64 {
    let gated = gated_indices(power_blocks, short_term, LRA_GATE_OFFSET);
    if gated.len() < 2 {
        return 0.0;
    }
    let mut kept: Vec<f64> = gated.iter().map(|&i| short_term[i]).collect();
    kept.sort_by(|a, b| a.partial_cmp(b).expect("gated loudness values are finite"));
    let pick = |p: f64| kept[((kept.len() - 1) as f64 * p).round() as usize];
    pick(LRA_HIGH_PERCENTILE) - pick(LRA_LOW_PERCENTILE)
}

/// Integrated loudness plus the LRA and momentary/short-term maxima.
pub fn measure_loudness_stats(channels: &[(f64, &[f64])], sample_rate: u32) -> LoudnessStats {
    let momentary_power = weighted_power_blocks(channels, sample_rate, BLOCK_S);
    let short_power = weighted_power_blocks(channels, sample_rate, SHORT_TERM_S);
    let momentary = momentary_power.as_deref().map(block_loudness).unwrap_or_default();
    let short_term = short_power.as_deref().map(block_loudness).unwrap_or_default();

    let integrated = match &momentary_power {
        Some(blocks) => {
            let gated = gated_indices(blocks, &momentary, REL_GATE_OFFSET);
            if gated.is_empty() { ABS_GATE } else { mean_loudness(blocks, &gated) }
        }
        None => ABS_GATE,
    };
    let max_of = |v: &[f64]| v.iter().fold(ABS_GATE, |m: f64, x| m.max(*x));
    LoudnessStats {
        integrated_lkfs: integrated,
        lra_lu: match &short_power {
            Some(blocks) => loudness_range(blocks, &short_term),
            None => 0.0,
        },
        max_momentary_lkfs: max_of(&momentary),
        max_short_term_lkfs: max_of(&short_term),
    }
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

/// Per-channel true peak in dBTP, in the order the channels were given.
pub fn measure_true_peak_per_channel(channels: &[&[f64]]) -> Vec<f64> {
    channels
        .iter()
        .map(|ch| 20.0 * true_peak_channel(ch).max(1e-30).log10())
        .collect()
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
