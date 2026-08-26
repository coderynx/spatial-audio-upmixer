//! Incremental streaming look-ahead limiter.

use std::collections::VecDeque;

use crate::kernels::minfilter::SlidingMin;
use crate::loudness::{TRUE_PEAK_FIR_4X, TRUE_PEAK_OVERSAMPLE};
use crate::mastering::limiter::{
    LimiterInfo, LimiterParams, FIR_DELAY, FIR_MARGIN_SAMPLES, GR_DUTY_FLOOR_DB,
};
use crate::mastering::non_lfe;

const DETECTOR_HISTORY: usize = TRUE_PEAK_FIR_4X.len().div_ceil(TRUE_PEAK_OVERSAMPLE);

struct LimiterCurve {
    channels: Vec<usize>,
    history: Vec<[f64; DETECTOR_HISTORY]>,
    history_cursor: usize,
    start: Option<usize>,
    fed_until: usize,
    real_frames: usize,
    generated: usize,
    lookahead: SlidingMin,
    lookahead_window: usize,
    lookahead_pushed: usize,
    release_state: f64,
    alpha_release: f64,
    over_group: [f64; TRUE_PEAK_OVERSAMPLE],
    over_group_len: usize,
    dilation: SlidingMin,
    base_pushed: usize,
    pending_stats: VecDeque<f64>,
    ready: VecDeque<(f64, f64)>,
    last_base_gain: f64,
    ceiling_linear: f64,
    finalised: bool,
}

impl LimiterCurve {
    fn new(
        channels: Vec<usize>,
        lookahead_window: usize,
        ceiling_linear: f64,
        alpha_release: f64,
    ) -> Self {
        Self {
            history: vec![[0.0; DETECTOR_HISTORY]; channels.len()],
            channels,
            history_cursor: 0,
            start: None,
            fed_until: 0,
            real_frames: 0,
            generated: 0,
            lookahead: SlidingMin::new(lookahead_window),
            lookahead_window,
            lookahead_pushed: 0,
            release_state: 0.0,
            alpha_release,
            over_group: [0.0; TRUE_PEAK_OVERSAMPLE],
            over_group_len: 0,
            dilation: SlidingMin::new(2 * FIR_MARGIN_SAMPLES + 1),
            base_pushed: 0,
            pending_stats: VecDeque::with_capacity(512),
            ready: VecDeque::with_capacity(512),
            last_base_gain: 1.0,
            ceiling_linear,
            finalised: false,
        }
    }

    fn feed(
        &mut self,
        queue: &[Vec<f64>],
        queue_base: usize,
        start: usize,
        through: usize,
        final_input: bool,
    ) {
        if self.start.is_none() {
            self.start = Some(start);
            self.fed_until = start;
        }
        while self.fed_until < through {
            let index = self.fed_until - queue_base;
            for (slot, &channel) in self.channels.iter().enumerate() {
                self.history[slot][self.history_cursor] = queue[channel][index];
            }
            self.push_phases(None);
            self.history_cursor = (self.history_cursor + 1) % DETECTOR_HISTORY;
            self.fed_until += 1;
            self.real_frames += 1;
        }
        if final_input && !self.finalised {
            // The centered true-peak FIR and both forward windows need their
            // offline zero/nearest padding once no more programme can arrive.
            let wanted = self.real_frames * TRUE_PEAK_OVERSAMPLE;
            while self.generated.saturating_sub(FIR_DELAY) < wanted {
                for history in &mut self.history {
                    history[self.history_cursor] = 0.0;
                }
                self.push_phases(Some(wanted));
                self.history_cursor = (self.history_cursor + 1) % DETECTOR_HISTORY;
            }
            for _ in 1..self.lookahead_window {
                self.push_instant(1.0);
            }
            for _ in 0..FIR_MARGIN_SAMPLES {
                let gain = self.dilation.push(self.last_base_gain);
                if let Some(stat) = self.pending_stats.pop_front() {
                    self.ready.push_back((gain, stat));
                }
            }
            self.finalised = true;
        }
    }

    fn push_phases(&mut self, limit: Option<usize>) {
        for phase in 0..TRUE_PEAK_OVERSAMPLE {
            let mut envelope = 0.0_f64;
            for history in &self.history {
                let mut sample = 0.0;
                let mut delay = 0;
                while delay * TRUE_PEAK_OVERSAMPLE + phase < TRUE_PEAK_FIR_4X.len() {
                    let index = (self.history_cursor + DETECTOR_HISTORY - delay) % DETECTOR_HISTORY;
                    sample +=
                        history[index] * TRUE_PEAK_FIR_4X[delay * TRUE_PEAK_OVERSAMPLE + phase];
                    delay += 1;
                }
                envelope = envelope.max(sample.abs());
            }
            if self.generated >= FIR_DELAY
                && limit.is_none_or(|wanted| self.generated - FIR_DELAY < wanted)
            {
                let gain = (self.ceiling_linear / envelope.max(1e-12)).min(1.0);
                self.push_instant(gain);
            }
            self.generated += 1;
        }
    }

