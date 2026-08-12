//! Linked-sidechain RMS bus compressor.
//!
//! One gain curve is derived from every non-LFE channel together and applied
//! uniformly, so surround imaging survives the stage. Profile values are
//! owned by `packages/core/src/mastering/compressor.py`.

use crate::kernels::biquad::lfilter;
use crate::kernels::sum::pairwise_sum;

use super::non_lfe;

#[derive(Clone, Copy, Debug)]
pub struct CompParams {
    pub threshold_db: f64,
    pub ratio: f64,
    pub attack_ms: f64,
    pub release_ms: f64,
    pub knee_db: f64,
    pub makeup_db: f64,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct CompInfo {
    pub max_gr_db: f64,
    pub avg_gr_db: f64,
}

/// Soft-knee gain computer: returns gain reduction in dB (≤ 0 before makeup).
fn gain_computer(env_db: f64, p: &CompParams) -> f64 {
    let (t, r, w) = (p.threshold_db, p.ratio, p.knee_db.max(0.0));
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

/// One-pole smoothing coefficient for a time constant in milliseconds.
fn alpha(ms: f64, sample_rate: u32) -> f64 {
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

    let mut x_sq = vec![0.0; n];
    for &i in &bed_idx {
        for (acc, v) in x_sq.iter_mut().zip(bed[i].iter()) {
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
        let gr_db = gain_computer(env_db, p);
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

#[cfg(test)]
mod tests {
    use super::*;

    fn params() -> CompParams {
        CompParams {
            threshold_db: -18.0,
            ratio: 2.0,
            attack_ms: 20.0,
            release_ms: 200.0,
            knee_db: 6.0,
            makeup_db: 0.0,
        }
    }

    #[test]
    fn gain_computer_is_continuous_across_the_knee() {
        let p = params();
        let lo = p.threshold_db - p.knee_db / 2.0;
        let hi = p.threshold_db + p.knee_db / 2.0;
        assert!(gain_computer(lo, &p).abs() < 1e-12);
        let inside = gain_computer(hi - 1e-9, &p);
        let outside = gain_computer(hi + 1e-9, &p);
        assert!((inside - outside).abs() < 1e-6, "{inside} vs {outside}");
    }

    #[test]
    fn a_sub_threshold_signal_is_untouched() {
        let quiet = vec![0.001; 4800];
        let mut bed = vec![quiet.clone(), quiet.clone()];
        bus_compress(&mut bed, None, 48_000, &params());
        for ch in &bed {
            for v in ch {
                assert!((v - 0.001).abs() < 1e-9);
            }
        }
    }

    #[test]
    fn lfe_bypasses_the_stage() {
        let loud = vec![0.9; 4800];
        let mut bed = vec![loud.clone(), loud.clone()];
        bus_compress(&mut bed, Some(1), 48_000, &params());
        assert_eq!(bed[1], loud);
        assert!(bed[0].last().unwrap() < &0.9);
    }

    #[test]
    fn unity_ratio_is_a_bypass() {
        let loud = vec![0.9; 128];
        let mut bed = vec![loud.clone()];
        let p = CompParams { ratio: 1.0, ..params() };
        bus_compress(&mut bed, None, 48_000, &p);
        assert_eq!(bed[0], loud);
    }
}
