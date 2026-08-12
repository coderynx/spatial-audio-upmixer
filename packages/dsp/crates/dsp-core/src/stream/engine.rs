//! The preview engine: stems in, mastered speaker bed out, block by block.
//!
//! The worklet owns the decoded stems, so the engine can always look ahead.
//! Two queues carry that look-ahead: `pre` holds the causal chain's output
//! and feeds the mono-maker's zero-phase pass, `post` holds the mono-maker's
//! output and feeds the limiter's forward-window minimum. Nothing is emitted
//! until its full look-ahead exists, which is what lets both stages be the
//! offline algorithm rather than a causal approximation of one.

use std::sync::Arc;

use super::master::{linked_rms, CausalChain, MonoMaker, StreamingLimiter, MONO_HORIZON_MS};
use super::meters::{Level, Meters};
use super::output::OutputStage;
use super::params::EngineParams;
use super::routing::{shape_index, LfeBus, StemRouteState};
use super::state::StreamingCompressor;

/// Run-up rendered and discarded before a seek lands, long enough to cover
/// the Haas delays, the compressor's release, and the mono-maker's horizon.
const SEEK_PREROLL_MS: f64 = 500.0;

/// Frames the mono-maker advances per call. Larger amortizes its zero-phase
/// context further but raises the worst-case cost of a single render.
const MONO_STRIDE: usize = 512;


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
    stems: Vec<Arc<StemSource>>,
    routes: Vec<StemRouteState>,
    lfe_bus: LfeBus,
    causal: Vec<CausalChain>,
    compressor: Option<StreamingCompressor>,
    mono: Option<MonoMaker>,
    limiter: Option<StreamingLimiter>,
    output: OutputStage,
    collapsed: Vec<Vec<f64>>,
    pre: Queue,
    post: Queue,
    mono_done: usize,
    emitted: usize,
    mono_horizon: usize,
    total_frames: usize,
    meters: Meters,
}

impl PreviewEngine {
    pub fn new(sample_rate: u32, params: EngineParams, stems: Vec<Arc<StemSource>>) -> Self {
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

        let output = OutputStage::new(sample_rate, &params);
        Self {
            sample_rate,
            lfe_bus: LfeBus::new(sample_rate, &params.sends),
            collapsed: vec![Vec::new(); n_channels.max(2)],
            output,
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
            meters: Meters::default(),
        }
    }

    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    /// Add a decoded stem, in the order its entry appears in `params.stems`.
    pub fn push_stem(&mut self, stem: StemSource) {
        self.total_frames = self.total_frames.max(stem.len());
        self.stems.push(Arc::new(stem));
    }

    /// A second engine over the same stems and parameters, at the top of the
    /// programme. Used to measure without disturbing the live transport; the
    /// stems are shared, not copied, so this costs filter state only.
    pub fn fork(&self) -> Self {
        Self::new(self.sample_rate, self.params.clone(), self.stems.clone())
    }

    pub fn total_frames(&self) -> usize {
        self.total_frames
    }

    /// Channels the collapse writes, which is two for every mode but native.
    pub fn output_channels(&self) -> usize {
        self.output.output_channels()
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
        if self.mono_done >= target.min(self.total_frames) {
            return;
        }
        // The mono-maker's zero-phase pass filters `horizon` samples either
        // side of what it emits, so emitting a render quantum at a time would
        // redo that context ~75 times over. Advance in strides instead.
        let target = target.max(self.mono_done + MONO_STRIDE).min(self.total_frames);
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
        let available = self.total_frames.saturating_sub(self.emitted);
        let emit = n_frames.min(available);
        let out_channels = self.output.output_channels();
        let span = (out_channels * n_frames).min(out.len());
        out[..span].fill(0.0);
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

        let window: Vec<Vec<f64>> = self
            .post
            .channels
            .iter()
            .map(|c| c[start..end].to_vec())
            .collect();
        self.output
            .process(&window, emit, self.params.master.output_gain, &mut self.collapsed);
        for (channel, rendered) in self.collapsed.iter().enumerate().take(out_channels) {
            let base = channel * n_frames;
            let count = emit.min(rendered.len());
            if base + count > out.len() {
                break;
            }
            out[base..base + count].copy_from_slice(&rendered[..count]);
        }

        self.meters.stems = self
            .stems
            .iter()
            .enumerate()
            .map(|(i, stem)| {
                let sp = self.params.stems.get(i);
                let enabled = sp.map(|p| p.enabled).unwrap_or(true);
                if !enabled {
                    return Level::default();
                }
                let gain = sp
                    .map(|p| 10.0_f64.powf(p.rebalance_db / 20.0))
                    .unwrap_or(1.0);
                let to = (self.emitted + emit).min(stem.left.len());
                if self.emitted >= to {
                    return Level::default();
                }
                Level::measure_f32(&stem.left[self.emitted..to], gain)
            })
            .collect();
        self.meters.channels = self
            .post
            .channels
            .iter()
            .map(|c| Level::measure(&c[start..end]))
            .collect();
        self.meters.output = [
            Level::measure(self.collapsed.first().map(|c| &c[..]).unwrap_or(&[])),
            Level::measure(self.collapsed.get(1).map(|c| &c[..]).unwrap_or(&[])),
        ];

        self.emitted += emit;
        self.post.drain_to(self.emitted);
        self.pre.drain_to(self.emitted.saturating_sub(self.mono_horizon));
        emit
    }

