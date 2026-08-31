//! Minimum-phase FIR spectral shaping.
//!
//! Profile breakpoints are owned by `packages/core/src/mastering/eq.py` and
//! passed in; this module only turns them into taps and convolves.

use crate::kernels::fft::fftconvolve;
use crate::kernels::fir_design::{firwin2, minimum_phase};

use super::non_lfe;

/// Turn `(frequency_hz, gain_db)` breakpoints into the normalized frequency
/// and linear gain vectors `firwin2` expects: endpoints are extended to DC
/// and Nyquist and duplicate frequencies are dropped, matching
/// `_build_fir_from_breakpoints`.
pub fn normalize_breakpoints(breakpoints: &[(f64, f64)], sample_rate: u32) -> (Vec<f64>, Vec<f64>) {
    assert!(!breakpoints.is_empty(), "EQ needs at least one breakpoint");
    let nyquist = sample_rate as f64 / 2.0;
    let mut freqs: Vec<f64> = breakpoints.iter().map(|(f, _)| f / nyquist).collect();
    let mut gains: Vec<f64> = breakpoints
        .iter()
        .map(|(_, g)| 10.0_f64.powf(g / 20.0))
        .collect();

    if freqs[0] > 0.0 {
        freqs.insert(0, 0.0);
        gains.insert(0, gains[0]);
    }
    for f in freqs.iter_mut() {
        *f = f.min(1.0);
    }
    if *freqs.last().expect("non-empty") < 1.0 {
        freqs.push(1.0);
        gains.push(*gains.last().expect("non-empty"));
    }

    let mut seen: Vec<f64> = Vec::new();
    let mut out_f = Vec::with_capacity(freqs.len());
    let mut out_g = Vec::with_capacity(gains.len());
    for (f, g) in freqs.iter().zip(gains.iter()) {
        let rounded = (f * 1e9).round() / 1e9;
        if !seen.contains(&rounded) {
            seen.push(rounded);
            out_f.push(*f);
            out_g.push(*g);
        }
    }
    (out_f, out_g)
}

/// Design the minimum-phase FIR for a breakpoint curve.
///
/// `half = false` in the underlying `minimum_phase` call keeps the full
/// length so the breakpoint dB values stay exact rather than being square-
/// rooted.
pub fn build_fir(breakpoints: &[(f64, f64)], sample_rate: u32, n_taps: usize) -> Vec<f64> {
    let (freqs, gains) = normalize_breakpoints(breakpoints, sample_rate);
    minimum_phase(&firwin2(n_taps, &freqs, &gains))
}

/// Convolve one channel with `ir`, trimmed to the input length, then blend
/// wet against dry.
pub fn apply_fir(channel: &[f64], ir: &[f64], strength: f64) -> Vec<f64> {
    let mut filtered = fftconvolve(channel, ir);
    filtered.truncate(channel.len());
    if strength >= 1.0 {
        return filtered;
    }
    channel
        .iter()
        .zip(filtered.iter())
        .map(|(dry, wet)| (1.0 - strength) * dry + strength * wet)
        .collect()
}

/// Apply the shaper to every channel except LFE.
pub fn spectral_shape(bed: &mut super::Bed, lfe: Option<usize>, ir: &[f64], strength: f64) {
    if strength == 0.0 {
        return;
    }
    for i in non_lfe(bed.len(), lfe) {
        bed[i] = apply_fir(&bed[i], ir, strength);
    }
}
