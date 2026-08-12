//! The reference-matching correction curve.
//!
//! Every threshold is a parameter: the analysis constants live with
//! `packages/core/src/mastering/match_reference/curve.py`, which is their
//! single source, and are passed down here.

use crate::kernels::fir_design::interp;

const EPS: f64 = 1e-20;

/// Bounds of the two band-edge taper ramps, in Hz.
#[derive(Clone, Copy, Debug)]
pub struct TaperBand {
    pub low_start: f64,
    pub low_end: f64,
    pub high_start: f64,
    pub high_end: f64,
}

/// Everything the curve algorithm needs beyond the two spectra.
#[derive(Clone, Copy, Debug)]
pub struct CurveParams {
    pub min_freq_hz: f64,
    pub max_freq_hz: f64,
    pub grid_step_oct: f64,
    pub smooth_sigma_oct: f64,
    pub norm_low_hz: f64,
    pub norm_high_hz: f64,
    pub confidence_floor_db: f64,
    pub taper: TaperBand,
    pub n_breakpoints: usize,
}

fn linspace(start: f64, stop: f64, n: usize) -> Vec<f64> {
    if n <= 1 {
        return vec![start];
    }
    let step = (stop - start) / (n - 1) as f64;
    let mut out: Vec<f64> = (0..n).map(|i| start + step * i as f64).collect();
    out[n - 1] = stop;
    out
}

/// Frequencies uniform in log2, from `min_freq_hz` up to `high_hz`.
pub fn log_grid(high_hz: f64, min_freq_hz: f64, step_oct: f64) -> Vec<f64> {
    let lo = min_freq_hz.log2();
    let hi = high_hz.max(min_freq_hz * 2.0).log2();
    let n = ((((hi - lo) / step_oct).round() as i64) + 1).max(2) as usize;
    linspace(lo, hi, n).iter().map(|v| v.exp2()).collect()
}

/// `numpy.pad(values, w, mode="reflect")` — mirrors without repeating the edge.
fn reflect_pad(values: &[f64], width: usize) -> Vec<f64> {
    let n = values.len();
    let mut out = Vec::with_capacity(n + 2 * width);
    for k in 0..width {
        out.push(values[(width - k).min(n - 1)]);
    }
    out.extend_from_slice(values);
    for m in 0..width {
        out.push(values[n.saturating_sub(2 + m).min(n - 1)]);
    }
    out
}

/// Gaussian smoothing on a grid uniform in log-frequency, so `sigma_oct` is
/// the true width in octaves.
pub fn smooth_log_grid(values: &[f64], sigma_oct: f64, step_oct: f64) -> Vec<f64> {
    if values.is_empty() {
        return Vec::new();
    }
    let sigma_bins = sigma_oct / step_oct;
    let half_w = (3.0 * sigma_bins) as usize + 1;
    let kernel: Vec<f64> = (0..2 * half_w + 1)
        .map(|i| {
            let idx = i as f64 - half_w as f64;
            (-0.5 * (idx / sigma_bins).powi(2)).exp()
        })
        .collect();
    let norm: f64 = kernel.iter().sum();
    let kernel: Vec<f64> = kernel.iter().map(|v| v / norm).collect();

    let padded = reflect_pad(values, half_w);
    (0..values.len())
        .map(|i| kernel.iter().enumerate().map(|(j, k)| padded[i + j] * k).sum())
        .collect()
}

/// Fade correction to 0 dB where the reference sits far below its own peak,
/// so a curve is never extrapolated from near-nothing.
pub fn confidence_taper(correction_db: &[f64], ref_power_db: &[f64], floor_db: f64) -> Vec<f64> {
    let peak = ref_power_db.iter().fold(f64::NEG_INFINITY, |m, v| m.max(*v));
    correction_db
        .iter()
        .zip(ref_power_db.iter())
        .map(|(c, r)| {
            let deficit = (peak - floor_db) - r;
            c * (1.0 - deficit / floor_db).clamp(0.0, 1.0)
        })
        .collect()
}

/// Hard-taper to 0 dB outside the band the analysis trusts.
pub fn band_edge_taper(correction_db: &[f64], freqs: &[f64], taper: &TaperBand) -> Vec<f64> {
    correction_db
        .iter()
        .zip(freqs.iter())
        .map(|(c, f)| {
            let mut gain = 1.0;
            if *f < taper.low_end {
                gain = ((f - taper.low_start) / (taper.low_end - taper.low_start)).clamp(0.0, 1.0);
            }
            if *f > taper.high_start {
                gain =
                    ((taper.high_end - f) / (taper.high_end - taper.high_start)).clamp(0.0, 1.0);
            }
            c * gain
        })
        .collect()
}

/// Clamp magnitude to `limit_db` with a soft knee, so the curve never
/// develops a hard corner at the ceiling.
pub fn soft_clamp(db: &[f64], limit_db: f64, knee_db: f64) -> Vec<f64> {
    if limit_db <= 0.0 {
        return vec![0.0; db.len()];
    }
    let knee_start = (limit_db - knee_db).max(0.0);
    let knee_width = (limit_db - knee_start).max(1e-6);
    db.iter()
        .map(|v| {
            let magnitude = v.abs();
            if magnitude <= knee_start {
                return *v;
            }
            let over = (magnitude - knee_start).max(0.0);
            let compressed = knee_start + knee_width * (over / knee_width).tanh();
            // numpy's sign(0) is 0; magnitude > knee_start >= 0 rules that out.
            v.signum() * compressed
        })
        .collect()
}

