//! Linked-sidechain RMS bus compressor.
//!
//! One gain curve is derived from every non-LFE channel together and applied
//! uniformly, so surround imaging survives the stage. Profile values are
//! owned by `packages/core/src/mastering/compressor.py`.

use crate::kernels::biquad::{lfilter, sosfilt};
use crate::kernels::butter::{butter_sos, BandType};
use crate::kernels::sum::pairwise_sum;

use super::non_lfe;

#[derive(Clone, Copy, Debug, PartialEq, serde::Deserialize)]
pub struct CompParams {
    pub threshold_db: f64,
    pub ratio: f64,
    pub attack_ms: f64,
    pub release_ms: f64,
    pub knee_db: f64,
    pub makeup_db: f64,
    /// High-pass on the detector only, so low frequencies stop driving gain
    /// reduction across the whole bed. `None` leaves the sidechain
    /// full-band. The bed sums N channels whose low end is correlated and
    /// whose mid/high is not, so LF is over-represented in the envelope by a
    /// factor that grows with the layout.
    pub sidechain_hpf_hz: Option<f64>,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct CompInfo {
    pub max_gr_db: f64,
    pub avg_gr_db: f64,
}

/// Soft-knee gain computer: returns gain reduction in dB (≤ 0 before makeup).
///
/// Shared with the streaming compressor, which differs only in how it
/// follows the envelope, and with the dynamic EQ, whose bands are the same
/// computer driven by a band-limited detector.
pub fn knee_gain_db(env_db: f64, threshold_db: f64, ratio: f64, knee_db: f64) -> f64 {
    let (t, r, w) = (threshold_db, ratio, knee_db.max(0.0));
    let output_db = if w > 0.0 {
        let knee_lo = t - w / 2.0;
        let knee_hi = t + w / 2.0;
        if env_db <= knee_lo {
            env_db
        } else if env_db >= knee_hi {
            t + (env_db - t) / r
        } else {
            env_db + ((1.0 / r - 1.0) * (env_db - knee_lo).powi(2)) / (2.0 * w)
        }
    } else if env_db <= t {
        env_db
    } else {
        t + (env_db - t) / r
    };
    output_db - env_db
}

pub fn gain_reduction_db(env_db: f64, p: &CompParams) -> f64 {
    knee_gain_db(env_db, p.threshold_db, p.ratio, p.knee_db)
}

/// One-pole smoothing coefficient for a time constant in milliseconds.
pub(super) fn alpha(ms: f64, sample_rate: u32) -> f64 {
    let dt = 1.0 / sample_rate as f64;
    1.0 - (-dt / (ms.max(0.01) / 1000.0)).exp()
}

/// Compress every channel except LFE against their shared sidechain.
pub fn bus_compress(
    bed: &mut super::Bed,
    lfe: Option<usize>,
    sample_rate: u32,
    p: &CompParams,
) -> CompInfo {
    let bed_idx = non_lfe(bed.len(), lfe);
    if bed_idx.is_empty() || p.ratio <= 1.0 {
        return CompInfo::default();
    }
    let n = bed_idx.iter().map(|&i| bed[i].len()).max().unwrap_or(0);
    if n == 0 {
        return CompInfo::default();
    }
    let n_ch = bed_idx.len() as f64;

    let nyq = sample_rate as f64 / 2.0;
    let sidechain_sos = p
        .sidechain_hpf_hz
        .map(|hz| butter_sos(2, (hz / nyq).clamp(1e-4, 0.999), BandType::High));

    let mut x_sq = vec![0.0; n];
    for &i in &bed_idx {
        let detector = sidechain_sos
            .as_ref()
            .map(|sos| sosfilt(sos, &bed[i]));
        let detector = detector.as_deref().unwrap_or(&bed[i]);
        for (acc, v) in x_sq.iter_mut().zip(detector.iter()) {
            *acc += v * v;
        }
    }
    let x_rms: Vec<f64> = x_sq.iter().map(|s| (s / n_ch + 1e-20).sqrt()).collect();

    let a_a = alpha(p.attack_ms, sample_rate);
    let a_r = alpha(p.release_ms, sample_rate);
    let fast = lfilter(&[a_a], &[1.0, -(1.0 - a_a)], &x_rms);
    let slow = lfilter(&[a_r], &[1.0, -(1.0 - a_r)], &x_rms);

    let mut gain_linear = Vec::with_capacity(n);
    let mut reductions = Vec::with_capacity(n);
    for (f, s) in fast.iter().zip(slow.iter()) {
        let env_db = 20.0 * f.max(*s).max(1e-20).log10();
        let gr_db = gain_reduction_db(env_db, p);
        reductions.push(gr_db.abs());
        gain_linear.push(10.0_f64.powf((gr_db + p.makeup_db) / 20.0));
    }

    for &i in &bed_idx {
        for (v, g) in bed[i].iter_mut().zip(gain_linear.iter()) {
            *v *= g;
        }
    }

    CompInfo {
        max_gr_db: reductions.iter().fold(0.0_f64, |m, v| m.max(*v)),
        avg_gr_db: pairwise_sum(&reductions) / reductions.len() as f64,
    }
}
