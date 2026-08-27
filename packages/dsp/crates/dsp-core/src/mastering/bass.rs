//! Multichannel low-end control: band gains, LF unification with
//! redistribution, transient shaping, harmonic excitation, mid-bass
//! decorrelation, LFE trim. Profile values, the spread tables and the
//! target-weight arithmetic are owned by
//! `packages/core/src/mastering/bass.py`.

use crate::kernels::biquad::sosfilt;
use crate::kernels::butter::{butter_sos, BandType};
use crate::kernels::filtfilt::sosfiltfilt;

use super::compressor::alpha;
use super::non_lfe;

/// Below this length the zero-phase pass is skipped for a single forward
/// pass, matching `BassController.process`.
const UNIFY_FILTFILT_MIN_LEN: usize = 15;

/// Keeps the punch shaper's gain at unity while both envelopes are still
/// converging from silence, instead of `0/0`.
const PUNCH_EPS: f64 = 1e-9;

#[derive(Clone, Copy, Debug, PartialEq, serde::Deserialize)]
pub struct BassParams {
    pub sub_gain_db: f64,
    pub mid_gain_db: f64,
    pub unify_hz: Option<f64>,
    pub punch: f64,
    pub excite: bool,
    pub lfe_gain_db: f64,
    pub sub_cutoff_hz: f64,
    pub mid_cutoff_hz: f64,
    pub excite_blend: f64,
    pub excite_drive: f64,
    pub punch_fast_ms: f64,
    pub punch_slow_ms: f64,
    pub punch_max_db: f64,
    pub decorrelate: f64,
    pub decorr_low_hz: f64,
    pub decorr_high_hz: f64,
    pub decorr_sections: usize,
    pub decorr_max_delay_ms: f64,
    pub decorr_fast_ms: f64,
    pub decorr_slow_ms: f64,
}

/// Boost or cut a band additively: `out = (x - band) + band * gain`.
///
/// Deliberately not a shelving/peaking biquad — the additive identity is what
/// the preview had to reproduce to stop the mono-maker flipping sign
/// (ledger D9).
fn apply_band_gain(
    channel: &[f64],
    gain_linear: f64,
    sos_lp: &[[f64; 6]],
    sos_hp: Option<&[[f64; 6]]>,
) -> Vec<f64> {
    let mut band = sosfilt(sos_lp, channel);
    if let Some(hp) = sos_hp {
        band = sosfilt(hp, &band);
    }
    channel
        .iter()
        .zip(band.iter())
        .map(|(x, b)| (x - b) + b * gain_linear)
        .collect()
}

/// A band taken zero-phase, so the complement `x - band` stays time-aligned.
pub fn zero_phase(sos: &[[f64; 6]], x: &[f64]) -> Vec<f64> {
    if x.len() > UNIFY_FILTFILT_MIN_LEN {
        sosfiltfilt(sos, x).unwrap_or_else(|| sosfilt(sos, x))
    } else {
        sosfilt(sos, x)
    }
}

/// Transient shaper on the mono LF bus, carrying its envelopes so streaming
/// can advance them across block boundaries.
///
/// `punch > 0` opens the gap between a fast and a slow envelope, so attacks
/// survive and the sustain between them drops; `punch < 0` closes it, which
/// densifies. iZotope's Punchy/Smooth switch as one signed control.
#[derive(Clone, Copy, Debug, Default)]
pub struct PunchState {
    fast: f64,
    slow: f64,
}

impl PunchState {
    pub fn run(&mut self, bus: &mut [f64], sample_rate: u32, p: &BassParams) {
        if p.punch == 0.0 {
            return;
        }
        let a_fast = alpha(p.punch_fast_ms, sample_rate);
        let a_slow = alpha(p.punch_slow_ms, sample_rate);
        let ceiling = 10.0_f64.powf(p.punch_max_db / 20.0);
        for v in bus.iter_mut() {
            let level = v.abs();
            self.fast += a_fast * (level - self.fast);
            self.slow += a_slow * (level - self.slow);
            let ratio = (self.fast + PUNCH_EPS) / (self.slow + PUNCH_EPS);
            *v *= ratio.powf(p.punch).clamp(1.0 / ceiling, ceiling);
        }
    }
}

/// Harmonics the exciter derives from the LF bus.
///
/// Kept off the LFE by the caller: tanh's third and fifth land at 3x and 5x
/// the fundamental, above the 120 Hz the channel is band-limited to.
pub(crate) fn excite_harmonics(bus: &[f64], p: &BassParams) -> Vec<f64> {
    bus.iter()
        .map(|v| (v * p.excite_drive).tanh() * p.excite_blend)
        .collect()
}

