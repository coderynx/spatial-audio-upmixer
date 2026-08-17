//! The mastering bus, rendered incrementally.
//!
//! Stages split by what they need to see. Reference match, EQ, compression
//! and the bass band gains are causal and simply carry their state. The LF
//! unifier's zero-phase pass and the limiter's forward-window minimum both
//! need to look ahead, so the engine keeps a queue in front of them and only
//! emits samples that have their full look-ahead behind them.

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::{butter_sos, BandType};
use crate::kernels::minfilter::{minimum_filter1d, BorderMode};
use crate::kernels::upfirdn::upfirdn_up;
use crate::loudness::{TRUE_PEAK_FIR_4X, TRUE_PEAK_OVERSAMPLE};
use crate::mastering::bass::{excite_harmonics, BassParams, PunchState};
use crate::mastering::decorrelate::Decorrelator;
use crate::mastering::limiter::{LimiterParams, FIR_DELAY, FIR_MARGIN_SAMPLES};
use crate::mastering::non_lfe;

use super::band::RollingBand;
use super::conv::StreamingConvolver;
use super::params::MasterParams;

/// Look-behind and look-ahead the LF unifier's zero-phase pass runs with.
/// At 100 ms the 80-120 Hz filter has decayed by `e^-40`, so truncating there
/// is below any tolerance that matters — see [`super::band::RollingBand`].
pub const UNIFY_HORIZON_MS: f64 = 100.0;

/// Band the unifier's backward pass produces per warm-up — see
/// [`DECORR_CHUNK_MS`].
pub const UNIFY_CHUNK_MS: f64 = 50.0;

/// The decorrelator's own, longer horizon: its band split is a 4th-order
/// 100-300 Hz band-pass, whose impulse response outlives the unifier's
/// 2nd-order low-pass by orders of magnitude. Only the backward pass is
/// truncated here — the forward one carries its whole history — which is
/// what keeps 200 ms enough: measured against the offline pass, the band
/// lands within 5e-12, where 150 ms gives 2.5e-9 and 100 ms gives 1.3e-6.
pub const DECORR_HORIZON_MS: f64 = 200.0;

/// Band the decorrelator's backward pass produces per warm-up. Bigger spends
/// the warm-up over more output; the engine has to buffer `2 * chunk +
/// horizon` of look-ahead for it, which is what caps it.
pub const DECORR_CHUNK_MS: f64 = 50.0;

/// Causal, per-channel front of the chain.
pub struct CausalChain {
    reference: Option<StreamingConvolver>,
    eq: Option<StreamingConvolver>,
    reference_gain: f64,
    eq_strength: f64,
    sub: Option<(SosFilter, f64)>,
    mid: Option<(SosFilter, SosFilter, f64)>,
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
            is_lfe,
        }
    }

    /// Adopt new master params in place. Reference/EQ gain and blend are
    /// scalars, assigned directly; the reference/EQ convolvers keep their
    /// history via [`StreamingConvolver::retune_kernel`] when their FIR
    /// content changed, or are added/dropped when it went to/from empty. The
    /// bass sub/mid filters are cheap to rebuild outright (a few biquad
    /// sections), so they're only touched — and only reset — when `new.bass`
    /// actually differs from what this chain was built with.
    pub fn retune(&mut self, sample_rate: u32, old: &MasterParams, new: &MasterParams) {
        self.reference_gain = new.reference_gain;
        self.eq_strength = new.eq_strength;

        if !self.is_lfe && old.reference_fir != new.reference_fir {
            if new.reference_fir.is_empty() {
                self.reference = None;
            } else if let Some(conv) = &mut self.reference {
                conv.retune_kernel(new.reference_fir.clone());
            } else {
                self.reference = Some(StreamingConvolver::new(new.reference_fir.clone()));
            }
        }

        if !self.is_lfe && old.eq_fir != new.eq_fir {
            if new.eq_fir.is_empty() {
                self.eq = None;
            } else if let Some(conv) = &mut self.eq {
                conv.retune_kernel(new.eq_fir.clone());
            } else {
                self.eq = Some(StreamingConvolver::new(new.eq_fir.clone()));
            }
        }

        if old.bass != new.bass {
            let nyq = sample_rate as f64 / 2.0;
            let bass = new.bass.as_ref();
            self.sub = bass.filter(|b| b.sub_gain_db != 0.0 && !self.is_lfe).map(|b| {
                (
                    SosFilter::from_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::Low)),
                    10.0_f64.powf(b.sub_gain_db / 20.0),
                )
            });
            self.mid = bass.filter(|b| b.mid_gain_db != 0.0 && !self.is_lfe).map(|b| {
                (
                    SosFilter::from_flat(&butter_sos(2, b.mid_cutoff_hz / nyq, BandType::Low)),
                    SosFilter::from_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::High)),
                    10.0_f64.powf(b.mid_gain_db / 20.0),
                )
            });
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

    /// The band gains, which run before the LF unifier.
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

    /// The LFE trim, which `BassController` runs *after* the LF unifier —
    /// reversing them measurably changes the low end.
    pub fn lfe_trim(&mut self, block: &mut [f64], lfe_gain_db: f64) {
        if self.is_lfe && lfe_gain_db != 0.0 {
            let g = 10.0_f64.powf(lfe_gain_db / 20.0);
            for v in block.iter_mut() {
                *v *= g;
            }
        }
    }
}

