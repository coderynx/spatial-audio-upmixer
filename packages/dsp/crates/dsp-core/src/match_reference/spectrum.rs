//! Gated, weighted power-spectrum estimation shared by the correction curve
//! and level matching.
//!
//! Channel weights are BS.1770's and are resolved by the caller — the core
//! never needs the channel-label table.

use crate::kernels::stft::{frame_frequencies, frame_power, FramePower};

/// Gate thresholds, owned by
/// `packages/core/src/mastering/match_reference/spectrum.py`.
#[derive(Clone, Copy, Debug)]
pub struct GateParams {
    pub absolute_db: f64,
    pub relative_offset_db: f64,
    pub epsilon: f64,
}

/// Two-stage absolute/relative energy gate over frames, mirroring BS.1770
/// §2.3's shape but applied to broadband STFT-frame energy so near-silent
/// frames do not bias the spectral average.
fn gate_mask(frame_energy_db: &[f64], gate: &GateParams) -> Vec<bool> {
    if frame_energy_db.is_empty() {
        return Vec::new();
    }
    let abs_mask: Vec<bool> = frame_energy_db.iter().map(|v| *v >= gate.absolute_db).collect();
    if !abs_mask.iter().any(|v| *v) {
        return vec![true; frame_energy_db.len()];
    }
    let kept: Vec<f64> = frame_energy_db
        .iter()
        .zip(abs_mask.iter())
        .filter(|(_, keep)| **keep)
        .map(|(v, _)| *v)
        .collect();
    let rel_ref = kept.iter().sum::<f64>() / kept.len() as f64;
    let rel_mask: Vec<bool> = frame_energy_db
        .iter()
        .zip(abs_mask.iter())
        .map(|(v, keep)| *keep && *v >= rel_ref + gate.relative_offset_db)
        .collect();
    if rel_mask.iter().any(|v| *v) {
        rel_mask
    } else {
        abs_mask
    }
}

/// Gated, weighted sum of per-channel power spectra, with the DC bin removed.
///
/// Channels with zero weight are excluded from both the gate and the sum.
/// The gate is computed once from the combined broadband energy, then applied
/// identically to every channel, so silence is judged across the programme
/// rather than per channel.
pub fn weighted_power_spectrum(
    arrays: &[&[f64]],
    weights: &[f64],
    sample_rate: u32,
    n_fft: usize,
    gate_params: &GateParams,
) -> (Vec<f64>, Vec<f64>) {
    let kept: Vec<(&[f64], f64)> = arrays
        .iter()
        .zip(weights.iter())
        .filter(|(_, w)| **w > 0.0)
        .map(|(a, w)| (*a, *w))
        .collect();
    assert!(!kept.is_empty(), "no channels with nonzero weight");

    let per_channel: Vec<(FramePower, f64)> = kept
        .iter()
        .map(|(audio, weight)| (frame_power(audio, n_fft), *weight))
        .collect();
    let freqs = frame_frequencies(kept[0].0.len(), n_fft, sample_rate);

    let n_frames = per_channel.iter().map(|(p, _)| p.n_frames).min().unwrap_or(0);
    if n_frames == 0 {
        return (Vec::new(), Vec::new());
    }
    let n_freqs = per_channel[0].0.n_freqs;

    let mut weighted_energy = vec![0.0; n_frames];
    for (power, weight) in &per_channel {
        for frame in 0..n_frames {
            let mut acc = 0.0;
            for freq in 0..power.n_freqs {
                acc += power.at(freq, frame);
            }
            weighted_energy[frame] += weight * acc;
        }
    }
    let gate_db: Vec<f64> = weighted_energy
        .iter()
        .map(|e| 10.0 * e.max(gate_params.epsilon).log10())
        .collect();
    let gate = gate_mask(&gate_db, gate_params);
    let gated: Vec<usize> = (0..n_frames).filter(|i| gate[*i]).collect();
    let selected = if gated.is_empty() { (0..n_frames).collect() } else { gated };

    let mut summed = vec![0.0; n_freqs];
    for (power, weight) in &per_channel {
        for (freq, out) in summed.iter_mut().enumerate() {
            let mean: f64 = selected.iter().map(|&f| power.at(freq, f)).sum::<f64>()
                / selected.len() as f64;
            *out += weight * mean;
        }
    }

    (freqs[1..].to_vec(), summed[1..].to_vec())
}