/// Collapse the low band of every non-LFE channel into one bus and hand it
/// back out over `lf_targets`.
///
/// The complement is taken by subtraction rather than by a second filter, so
/// it is exact for any low-pass — an LR4 pair would not sum flat once run
/// zero-phase. Weights are resolved by the caller and normally sum to 1,
/// which is what keeps the coherent low-frequency level unchanged.
fn lf_unify(
    bed: &mut super::Bed,
    lfe: Option<usize>,
    lf_targets: &[(usize, f64)],
    bed_idx: &[usize],
    sample_rate: u32,
    unify_hz: f64,
    p: &BassParams,
) {
    let nyq = sample_rate as f64 / 2.0;
    let sos = butter_sos(2, (unify_hz / nyq).clamp(1e-4, 0.999), BandType::Low);

    let lows: Vec<Vec<f64>> = bed_idx.iter().map(|&i| zero_phase(&sos, &bed[i])).collect();
    let n = lows.iter().map(|l| l.len()).max().unwrap_or(0);
    if n == 0 {
        return;
    }

    let mut bus = vec![0.0; n];
    for low in &lows {
        for (acc, v) in bus.iter_mut().zip(low.iter()) {
            *acc += v;
        }
    }
    PunchState::default().run(&mut bus, sample_rate, p);

    for (&i, low) in bed_idx.iter().zip(lows.iter()) {
        for (v, l) in bed[i].iter_mut().zip(low.iter()) {
            *v -= l;
        }
    }

    let harmonics = p.excite.then(|| excite_harmonics(&bus, p));
    for &(i, weight) in lf_targets {
        if i >= bed.len() || weight == 0.0 {
            continue;
        }
        let harmonics = harmonics.as_ref().filter(|_| Some(i) != lfe);
        for (k, v) in bed[i].iter_mut().enumerate().take(n) {
            let h = harmonics.map_or(0.0, |h| h[k]);
            *v += (bus[k] + h) * weight;
        }
    }
}

/// Apply the low-end chain in place.
///
/// `lf_targets` carries `(channel index, weight)` pairs resolved by the
/// caller from the layout and the LFE mode, so the core never needs to know
/// channel names.
pub fn bass_control(
    bed: &mut super::Bed,
    lfe: Option<usize>,
    lf_targets: &[(usize, f64)],
    sample_rate: u32,
    p: &BassParams,
) {
    let spatial_channels = bed.len();
    bass_control_sources(bed, lfe, spatial_channels, lf_targets, sample_rate, p);
}

pub fn bass_control_sources(
    bed: &mut super::Bed,
    lfe: Option<usize>,
    spatial_channels: usize,
    lf_targets: &[(usize, f64)],
    sample_rate: u32,
    p: &BassParams,
) {
    let nyq = sample_rate as f64 / 2.0;
    let all_idx = non_lfe(bed.len(), lfe);
    let bed_idx = non_lfe(spatial_channels.min(bed.len()), lfe);

    if !all_idx.is_empty() && (p.sub_gain_db != 0.0 || p.mid_gain_db != 0.0) {
        let sos_sub_lp = butter_sos(2, p.sub_cutoff_hz / nyq, BandType::Low);
        let sos_mid_lp = butter_sos(2, p.mid_cutoff_hz / nyq, BandType::Low);
        let sos_mid_hp = butter_sos(2, p.sub_cutoff_hz / nyq, BandType::High);
        let sub_lin = 10.0_f64.powf(p.sub_gain_db / 20.0);
        let mid_lin = 10.0_f64.powf(p.mid_gain_db / 20.0);
        for &i in &all_idx {
            let mut shaped = std::mem::take(&mut bed[i]);
            if p.sub_gain_db != 0.0 {
                shaped = apply_band_gain(&shaped, sub_lin, &sos_sub_lp, None);
            }
            if p.mid_gain_db != 0.0 {
                shaped = apply_band_gain(&shaped, mid_lin, &sos_mid_lp, Some(&sos_mid_hp));
            }
            bed[i] = shaped;
        }
    }

    // Taken before unification, which is what lets the streaming mirror read
    // its look-ahead out of the same pre-unification queue. The two bands are
    // disjoint — the decorrelator's low corner is clamped up to `unify_hz` —
    // so nothing the unifier does belongs in this band anyway.
    let bands = super::decorrelate::band_sos(sample_rate, p).map(|sos| {
        bed_idx
            .iter()
            .map(|&i| zero_phase(&sos, &bed[i]))
            .collect::<Vec<_>>()
    });

    if let Some(unify_hz) = p.unify_hz {
        if !bed_idx.is_empty() && !lf_targets.is_empty() {
            lf_unify(bed, lfe, lf_targets, &bed_idx, sample_rate, unify_hz, p);
        }
    }

    if let Some(bands) = bands {
        for (&i, band) in bed_idx.iter().zip(bands.iter()) {
            super::decorrelate::Decorrelator::new(i, sample_rate, p).run(&mut bed[i], band);
        }
    }

    if p.lfe_gain_db != 0.0 {
        if let Some(i) = lfe {
            let g = 10.0_f64.powf(p.lfe_gain_db / 20.0);
            for v in bed[i].iter_mut() {
                *v *= g;
            }
        }
    }
}
