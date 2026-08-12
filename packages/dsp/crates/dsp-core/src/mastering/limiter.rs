//! Linked look-ahead true-peak limiter.
//!
//! Detection reuses the BS.1770 4x interpolator, so the limiter reacts to
//! genuine inter-sample peaks. Offline it introduces no output latency: the
//! forward-window minimum is taken directly against already-available future
//! samples, which is exactly what delaying and trimming would produce.

use crate::kernels::biquad::lfilter;
use crate::kernels::minfilter::{minimum_filter1d, BorderMode};
use crate::kernels::upfirdn::upfirdn_up;
use crate::loudness::{TRUE_PEAK_FIR_4X, TRUE_PEAK_OVERSAMPLE};

/// Group delay of the symmetric detector FIR, in oversampled samples.
pub const FIR_DELAY: usize = (TRUE_PEAK_FIR_4X.len() - 1) / 2;
/// Half-width of the detector's support in base-rate samples. Gain reduction
/// is held constant across at least this span so a reduced sample and an
/// untouched neighbour cannot recombine into a fresh inter-sample peak.
pub const FIR_MARGIN_SAMPLES: usize = FIR_DELAY.div_ceil(TRUE_PEAK_OVERSAMPLE);

#[derive(Clone, Copy, Debug)]
pub struct LimiterParams {
    pub ceiling_dbtp: f64,
    pub lookahead_ms: f64,
    pub release_ms: f64,
    /// Extra headroom folded into the gain computation only; the limiter
    /// still targets the nominal ceiling.
    pub safety_margin_db: f64,
}

/// `result[n] = min(values[n .. n + window])`, treating positions past the
/// end as 1.0 — there is no more signal to protect against.
fn forward_window_min(values: &[f64], window: usize) -> Vec<f64> {
    if window <= 1 {
        return values.to_vec();
    }
    let window = if window % 2 == 0 { window + 1 } else { window };
    let mut padded = values.to_vec();
    padded.extend(std::iter::repeat_n(1.0, window - 1));
    let filtered = minimum_filter1d(&padded, window, BorderMode::Reflect);
    let half = (window - 1) / 2;
    filtered[half..half + values.len()].to_vec()
}

