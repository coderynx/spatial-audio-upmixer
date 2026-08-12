//! The preview engine: stems in, mastered speaker bed out, block by block.
//!
//! The worklet owns the decoded stems, so the engine can always look ahead.
//! Two queues carry that look-ahead: `pre` holds the causal chain's output
//! and feeds the mono-maker's zero-phase pass, `post` holds the mono-maker's
//! output and feeds the limiter's forward-window minimum. Nothing is emitted
//! until its full look-ahead exists, which is what lets both stages be the
//! offline algorithm rather than a causal approximation of one.

use super::master::{linked_rms, CausalChain, MonoMaker, StreamingLimiter, MONO_HORIZON_MS};
use super::params::EngineParams;
use super::routing::{shape_index, LfeBus, StemRouteState};
use super::state::StreamingCompressor;

/// Decoded stereo PCM for one stem, as the host transfers it.
pub struct StemSource {
    pub left: Vec<f32>,
    pub right: Vec<f32>,
}

impl StemSource {
    pub fn len(&self) -> usize {
        self.left.len().min(self.right.len())
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

struct Queue {
    base: usize,
    channels: Vec<Vec<f64>>,
}

impl Queue {
    fn new(n_channels: usize) -> Self {
        Self { base: 0, channels: vec![Vec::new(); n_channels] }
    }

    fn end(&self) -> usize {
        self.base + self.channels[0].len()
    }

    fn drain_to(&mut self, absolute: usize) {
        let keep_from = absolute.saturating_sub(self.base).min(self.channels[0].len());
        if keep_from == 0 {
            return;
        }
        for channel in &mut self.channels {
            channel.drain(..keep_from);
        }
        self.base += keep_from;
    }
}

pub struct PreviewEngine {
    sample_rate: u32,
    params: EngineParams,
    stems: Vec<StemSource>,
    routes: Vec<StemRouteState>,
    lfe_bus: LfeBus,
    causal: Vec<CausalChain>,
    compressor: Option<StreamingCompressor>,
    mono: Option<MonoMaker>,
    limiter: Option<StreamingLimiter>,
    pre: Queue,
    post: Queue,
    mono_done: usize,
    emitted: usize,
    mono_horizon: usize,
    total_frames: usize,
}

impl PreviewEngine {
    pub fn new(sample_rate: u32, params: EngineParams, stems: Vec<StemSource>) -> Self {
        let n_channels = params.speakers.len();
        let routes = params
            .stems
            .iter()
            .map(|s| StemRouteState::new(sample_rate, &params.sends, &s.eq_fir))
            .collect();
        let causal = (0..n_channels)
            .map(|i| CausalChain::new(sample_rate, &params.master, params.lfe_index == Some(i)))
            .collect();
        let compressor = params
            .master
            .compressor
            .map(|c| StreamingCompressor::new(c, sample_rate));
        let mono = params
            .master
            .bass
            .and_then(|b| b.mono_cutoff_hz)
            .map(|hz| MonoMaker::new(sample_rate, hz, params.master.stereo_pairs.clone()));
        let limiter = params
            .master
            .limiter
            .map(|l| StreamingLimiter::new(l, sample_rate, n_channels));
        let total_frames = stems.iter().map(|s| s.len()).max().unwrap_or(0);

        Self {
            sample_rate,
            lfe_bus: LfeBus::new(sample_rate, &params.sends),
            params,
            stems,
            routes,
            causal,
            compressor,
            mono,
            limiter,
            pre: Queue::new(n_channels),
            post: Queue::new(n_channels),
            mono_done: 0,
            emitted: 0,
            mono_horizon: (sample_rate as f64 * MONO_HORIZON_MS / 1000.0) as usize,
            total_frames,
        }
    }

    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    /// Add a decoded stem, in the order its entry appears in `params.stems`.
    pub fn push_stem(&mut self, stem: StemSource) {
        self.total_frames = self.total_frames.max(stem.len());
        self.stems.push(stem);
    }

    pub fn total_frames(&self) -> usize {
        self.total_frames
    }

    pub fn position(&self) -> usize {
        self.emitted
    }

    fn non_lfe(&self) -> Vec<usize> {
        (0..self.params.speakers.len())
            .filter(|i| self.params.lfe_index != Some(*i))
            .collect()
    }

    /// Route and run the causal chain until `pre` reaches `target` frames.
    fn fill_pre(&mut self, target: usize) {
        let target = target.min(self.total_frames);
        if self.pre.end() >= target {
            return;
        }
        let start = self.pre.end();
        let count = target - start;
        let n_channels = self.params.speakers.len();

        let mut bed = vec![vec![0.0; count]; n_channels];
        let mut lfe_sum = vec![0.0; count];

        for (stem_index, stem) in self.stems.iter().enumerate() {
            let Some(sp) = self.params.stems.get(stem_index) else { continue };
            if !sp.enabled {
                continue;
            }
            let gain = 10.0_f64.powf(sp.rebalance_db / 20.0) * sp.route_scale;
            let route = &mut self.routes[stem_index];

            let mut left = Vec::with_capacity(count);
            let mut right = Vec::with_capacity(count);
            for i in 0..count {
                let frame = start + i;
                left.push(*stem.left.get(frame).unwrap_or(&0.0) as f64);
                right.push(*stem.right.get(frame).unwrap_or(&0.0) as f64);
            }
            if let Some((eq_l, eq_r)) = &mut route.eq {
                left = eq_l.process(&left);
                right = eq_r.process(&right);
            }

            for i in 0..count {
                let shaped = route.tick(left[i], right[i]);
                for (name, weight) in &sp.routing {
                    if *weight == 0.0 {
                        continue;
                    }
                    if name == "LFE" {
                        lfe_sum[i] += shaped[shape_index(super::params::SendShape::Mono)] * weight;
                        continue;
                    }
                    let Some(channel) = self.params.speaker_index(name) else { continue };
                    let speaker = &self.params.speakers[channel];
                    let signal = shaped[shape_index(self.params.shapes[channel])];
                    bed[channel][i] += signal * weight * speaker.group_gain * gain;
                }
            }
        }

        if let Some(lfe) = self.params.lfe_index {
            for (i, v) in lfe_sum.iter().enumerate() {
                bed[lfe][i] += self.lfe_bus.tick(*v);
            }
        }

        if !self.params.bypass_mastering {
            for (channel, block) in bed.iter_mut().enumerate() {
                *block = self.causal[channel].pre_compressor(block);
            }
            let non_lfe = self.non_lfe();
            if let Some(comp) = &mut self.compressor {
                if !non_lfe.is_empty() {
                    for i in 0..count {
                        let gain = comp.tick(linked_rms(&bed, &non_lfe, i));
                        for &ch in &non_lfe {
                            bed[ch][i] *= gain;
                        }
                    }
                }
            }
            for (channel, block) in bed.iter_mut().enumerate() {
                self.causal[channel].band_gains(block);
            }
        }

        for (channel, block) in bed.into_iter().enumerate() {
            self.pre.channels[channel].extend(block);
        }
    }

    /// Run the mono-maker until `post` reaches `target` frames.
    fn fill_post(&mut self, target: usize) {
        let target = target.min(self.total_frames);
        if self.mono_done >= target {
            return;
        }
        let horizon = if self.mono.is_some() { self.mono_horizon } else { 0 };
        self.fill_pre(target + horizon);

        let start = self.mono_done;
        let end = target.min(self.pre.end());
        if end <= start {
            return;
        }

        let mut window: Vec<Vec<f64>> = self
            .pre
            .channels
            .iter()
            .map(|c| c[(start - self.pre.base)..(end - self.pre.base)].to_vec())
            .collect();

        if let Some(mono) = &self.mono {
            // The mono-maker needs context on both sides; hand it the whole
            // live `pre` window and let it read outward from there.
            let mut full: Vec<Vec<f64>> = self.pre.channels.clone();
            mono.process(&mut full, start - self.pre.base, end - self.pre.base);
            window = full
                .iter()
                .map(|c| c[(start - self.pre.base)..(end - self.pre.base)].to_vec())
                .collect();
        }

        // The exciter and LFE trim follow the mono-maker, so they run here
        // rather than in the causal front — see `CausalChain::post_mono`.
        let lfe_gain_db = self.params.master.bass.map(|b| b.lfe_gain_db).unwrap_or(0.0);
        for (channel, mut block) in window.into_iter().enumerate() {
            if !self.params.bypass_mastering {
                self.causal[channel].post_mono(&mut block, lfe_gain_db);
            }
            self.post.channels[channel].extend(block);
        }
        self.mono_done = end;
    }

    /// Render `n_frames` of the mastered bed into `out`, channel-major.
    ///
    /// Returns the number of frames actually written; a short count means the
    /// programme ended.
    pub fn render(&mut self, out: &mut [f64], n_frames: usize) -> usize {
        let n_channels = self.params.speakers.len();
        let available = self.total_frames.saturating_sub(self.emitted);
        let emit = n_frames.min(available);
        out[..n_channels * n_frames].fill(0.0);
        if emit == 0 {
            return 0;
        }

        let lookahead = self.limiter.as_ref().map(|l| l.required_lookahead()).unwrap_or(0);
        self.fill_post(self.emitted + emit + lookahead);

        let start = self.emitted - self.post.base;
        let end = start + emit;
        if let Some(limiter) = &mut self.limiter {
            limiter.process(&mut self.post.channels, start, end);
        }

        let output_gain = self.params.master.output_gain;
        for channel in 0..n_channels {
            let src = &self.post.channels[channel][start..end];
            let dst = &mut out[channel * n_frames..channel * n_frames + emit];
            for (d, s) in dst.iter_mut().zip(src.iter()) {
                *d = s * output_gain;
            }
        }

        self.emitted += emit;
        self.post.drain_to(self.emitted);
        self.pre.drain_to(self.emitted.saturating_sub(self.mono_horizon));
        emit
    }

    /// Reset transport and every filter state to the top of the programme.
    pub fn rewind(&mut self) {
        for route in &mut self.routes {
            route.reset();
        }
        self.lfe_bus.reset();
        if let Some(comp) = &mut self.compressor {
            comp.reset();
        }
        let n_channels = self.params.speakers.len();
        self.causal = (0..n_channels)
            .map(|i| {
                CausalChain::new(self.sample_rate, &self.params.master, self.params.lfe_index == Some(i))
            })
            .collect();
        if let Some(l) = self.params.master.limiter {
            self.limiter = Some(StreamingLimiter::new(l, self.sample_rate, n_channels));
        }
        self.pre = Queue::new(n_channels);
        self.post = Queue::new(n_channels);
        self.mono_done = 0;
        self.emitted = 0;
    }
}