    fn push_instant(&mut self, gain: f64) {
        let gain = self.lookahead.push(gain);
        self.lookahead_pushed += 1;
        if self.lookahead_pushed < self.lookahead_window {
            return;
        }
        let need = -20.0 * gain.max(1e-12).log10();
        self.release_state += self.alpha_release * (need - self.release_state);
        self.over_group[self.over_group_len] = need.max(self.release_state);
        self.over_group_len += 1;
        if self.over_group_len < TRUE_PEAK_OVERSAMPLE {
            return;
        }

        let worst = self
            .over_group
            .iter()
            .fold(f64::NEG_INFINITY, |max, value| max.max(*value));
        self.last_base_gain = 10.0_f64.powf(-worst / 20.0);
        self.pending_stats.push_back(worst);
        if self.base_pushed == 0 {
            for _ in 0..FIR_MARGIN_SAMPLES {
                self.dilation.push(self.last_base_gain);
            }
        }
        let gain = self.dilation.push(self.last_base_gain);
        self.base_pushed += 1;
        if self.base_pushed > FIR_MARGIN_SAMPLES {
            let stat = self
                .pending_stats
                .pop_front()
                .expect("limiter statistic queued");
            self.ready.push_back((gain, stat));
        }
        self.over_group_len = 0;
    }

    fn take(&mut self, count: usize, curve: &mut Vec<f64>) -> (f64, usize) {
        curve.clear();
        let mut max_gr = 0.0_f64;
        let floor_gain = 10.0_f64.powf(-GR_DUTY_FLOOR_DB / 20.0);
        let mut engaged = 0;
        for _ in 0..count {
            let (gain, stat) = self.ready.pop_front().expect("limiter look-ahead filled");
            curve.push(gain);
            max_gr = max_gr.max(stat);
            engaged += usize::from(gain < floor_gain);
        }
        (max_gr, engaged)
    }
}

/// Streaming look-ahead limiter.
///
/// The true-peak FIR, forward-window minimum, release smoother, and final
/// dilation all advance once per new sample. Mains share one curve while LFE
/// carries its own, matching [`crate::mastering::limiter::lookahead_limit`].
pub struct StreamingLimiter {
    required_lookahead: usize,
    mains: LimiterCurve,
    lfe: Option<(usize, LimiterCurve)>,
    curve: Vec<f64>,
    lfe_curve: Vec<f64>,
}

impl StreamingLimiter {
    /// Build independent linked-mains and LFE detector curves.
    pub fn new(
        params: LimiterParams,
        sample_rate: u32,
        n_channels: usize,
        lfe: Option<usize>,
    ) -> Self {
        let over_sr = sample_rate as f64 * TRUE_PEAK_OVERSAMPLE as f64;
        let mut lookahead_window =
            ((params.lookahead_ms / 1000.0 * over_sr).round() as usize).max(1);
        if lookahead_window % 2 == 0 {
            lookahead_window += 1;
        }
        let ceiling = 10.0_f64.powf((params.ceiling_dbtp - params.safety_margin_db) / 20.0);
        let alpha = 1.0 - (-1.0 / (params.release_ms.max(0.01) / 1000.0 * over_sr)).exp();
        let required_lookahead =
            (lookahead_window - 1 + FIR_DELAY).div_ceil(TRUE_PEAK_OVERSAMPLE) + FIR_MARGIN_SAMPLES;
        Self {
            required_lookahead,
            mains: LimiterCurve::new(non_lfe(n_channels, lfe), lookahead_window, ceiling, alpha),
            lfe: lfe.map(|channel| {
                (
                    channel,
                    LimiterCurve::new(vec![channel], lookahead_window, ceiling, alpha),
                )
            }),
            curve: Vec::with_capacity(512),
            lfe_curve: Vec::with_capacity(512),
        }
    }

    pub fn required_lookahead(&self) -> usize {
        self.required_lookahead
    }

    /// Limit `queue[start..end]`, reading only the queued future after `end`.
    pub fn process(
        &mut self,
        queue: &mut [Vec<f64>],
        queue_base: usize,
        start: usize,
        end: usize,
        final_input: bool,
    ) -> LimiterInfo {
        let emit = end - start;
        if emit == 0 {
            return LimiterInfo::default();
        }
        let absolute_start = queue_base + start;
        let window_end = (end + self.required_lookahead()).min(queue[0].len());
        let absolute_window_end = queue_base + window_end;
        self.mains.feed(
            queue,
            queue_base,
            absolute_start,
            absolute_window_end,
            final_input,
        );
        let (max_gr_db, engaged) = self.mains.take(emit, &mut self.curve);
        let lfe_max_gr_db = match &mut self.lfe {
            Some((_, detector)) => {
                detector.feed(
                    queue,
                    queue_base,
                    absolute_start,
                    absolute_window_end,
                    final_input,
                );
                detector.take(emit, &mut self.lfe_curve).0
            }
            None => 0.0,
        };

        for &channel in &self.mains.channels {
            for (offset, gain) in self.curve.iter().enumerate() {
                queue[channel][start + offset] *= gain;
            }
        }
        if let Some((channel, _)) = &self.lfe {
            for (offset, gain) in self.lfe_curve.iter().enumerate() {
                queue[*channel][start + offset] *= gain;
            }
        }

        LimiterInfo {
            max_gr_db,
            duty: engaged as f64 / emit as f64,
            lfe_max_gr_db,
        }
    }
}
