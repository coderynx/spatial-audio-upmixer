//! Stateful forms of the stages the offline chain runs whole-buffer.
//!
//! Each type carries exactly the state its offline counterpart would have
//! accumulated, so streaming a signal through in blocks and running it
//! offline give the same samples.

use crate::kernels::biquad::SosFilter;
use crate::kernels::filtfilt::sosfiltfilt;
use crate::mastering::compressor::CompParams;

/// A one-pole `lfilter([a], [1, -(1-a)], x)` that survives across blocks.
#[derive(Clone, Copy, Debug)]
pub struct OnePole {
    alpha: f64,
    state: f64,
}

impl OnePole {
    /// `ms` is the time constant; `sample_rate` the rate it applies at.
    pub fn new(ms: f64, sample_rate: f64) -> Self {
        let dt = 1.0 / sample_rate;
        Self { alpha: 1.0 - (-dt / (ms.max(0.01) / 1000.0)).exp(), state: 0.0 }
    }

    /// Start already settled at `initial`, so a caller that never moves the
    /// target away from it sees no ramp-in — used for gain smoothers, whose
    /// state must match a fresh hard-coded gain exactly until the first
    /// parameter edit.
    pub fn new_at(ms: f64, sample_rate: f64, initial: f64) -> Self {
        let mut pole = Self::new(ms, sample_rate);
        pole.state = initial;
        pole
    }

    pub fn reset(&mut self) {
        self.state = 0.0;
    }

    /// Recompute the time constant, keeping the current state — a config
    /// edit (e.g. a new attack/release) should not restart the envelope.
    pub fn retune(&mut self, ms: f64, sample_rate: f64) {
        let dt = 1.0 / sample_rate;
        self.alpha = 1.0 - (-dt / (ms.max(0.01) / 1000.0)).exp();
    }

    #[inline]
    pub fn tick(&mut self, x: f64) -> f64 {
        self.state += self.alpha * (x - self.state);
        self.state
    }

    /// Tick `n` times toward `target`, for smoothing a value that only
    /// changes once per render block rather than once per sample.
    pub fn advance(&mut self, target: f64, n: usize) -> f64 {
        for _ in 0..n {
            self.tick(target);
        }
        self.state
    }

    /// Whether the state has settled within `target`'s floating-point noise
    /// floor — lets a caller resume a cheap early-exit once a ramp finishes.
    pub fn is_settled(&self, target: f64) -> bool {
        (self.state - target).abs() < 1e-9
    }
}

/// Streaming linked-sidechain compressor.
///
/// The gain computer is shared with the offline stage; only the envelope
/// followers need carried state.
pub struct StreamingCompressor {
    params: CompParams,
    fast: OnePole,
    slow: OnePole,
    /// One detector high-pass per bed channel, indexed by channel, built only
    /// when `params.sidechain_hpf_hz` is set.
    sidechain: Vec<SosFilter>,
    n_channels: usize,
}

fn sidechain_filters(params: &CompParams, sample_rate: u32, n_channels: usize) -> Vec<SosFilter> {
    let nyq = sample_rate as f64 / 2.0;
    match params.sidechain_hpf_hz {
        None => Vec::new(),
        Some(hz) => {
            let sos = crate::kernels::butter::butter_sos(
                2,
                (hz / nyq).clamp(1e-4, 0.999),
                crate::kernels::butter::BandType::High,
            );
            (0..n_channels).map(|_| SosFilter::from_flat(&sos)).collect()
        }
    }
}

impl StreamingCompressor {
    pub fn new(params: CompParams, sample_rate: u32, n_channels: usize) -> Self {
        Self {
            params,
            fast: OnePole::new(params.attack_ms, sample_rate as f64),
            slow: OnePole::new(params.release_ms, sample_rate as f64),
            sidechain: sidechain_filters(&params, sample_rate, n_channels),
            n_channels,
        }
    }

    pub fn reset(&mut self) {
        self.fast.reset();
        self.slow.reset();
        for filter in &mut self.sidechain {
            filter.reset();
        }
    }

    /// Adopt new config, keeping the envelope followers' state — a live
    /// threshold/ratio/attack edit should not restart the compressor cold.
    /// The detector filters are rebuilt only when the sidechain cutoff moved.
    pub fn retune(&mut self, params: CompParams, sample_rate: u32) {
        self.fast.retune(params.attack_ms, sample_rate as f64);
        self.slow.retune(params.release_ms, sample_rate as f64);
        if self.params.sidechain_hpf_hz != params.sidechain_hpf_hz {
            self.sidechain = sidechain_filters(&params, sample_rate, self.n_channels);
        }
        self.params = params;
    }

