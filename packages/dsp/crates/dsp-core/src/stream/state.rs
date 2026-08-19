//! Stateful forms of the stages the offline chain runs whole-buffer.
//!
//! Each type carries exactly the state its offline counterpart would have
//! accumulated, so streaming a signal through in blocks and running it
//! offline give the same samples.

use crate::kernels::biquad::SosFilter;
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
    /// linked RMS across them, and the gain reduction that gain carries in dB
    /// — positive downward, and excluding make-up, which is what a GR meter
    /// shows.
    #[inline]
    pub fn tick(&mut self, linked_rms: f64) -> (f64, f64) {
        let envelope = self.fast.tick(linked_rms).max(self.slow.tick(linked_rms));
        let env_db = 20.0 * envelope.max(1e-20).log10();
        let gr_db = crate::mastering::compressor::gain_reduction_db(env_db, &self.params);
        (10.0_f64.powf((gr_db + self.params.makeup_db) / 20.0), -gr_db)
    }
}

/// Streaming SOS filter — a thin alias so call sites read as "carried state".
pub type StreamingSos = SosFilter;