/// The strength- and clamp-independent correction curve, as
/// `(frequency_hz, gain_db)` breakpoints.
///
/// `power_t` must already be level-matched to `power_r`; otherwise a residual
/// broadband offset ends up baked into the per-band curve instead of staying
/// cleanly separated as a level gain.
pub fn correction_curve(
    freqs_t: &[f64],
    power_t: &[f64],
    freqs_r: &[f64],
    power_r: &[f64],
    sample_rate: u32,
    p: &CurveParams,
) -> Vec<(f64, f64)> {
    let nyquist = sample_rate as f64 / 2.0;
    let grid = log_grid(p.max_freq_hz.min(nyquist), p.min_freq_hz, p.grid_step_oct);
    let log_grid_values: Vec<f64> = grid.iter().map(|f| f.log2()).collect();

    let log_t: Vec<f64> = freqs_t.iter().map(|f| f.log2()).collect();
    let log_r: Vec<f64> = freqs_r.iter().map(|f| f.log2()).collect();
    let power_t_grid: Vec<f64> = log_grid_values.iter().map(|x| interp(*x, &log_t, power_t)).collect();
    let power_r_grid: Vec<f64> = log_grid_values.iter().map(|x| interp(*x, &log_r, power_r)).collect();

    let mut correction_db: Vec<f64> = power_r_grid
        .iter()
        .zip(power_t_grid.iter())
        .map(|(r, t)| 10.0 * ((r + EPS) / (t + EPS)).log10())
        .collect();
    correction_db = smooth_log_grid(&correction_db, p.smooth_sigma_oct, p.grid_step_oct);

    let norm: Vec<f64> = grid
        .iter()
        .zip(correction_db.iter())
        .filter(|(f, _)| **f >= p.norm_low_hz && **f <= p.norm_high_hz)
        .map(|(_, c)| *c)
        .collect();
    if !norm.is_empty() {
        let mean = norm.iter().sum::<f64>() / norm.len() as f64;
        for c in correction_db.iter_mut() {
            *c -= mean;
        }
    }

    let ref_power_db: Vec<f64> = power_r_grid.iter().map(|v| 10.0 * (v + EPS).log10()).collect();
    correction_db = confidence_taper(&correction_db, &ref_power_db, p.confidence_floor_db);
    correction_db = band_edge_taper(&correction_db, &grid, &p.taper);

    let bp_high = p.max_freq_hz.min(nyquist);
    let bp_freqs: Vec<f64> = linspace(p.min_freq_hz.log10(), bp_high.log10(), p.n_breakpoints)
        .iter()
        .map(|v| 10.0_f64.powf(*v))
        .collect();
    bp_freqs
        .iter()
        .map(|f| (*f, interp(f.log2(), &log_grid_values, &correction_db)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn taper() -> TaperBand {
        TaperBand { low_start: 20.0, low_end: 25.0, high_start: 18000.0, high_end: 20000.0 }
    }

    #[test]
    fn log_grid_is_uniform_in_octaves() {
        let grid = log_grid(20_000.0, 20.0, 1.0 / 24.0);
        let steps: Vec<f64> = grid.windows(2).map(|w| (w[1] / w[0]).log2()).collect();
        for s in &steps {
            assert!((s - steps[0]).abs() < 1e-12);
        }
        assert!((grid[0] - 20.0).abs() < 1e-12);
        assert!((grid[grid.len() - 1] - 20_000.0).abs() < 1e-9);
    }

    #[test]
    fn smoothing_preserves_a_constant() {
        let values = vec![3.0; 200];
        for v in smooth_log_grid(&values, 1.0 / 3.0, 1.0 / 24.0) {
            assert!((v - 3.0).abs() < 1e-12);
        }
    }

    #[test]
    fn smoothing_actually_smooths_at_the_requested_width() {
        let mut spike = vec![0.0; 201];
        spike[100] = 1.0;
        let out = smooth_log_grid(&spike, 1.0 / 3.0, 1.0 / 24.0);
        // A real 1/3-octave kernel spans 8 bins of sigma, so the spike must
        // spread far beyond the near-identity 3-tap kernel of the old bug.
        assert!(out[100] < 0.06, "peak {} is too sharp", out[100]);
        assert!(out[92] > 0.01, "kernel is too narrow");
    }

    #[test]
    fn band_edge_taper_zeroes_the_extremes() {
        let freqs = [10.0, 25.0, 1000.0, 19_000.0, 21_000.0];
        let out = band_edge_taper(&[1.0; 5], &freqs, &taper());
        assert_eq!(out[0], 0.0);
        assert!((out[2] - 1.0).abs() < 1e-12);
        assert!(out[3] > 0.0 && out[3] < 1.0);
        assert_eq!(out[4], 0.0);
    }

    #[test]
    fn soft_clamp_bounds_magnitude_and_keeps_small_values_exact() {
        let out = soft_clamp(&[0.5, -0.5, 20.0, -20.0], 6.0, 2.0);
        assert_eq!(out[0], 0.5);
        assert_eq!(out[1], -0.5);
        assert!(out[2] <= 6.0 && out[2] > 4.0);
        assert!((out[2] + out[3]).abs() < 1e-15);
    }

    #[test]
    fn confidence_taper_silences_correction_where_the_reference_is_empty() {
        let correction = [6.0, 6.0];
        let ref_db = [0.0, -100.0];
        let out = confidence_taper(&correction, &ref_db, 40.0);
        assert!((out[0] - 6.0).abs() < 1e-12);
        assert_eq!(out[1], 0.0);
    }
}