/// LF unification over a look-ahead window.
///
/// Mirrors `mastering::bass::lf_unify`: one low band per non-LFE channel,
/// summed to a mono bus, transient-shaped, excited, then handed back over the
/// caller-resolved target weights. The punch envelopes are causal, so they
/// advance only over what is emitted — the same discipline
/// [`StreamingLimiter`]'s release smoother follows.
pub struct LfUnifier {
    filter: RollingBand,
    lf_targets: Vec<(usize, f64)>,
    lfe: Option<usize>,
    params: BassParams,
    punch: PunchState,
    sample_rate: u32,
}

impl LfUnifier {
    pub fn new(
        sample_rate: u32,
        n_channels: usize,
        lfe: Option<usize>,
        params: BassParams,
        lf_targets: Vec<(usize, f64)>,
        base: usize,
    ) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        let scale = |ms: f64| (sample_rate as f64 * ms / 1000.0) as usize;
        let cutoff = params.unify_hz.unwrap_or(nyq);
        let sos = butter_sos(2, (cutoff / nyq).clamp(1e-4, 0.999), BandType::Low);
        Self {
            filter: RollingBand::new(
                sos,
                scale(UNIFY_HORIZON_MS),
                scale(UNIFY_CHUNK_MS),
                non_lfe(n_channels, lfe),
                base,
            ),
            lf_targets,
            lfe,
            params,
            punch: PunchState::default(),
            sample_rate,
        }
    }

    /// Source frames ahead of `end` its band split needs — see
    /// [`RollingBand::look_ahead`].
    pub fn look_ahead(&self) -> usize {
        self.filter.look_ahead()
    }

    /// Unify the low band across `window`, which holds `source[..][start..end]`
    /// and is the only thing written — the zero-phase pass reads its context
    /// outward from `start`/`end` in `source`, which stays untouched so the
    /// decorrelator still sees the pre-unification signal offline hands it.
    pub fn process(
        &mut self,
        source: &[Vec<f64>],
        base: usize,
        total: usize,
        window: &mut [Vec<f64>],
        start: usize,
        end: usize,
    ) {
        if end <= start || self.lf_targets.is_empty() {
            return;
        }
        let n = end - start;
        let mut bus = vec![0.0; n];
        self.filter.advance(source, base, total, start, end);
        for slot in 0..self.filter.channels().len() {
            for (acc, v) in bus.iter_mut().zip(self.filter.band(slot, start, end)) {
                *acc += v;
            }
        }
        self.punch.run(&mut bus, self.sample_rate, &self.params);

        for (slot, &i) in self.filter.channels().iter().enumerate() {
            let Some(out) = window.get_mut(i) else { continue };
            for (v, l) in out.iter_mut().zip(self.filter.band(slot, start, end)) {
                *v -= l;
            }
        }

        let harmonics = self.params.excite.then(|| excite_harmonics(&bus, &self.params));
        for &(i, weight) in &self.lf_targets {
            if i >= window.len() || weight == 0.0 {
                continue;
            }
            let harmonics = harmonics.as_ref().filter(|_| Some(i) != self.lfe);
            for k in 0..n.min(window[i].len()) {
                let h = harmonics.map_or(0.0, |h| h[k]);
                window[i][k] += (bus[k] + h) * weight;
            }
        }
    }
}

/// Mid-bass decorrelation over its own look-ahead window.
///
/// Mirrors the tail of `mastering::bass::bass_control`: the zero-phase band
/// comes off the *pre*-unification queue, exactly as offline takes it before
/// calling `lf_unify`, and the resulting delta is added to the unified block.
/// That ordering is what lets this read its look-ahead out of `pre`, whose
/// samples are already final, instead of needing a second horizon behind the
/// unifier.
pub struct StreamingDecorrelator {
    band: RollingBand,
    channels: Vec<Decorrelator>,
}

impl StreamingDecorrelator {
    /// `None` when the stage is off or its band collapsed — see
    /// [`crate::mastering::decorrelate::band_sos`].
    pub fn new(
        sample_rate: u32,
        n_channels: usize,
        lfe: Option<usize>,
        params: &BassParams,
        base: usize,
    ) -> Option<Self> {
        let sos = crate::mastering::decorrelate::band_sos(sample_rate, params)?;
        let scale = |ms: f64| (sample_rate as f64 * ms / 1000.0) as usize;
        let bed = non_lfe(n_channels, lfe);
        Some(Self {
            channels: bed.iter().map(|i| Decorrelator::new(*i, sample_rate, params)).collect(),
            band: RollingBand::new(
                sos,
                scale(DECORR_HORIZON_MS),
                scale(DECORR_CHUNK_MS),
                bed,
                base,
            ),
        })
    }

    /// Source frames ahead of `end` its band split needs — see
    /// [`RollingBand::look_ahead`].
    pub fn look_ahead(&self) -> usize {
        self.band.look_ahead()
    }

    /// Decorrelate `window[..][..]`, taking the band from `pre[..][start..end]`
    /// and reading ahead of it for the zero-phase pass's context.
    pub fn process(
        &mut self,
        pre: &[Vec<f64>],
        pre_base: usize,
        total: usize,
        window: &mut [Vec<f64>],
        start: usize,
        end: usize,
    ) {
        if end <= start {
            return;
        }
        self.band.advance(pre, pre_base, total, start, end);
        for (slot, decorrelator) in self.channels.iter_mut().enumerate() {
            let channel = self.band.channels()[slot];
            let Some(out) = window.get_mut(channel) else { continue };
            decorrelator.run(out, self.band.band(slot, start, end));
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

