//! The mastering bus, rendered incrementally.
//!
//! Stages split by what they need to see. Reference match, EQ, compression,
//! the bass band gains and the exciter are causal and simply carry their
//! state. The mono-maker's zero-phase pass and the limiter's forward-window
//! minimum both need to look ahead, so the engine keeps a queue in front of
//! them and only emits samples that have their full look-ahead behind them.

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::{butter_sos, BandType};
use crate::kernels::minfilter::{minimum_filter1d, BorderMode};
use crate::kernels::upfirdn::upfirdn_up;
use crate::loudness::{TRUE_PEAK_FIR_4X, TRUE_PEAK_OVERSAMPLE};
use crate::mastering::limiter::{LimiterParams, FIR_DELAY, FIR_MARGIN_SAMPLES};

use super::conv::StreamingConvolver;
use super::params::MasterParams;
use super::state::HorizonFiltFilt;

/// Look-behind and look-ahead the mono-maker's zero-phase pass runs with.
/// At 100 ms the 80-100 Hz filter has decayed by `e^-40`, so truncating there
/// is below any tolerance that matters — see `state::HorizonFiltFilt`.
pub const MONO_HORIZON_MS: f64 = 100.0;

/// Causal, per-channel front of the chain.
pub struct CausalChain {
    reference: Option<StreamingConvolver>,
    eq: Option<StreamingConvolver>,
    reference_gain: f64,
    eq_strength: f64,
    sub: Option<(SosFilter, f64)>,
    mid: Option<(SosFilter, SosFilter, f64)>,
    exciter: Option<(SosFilter, f64, f64)>,
    is_lfe: bool,
}

impl CausalChain {
    pub fn new(sample_rate: u32, p: &MasterParams, is_lfe: bool) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        let bass = p.bass.as_ref();
        Self {
            reference: (!p.reference_fir.is_empty() && !is_lfe)
                .then(|| StreamingConvolver::new(p.reference_fir.clone())),
            eq: (!p.eq_fir.is_empty() && !is_lfe)
                .then(|| StreamingConvolver::new(p.eq_fir.clone())),
            reference_gain: p.reference_gain,
            eq_strength: p.eq_strength,
            sub: bass.filter(|b| b.sub_gain_db != 0.0 && !is_lfe).map(|b| {
                (
                    SosFilter::from_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::Low)),
                    10.0_f64.powf(b.sub_gain_db / 20.0),
                )
            }),
            mid: bass.filter(|b| b.mid_gain_db != 0.0 && !is_lfe).map(|b| {
                (
                    SosFilter::from_flat(&butter_sos(2, b.mid_cutoff_hz / nyq, BandType::Low)),
                    SosFilter::from_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::High)),
                    10.0_f64.powf(b.mid_gain_db / 20.0),
                )
            }),
            exciter: bass.filter(|b| b.excite && !is_lfe).map(|b| {
                (
                    SosFilter::from_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::Low)),
                    b.excite_drive,
                    b.excite_blend,
                )
            }),
            is_lfe,
        }
    }

    /// Everything before the compressor, which needs the linked bus first.
    pub fn pre_compressor(&mut self, block: &[f64]) -> Vec<f64> {
        let mut out: Vec<f64> = block.iter().map(|v| v * self.reference_gain).collect();
        if let Some(conv) = &mut self.reference {
            out = conv.process(&out);
        }
        if let Some(conv) = &mut self.eq {
            let wet = conv.process(&out);
            if self.eq_strength < 1.0 {
                for (dry, w) in out.iter_mut().zip(wet.iter()) {
                    *dry = (1.0 - self.eq_strength) * *dry + self.eq_strength * w;
                }
            } else {
                out = wet;
            }
        }
        out
    }

    /// The band gains, which run before the mono-maker.
    pub fn band_gains(&mut self, block: &mut [f64]) {
        if let Some((filter, gain)) = &mut self.sub {
            for v in block.iter_mut() {
                let band = filter.tick(*v);
                *v = (*v - band) + band * *gain;
            }
        }
        if let Some((lp, hp, gain)) = &mut self.mid {
            for v in block.iter_mut() {
                let band = hp.tick(lp.tick(*v));
                *v = (*v - band) + band * *gain;
            }
        }
    }

    /// The exciter and LFE trim, which `BassController` runs *after* the
    /// mono-maker — reversing them measurably changes the low end.
    pub fn post_mono(&mut self, block: &mut [f64], lfe_gain_db: f64) {
        if let Some((filter, drive, blend)) = &mut self.exciter {
            for v in block.iter_mut() {
                let sub = filter.tick(*v);
                *v += (sub * *drive).tanh() * *blend;
            }
        }
        if self.is_lfe && lfe_gain_db != 0.0 {
            let g = 10.0_f64.powf(lfe_gain_db / 20.0);
            for v in block.iter_mut() {
                *v *= g;
            }
        }
    }
}

/// Bass mono-maker over a look-ahead window.
pub struct MonoMaker {
    filter: HorizonFiltFilt,
    pairs: Vec<(usize, usize)>,
}