/// Apply the limiter to every channel, LFE included, and return the peak
/// gain reduction in dB.
pub fn lookahead_limit(bed: &mut super::Bed, sample_rate: u32, p: &LimiterParams) -> f64 {
    let n = bed.iter().map(|c| c.len()).max().unwrap_or(0);
    if n == 0 {
        return 0.0;
    }
    let over_sr = sample_rate as f64 * TRUE_PEAK_OVERSAMPLE as f64;
    let ceiling_linear = 10.0_f64.powf((p.ceiling_dbtp - p.safety_margin_db) / 20.0);

    let mut envelope: Vec<f64> = vec![0.0; n * TRUE_PEAK_OVERSAMPLE];
    for channel in bed.iter() {
        let length = channel.len().min(n);
        if length == 0 {
            continue;
        }
        let upsampled = upfirdn_up(&TRUE_PEAK_FIR_4X, &channel[..length], TRUE_PEAK_OVERSAMPLE);
        let span = length * TRUE_PEAK_OVERSAMPLE;
        for (dst, src) in envelope[..span].iter_mut().zip(&upsampled[FIR_DELAY..FIR_DELAY + span]) {
            *dst = (*dst).max(src.abs());
        }
    }

    let gain_inst: Vec<f64> = envelope
        .iter()
        .map(|e| (ceiling_linear / (*e).max(1e-12)).min(1.0))
        .collect();
    let lookahead_samples = ((p.lookahead_ms / 1000.0 * over_sr).round() as usize).max(1);
    let gain_lookahead = forward_window_min(&gain_inst, lookahead_samples);

    let need_db: Vec<f64> = gain_lookahead
        .iter()
        .map(|g| -20.0 * (*g).max(1e-12).log10())
        .collect();
    let alpha_release = 1.0 - (-1.0 / (p.release_ms.max(0.01) / 1000.0 * over_sr)).exp();
    let slow = lfilter(&[alpha_release], &[1.0, -(1.0 - alpha_release)], &need_db);
    let need_smoothed: Vec<f64> = need_db
        .iter()
        .zip(slow.iter())
        .map(|(a, b)| a.max(*b))
        .collect();

    let gain_base: Vec<f64> = need_smoothed
        .chunks_exact(TRUE_PEAK_OVERSAMPLE)
        .map(|c| {
            let worst = c.iter().fold(f64::NEG_INFINITY, |m, v| m.max(*v));
            10.0_f64.powf(-worst / 20.0)
        })
        .collect();
    let dilated = minimum_filter1d(&gain_base, 2 * FIR_MARGIN_SAMPLES + 1, BorderMode::Nearest);

    for channel in bed.iter_mut() {
        for (v, g) in channel.iter_mut().zip(dilated.iter()) {
            *v *= g;
        }
    }

    need_smoothed.iter().fold(0.0_f64, |m, v| m.max(*v))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::loudness::measure_true_peak;

    fn params() -> LimiterParams {
        LimiterParams {
            ceiling_dbtp: -1.0,
            lookahead_ms: 5.0,
            release_ms: 50.0,
            safety_margin_db: 0.1,
        }
    }

    #[test]
    fn forward_window_min_matches_a_brute_force_reference() {
        let values: Vec<f64> = (0..200).map(|i| ((i * 17) % 13) as f64 * 0.05 + 0.1).collect();
        let window = 9;
        let got = forward_window_min(&values, window);
        for i in 0..values.len() {
            let end = (i + window).min(values.len());
            let mut want = 1.0_f64;
            for v in &values[i..end] {
                want = want.min(*v);
            }
            assert!((got[i] - want).abs() < 1e-15, "index {i}: {} vs {want}", got[i]);
        }
    }

    #[test]
    fn quiet_material_passes_through_untouched() {
        let quiet: Vec<f64> = (0..9600).map(|i| 0.05 * (i as f64 * 0.1).sin()).collect();
        let mut bed = vec![quiet.clone()];
        let gr = lookahead_limit(&mut bed, 48_000, &params());
        assert!(gr < 1e-9, "unexpected gain reduction {gr} dB");
        for (a, b) in bed[0].iter().zip(quiet.iter()) {
            assert!((a - b).abs() < 1e-12);
        }
    }

    #[test]
    fn output_respects_the_true_peak_ceiling() {
        let sr = 48_000;
        let loud: Vec<f64> = (0..sr)
            .map(|i| {
                let t = i as f64 / sr as f64;
                0.98 * (2.0 * std::f64::consts::PI * 997.0 * t).sin()
                    * (1.0 + 0.5 * (2.0 * std::f64::consts::PI * 3.0 * t).sin())
            })
            .collect();
        let mut bed = vec![loud.clone(), loud.iter().map(|v| v * 0.8).collect()];
        let gr = lookahead_limit(&mut bed, sr as u32, &params());
        assert!(gr > 0.0, "expected the limiter to engage");
        let refs: Vec<&[f64]> = bed.iter().map(|c| c.as_slice()).collect();
        let dbtp = measure_true_peak(&refs);
        assert!(dbtp <= -1.0 + 1e-6, "true peak {dbtp} dBTP exceeds the ceiling");
    }

    #[test]
    fn every_channel_gets_the_same_gain_curve() {
        let sr = 48_000;
        let loud: Vec<f64> = (0..sr).map(|i| 0.95 * (i as f64 * 0.05).sin()).collect();
        let quiet: Vec<f64> = loud.iter().map(|v| v * 0.25).collect();
        let mut bed = vec![loud.clone(), quiet.clone()];
        lookahead_limit(&mut bed, sr as u32, &params());
        for i in 0..loud.len() {
            if loud[i].abs() > 1e-9 {
                let ratio_a = bed[0][i] / loud[i];
                let ratio_b = bed[1][i] / quiet[i];
                assert!((ratio_a - ratio_b).abs() < 1e-12, "sample {i}");
            }
        }
    }
}