    /// Measure the whole collapsed programme: integrated loudness in LKFS and
    /// true peak in dBTP.
    ///
    /// This replaces the browser's old excerpt-sampled estimate (ledger D4)
    /// with the real BS.1770 measurement over the actual render, so the
    /// correction gain the preview applies is the one a bounce would need.
    /// It renders the programme once into memory — two channels for every
    /// collapse mode — and rewinds afterwards, so the transport is untouched.
    pub fn measure(&mut self, weights: &[f64]) -> (f64, f64) {
        let out_channels = self.output_channels();
        self.rewind();

        let block = 8192;
        let mut collected = vec![Vec::new(); out_channels];
        let mut scratch = vec![0.0; out_channels.max(self.params.speakers.len()) * block];
        loop {
            let written = self.render(&mut scratch, block);
            if written == 0 {
                break;
            }
            for (channel, sink) in collected.iter_mut().enumerate() {
                sink.extend_from_slice(&scratch[channel * block..channel * block + written]);
            }
        }
        self.rewind();

        let refs: Vec<&[f64]> = collected.iter().map(|c| c.as_slice()).collect();
        let weighted: Vec<(f64, &[f64])> = refs
            .iter()
            .enumerate()
            .map(|(i, samples)| (weights.get(i).copied().unwrap_or(1.0), *samples))
            .collect();
        (
            crate::loudness::measure_integrated_loudness(&weighted, self.sample_rate),
            crate::loudness::measure_true_peak(&refs),
        )
    }

    /// Levels from the most recent render.
    pub fn meters(&self) -> &Meters {
        &self.meters
    }

    /// Jump to `frame`, warming the filter states up from shortly before it.
    ///
    /// Starting cold would be audible: the surround and height sends are
    /// Haas-delayed by up to 37 ms and would drop out, and the compressor
    /// would re-attack from silence. Rendering a discarded run-up instead
    /// lets every state settle, so a seek lands on the audio the export
    /// would have produced there.
    pub fn seek(&mut self, frame: usize) {
        let target = frame.min(self.total_frames);
        self.rewind();

        let preroll = (self.sample_rate as f64 * SEEK_PREROLL_MS / 1000.0) as usize;
        let start = target.saturating_sub(preroll);
        self.emitted = start;
        self.mono_done = start;
        self.pre.base = start;
        self.post.base = start;

        let block = 4096;
        let width = self.params.speakers.len().max(2);
        let mut scratch = vec![0.0; width * block];
        while self.emitted < target {
            let step = block.min(target - self.emitted);
            if self.render(&mut scratch, step) == 0 {
                break;
            }
        }
    }

    /// Replace the parameter block, keeping the loaded stems and playhead.
    ///
    /// Mute, solo, rebalance, routing, mastering and output-mode changes all
    /// arrive this way, so there is one path for "the mix changed" rather
    /// than a special case per control.
    pub fn update_params(&mut self, params: EngineParams) {
        let position = self.emitted;
        self.params = params;
        self.output = OutputStage::new(self.sample_rate, &self.params);
        let n_channels = self.params.speakers.len();
        self.collapsed = vec![Vec::new(); n_channels.max(2)];
        self.routes = self
            .params
            .stems
            .iter()
            .map(|s| StemRouteState::new(self.sample_rate, &self.params.sends, &s.eq_fir))
            .collect();
        self.compressor = self
            .params
            .master
            .compressor
            .map(|c| StreamingCompressor::new(c, self.sample_rate));
        self.mono = self
            .params
            .master
            .bass
            .and_then(|b| b.mono_cutoff_hz)
            .map(|hz| MonoMaker::new(self.sample_rate, hz, self.params.master.stereo_pairs.clone()));
        self.seek(position);
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
        self.output = OutputStage::new(self.sample_rate, &self.params);
        self.pre = Queue::new(n_channels);
        self.post = Queue::new(n_channels);
        self.mono_done = 0;
        self.emitted = 0;
    }
}