impl MonoMaker {
    pub fn new(sample_rate: u32, cutoff_hz: f64, pairs: Vec<(usize, usize)>) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        let horizon = (sample_rate as f64 * MONO_HORIZON_MS / 1000.0) as usize;
        let sos = butter_sos(2, (cutoff_hz / nyq).clamp(1e-4, 0.999), BandType::Low);
        Self { filter: HorizonFiltFilt::new(sos, horizon, horizon), pairs }
    }

    /// Couple each pair's low band across `queue[start..end]`.
    pub fn process(&self, queue: &mut [Vec<f64>], start: usize, end: usize) {
        for &(l, r) in &self.pairs {
            if l >= queue.len() || r >= queue.len() {
                continue;
            }
            let low_l = self.filter.process_window(&queue[l], start, end);
            let low_r = self.filter.process_window(&queue[r], start, end);
            for i in start..end {
                let mono = (low_l[i - start] + low_r[i - start]) * 0.5;
                let dry_l = queue[l][i];
                let dry_r = queue[r][i];
                queue[l][i] = mono + (dry_l - low_l[i - start]);
                queue[r][i] = mono + (dry_r - low_r[i - start]);
            }
        }
    }
}

/// Streaming look-ahead limiter.
///
/// The forward-window minimum only ever reads `lookahead` samples ahead, so
/// given that much queue the emitted gain curve is the offline one exactly;
/// only the release smoother needs carried state.
pub struct StreamingLimiter {
    lookahead_samples: usize,
    ceiling_linear: f64,
    alpha_release: f64,
    release_state: f64,
    history: Vec<Vec<f64>>,
}

impl StreamingLimiter {
    pub fn new(params: LimiterParams, sample_rate: u32, n_channels: usize) -> Self {
        let over_sr = sample_rate as f64 * TRUE_PEAK_OVERSAMPLE as f64;
        Self {
            lookahead_samples: ((params.lookahead_ms / 1000.0 * over_sr).round() as usize).max(1),
            ceiling_linear: 10.0_f64.powf((params.ceiling_dbtp - params.safety_margin_db) / 20.0),
            alpha_release: 1.0 - (-1.0 / (params.release_ms.max(0.01) / 1000.0 * over_sr)).exp(),
            release_state: 0.0,
            history: vec![vec![0.0; TRUE_PEAK_FIR_4X.len() - 1]; n_channels],
        }
    }

    /// Base-rate samples of queue this needs past the emit point.
    pub fn required_lookahead(&self) -> usize {
        self.lookahead_samples.div_ceil(TRUE_PEAK_OVERSAMPLE) + FIR_MARGIN_SAMPLES + 2
    }

    /// Limit `queue[..][start..end]` in place, reading ahead within the queue.
    pub fn process(&mut self, queue: &mut [Vec<f64>], start: usize, end: usize) -> f64 {
        let emit = end - start;
        if emit == 0 {
            return 0.0;
        }
        let margin = FIR_MARGIN_SAMPLES;
        let window_end = (end + self.required_lookahead()).min(queue[0].len());
        let span = window_end - start;

        // Linked 4x true-peak envelope across the window, carrying each
        // channel's FIR history so the interpolation is continuous.
        let mut envelope = vec![0.0_f64; span * TRUE_PEAK_OVERSAMPLE];
        for (ch, channel) in queue.iter().enumerate() {
            let history = &self.history[ch];
            let mut padded = history.clone();
            padded.extend_from_slice(&channel[start..window_end]);
            let upsampled = upfirdn_up(&TRUE_PEAK_FIR_4X, &padded, TRUE_PEAK_OVERSAMPLE);
            let begin = history.len() * TRUE_PEAK_OVERSAMPLE + FIR_DELAY;
            for (i, slot) in envelope.iter_mut().enumerate() {
                if let Some(v) = upsampled.get(begin + i) {
                    *slot = slot.max(v.abs());
                }
            }
        }

        let gain_inst: Vec<f64> = envelope
            .iter()
            .map(|e| (self.ceiling_linear / e.max(1e-12)).min(1.0))
            .collect();
        let gain_lookahead =
            crate::mastering::limiter::forward_window_min(&gain_inst, self.lookahead_samples);
        let need_db: Vec<f64> = gain_lookahead
            .iter()
            .map(|g| -20.0 * g.max(1e-12).log10())
            .collect();

        // The release smoother advances only over what is emitted; the extra
        // margin the dilation needs is computed from a copy of its state.
        let emit_over = emit * TRUE_PEAK_OVERSAMPLE;
        let margin_over = (margin + 1) * TRUE_PEAK_OVERSAMPLE;
        let mut smoothed = Vec::with_capacity(emit_over + margin_over);
        let mut state = self.release_state;
        for (i, need) in need_db.iter().take(emit_over + margin_over).enumerate() {
            state += self.alpha_release * (need - state);
            smoothed.push(need.max(state));
            if i + 1 == emit_over {
                self.release_state = state;
            }
        }

        let gain_base: Vec<f64> = smoothed
            .chunks_exact(TRUE_PEAK_OVERSAMPLE)
            .map(|c| {
                let worst = c.iter().fold(f64::NEG_INFINITY, |m, v| m.max(*v));
                10.0_f64.powf(-worst / 20.0)
            })
            .collect();
        let dilated = minimum_filter1d(&gain_base, 2 * margin + 1, BorderMode::Nearest);

        for (ch, channel) in queue.iter_mut().enumerate() {
            self.history[ch] = channel[end.saturating_sub(TRUE_PEAK_FIR_4X.len() - 1)..end].to_vec();
            for (i, g) in dilated.iter().take(emit).enumerate() {
                channel[start + i] *= g;
            }
        }

        smoothed
            .iter()
            .take(emit_over)
            .fold(0.0_f64, |m, v| m.max(*v))
    }
}

/// Linked sidechain RMS across every non-LFE channel, for the compressor.
pub fn linked_rms(bed: &[Vec<f64>], non_lfe: &[usize], frame: usize) -> f64 {
    let mut acc = 0.0;
    for &i in non_lfe {
        let v = bed[i][frame];
        acc += v * v;
    }
    (acc / non_lfe.len() as f64 + 1e-20).sqrt()
}