    /// Linked sidechain RMS across every non-LFE channel, with the detector
    /// high-pass applied per channel when one is configured.
    pub fn linked_rms(&mut self, bed: &[Vec<f64>], non_lfe: &[usize], frame: usize) -> f64 {
        let mut acc = 0.0;
        for &i in non_lfe {
            let v = match self.sidechain.get_mut(i) {
                Some(filter) => filter.tick(bed[i][frame]),
                None => bed[i][frame],
            };
            acc += v * v;
        }
        (acc / non_lfe.len() as f64 + 1e-20).sqrt()
    }

    /// Gain to apply to every non-LFE channel for one sample, given the
    /// linked RMS across them.
    #[inline]
    pub fn tick(&mut self, linked_rms: f64) -> f64 {
        let envelope = self.fast.tick(linked_rms).max(self.slow.tick(linked_rms));
        let env_db = 20.0 * envelope.max(1e-20).log10();
        let gr_db = crate::mastering::compressor::gain_reduction_db(env_db, &self.params);
        10.0_f64.powf((gr_db + self.params.makeup_db) / 20.0)
    }
}

/// Streaming SOS filter — a thin alias so call sites read as "carried state".
pub type StreamingSos = SosFilter;

/// Zero-phase low-pass over a bounded look-behind/look-ahead horizon.
///
/// The offline mono-maker runs `sosfiltfilt`, whose backward pass is
/// anticausal and therefore impossible to compute causally. Because the
/// worklet owns the whole stem it can still look ahead: running the offline
/// filter over `[t - behind, t + ahead]` and keeping the middle reproduces it
/// to within the filter's own decay over `ahead` samples — for the 80-100 Hz
/// mono-maker that is `e^-40` at a 100 ms horizon, far below any tolerance
/// that matters.
pub struct HorizonFiltFilt {
    sections: Vec<[f64; 6]>,
    behind: usize,
    ahead: usize,
}

impl HorizonFiltFilt {
    pub fn new(sections: Vec<[f64; 6]>, behind: usize, ahead: usize) -> Self {
        Self { sections, behind, ahead }
    }

    /// Filter `signal[start..end]`, using surrounding samples for context.
    ///
    /// Near either end of `signal` the true file boundary is used, which is
    /// what the offline pass sees, so the result there is exact.
    pub fn process_window(&self, signal: &[f64], start: usize, end: usize) -> Vec<f64> {
        let lo = start.saturating_sub(self.behind);
        let hi = (end + self.ahead).min(signal.len());
        let window = &signal[lo..hi];
        let filtered = sosfiltfilt(&self.sections, window)
            .unwrap_or_else(|| crate::kernels::biquad::sosfilt(&self.sections, window));
        filtered[start - lo..end - lo].to_vec()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernels::biquad::lfilter;
    use crate::kernels::butter::{butter_sos, BandType};
    use crate::kernels::filtfilt::sosfiltfilt;

    #[test]
    fn one_pole_matches_the_offline_lfilter() {
        let x: Vec<f64> = (0..2000).map(|i| (i as f64 * 0.03).sin()).collect();
        let sr = 48_000.0;
        let ms = 20.0;
        let mut p = OnePole::new(ms, sr);
        let got: Vec<f64> = x.iter().map(|v| p.tick(*v)).collect();

        let alpha = 1.0 - (-(1.0 / sr) / (ms / 1000.0)).exp();
        let want = lfilter(&[alpha], &[1.0, -(1.0 - alpha)], &x);
        for (i, (a, b)) in got.iter().zip(want.iter()).enumerate() {
            assert!((a - b).abs() < 1e-13, "sample {i}: {a} vs {b}");
        }
    }

    #[test]
    fn horizon_filtfilt_reproduces_the_offline_zero_phase_pass() {
        let sr = 48_000;
        let signal: Vec<f64> = (0..24_000)
            .map(|i| {
                let t = i as f64 / sr as f64;
                (2.0 * std::f64::consts::PI * 60.0 * t).sin()
                    + 0.3 * (2.0 * std::f64::consts::PI * 900.0 * t).sin()
            })
            .collect();
        let sos = butter_sos(2, 100.0 / (sr as f64 / 2.0), BandType::Low);
        let offline = sosfiltfilt(&sos, &signal).expect("signal is long enough");

        // 100 ms of context either side, the horizon the worklet renders with.
        let horizon = HorizonFiltFilt::new(sos, 4800, 4800);
        let mut got = Vec::with_capacity(signal.len());
        let block = 128;
        let mut start = 0;
        while start < signal.len() {
            let end = (start + block).min(signal.len());
            got.extend(horizon.process_window(&signal, start, end));
            start = end;
        }

        assert_eq!(got.len(), offline.len());
        for (i, (a, b)) in got.iter().zip(offline.iter()).enumerate() {
            assert!((a - b).abs() < 1e-9, "sample {i}: {a} vs {b}");
        }
    }
}
