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
    pub norm_low_hz: f64,
    pub norm_high_hz: f64,
    pub confidence_floor_db: f64,
    pub taper: TaperBand,
}

/// Everything turning a stored curve into FIR breakpoints needs.
#[derive(Clone, Copy, Debug)]
pub struct RealizeParams {
    pub strength: f64,
    pub max_correction_db: f64,
    pub clamp_knee_db: f64,
    pub smooth_oct: f64,
    pub grid_step_oct: f64,
    pub low_hz: f64,
    pub high_hz: f64,
    pub mask_ease_oct: f64,
    pub bass_clamp_hz: f64,
    pub bass_clamp_db: f64,
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
        .map(|i| {
            kernel
                .iter()
                .enumerate()
                .map(|(j, k)| padded[i + j] * k)
                .sum()
        })
        .collect()
}

/// Fade correction to 0 dB where the reference sits far below its own peak,
/// so a curve is never extrapolated from near-nothing.
pub fn confidence_taper(correction_db: &[f64], ref_power_db: &[f64], floor_db: f64) -> Vec<f64> {
    let peak = ref_power_db
        .iter()
        .fold(f64::NEG_INFINITY, |m, v| m.max(*v));
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
                gain = ((taper.high_end - f) / (taper.high_end - taper.high_start)).clamp(0.0, 1.0);
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

/// Raised-cosine easing to unity gain outside `[low_hz, high_hz]`, spanning
/// `ease_oct` octaves either side. A bound at or below zero leaves that side
/// unmasked.
pub fn range_mask(freqs: &[f64], low_hz: f64, high_hz: f64, ease_oct: f64) -> Vec<f64> {
    let ease = ease_oct.max(1e-9);
    let ramp = |t: f64| 0.5 - 0.5 * (std::f64::consts::PI * t.clamp(0.0, 1.0)).cos();
    freqs
        .iter()
        .map(|f| {
            let oct = f.log2();
            let mut gain = 1.0_f64;
            if low_hz > 0.0 {
                gain = gain.min(ramp((oct - (low_hz.log2() - ease)) / ease));
            }
            if high_hz > 0.0 {
                gain = gain.min(ramp(((high_hz.log2() + ease) - oct) / ease));
            }
            gain
        })
        .collect()
}

/// Realize a stored correction curve at one set of user controls: strength
/// scaling, smoothing, range masking, then the two clamps.
///
/// The stored curve is raw — smoothing happens here, so changing any control
/// re-designs the FIR without re-running the spectral analysis.
pub fn realize_curve(freqs: &[f64], correction_db: &[f64], p: &RealizeParams) -> Vec<f64> {
    let scaled: Vec<f64> = correction_db.iter().map(|v| v * p.strength).collect();
    let mut out = smooth_log_grid(&scaled, p.smooth_oct, p.grid_step_oct);
    for (v, m) in out
        .iter_mut()
        .zip(range_mask(freqs, p.low_hz, p.high_hz, p.mask_ease_oct))
    {
        *v *= m;
    }
    out = soft_clamp(&out, p.max_correction_db, p.clamp_knee_db);
    for (v, f) in out.iter_mut().zip(freqs.iter()) {
        if *f < p.bass_clamp_hz {
            *v = v.clamp(-p.bass_clamp_db, p.bass_clamp_db);
        }
    }
    out
}

/// The control-independent correction curve, as `(frequency_hz, gain_db)`
/// pairs on the log-frequency grid. Unsmoothed: [`realize_curve`] owns every
/// user control, so none of them forces a re-analysis.
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
    let power_t_grid: Vec<f64> = log_grid_values
        .iter()
        .map(|x| interp(*x, &log_t, power_t))
        .collect();
    let power_r_grid: Vec<f64> = log_grid_values
        .iter()
        .map(|x| interp(*x, &log_r, power_r))
        .collect();

    let mut correction_db: Vec<f64> = power_r_grid
        .iter()
        .zip(power_t_grid.iter())
        .map(|(r, t)| 10.0 * ((r + EPS) / (t + EPS)).log10())
        .collect();

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

    let ref_power_db: Vec<f64> = power_r_grid
        .iter()
        .map(|v| 10.0 * (v + EPS).log10())
        .collect();
    correction_db = confidence_taper(&correction_db, &ref_power_db, p.confidence_floor_db);
    correction_db = band_edge_taper(&correction_db, &grid, &p.taper);

    grid.into_iter().zip(correction_db).collect()
}
