//! The preview engine: stems in, mastered speaker bed out, block by block.
//!
//! The worklet owns the decoded stems, so the engine can always look ahead.
//! Two queues carry that look-ahead: `pre` holds the causal chain's output
//! and feeds the LF unifier's zero-phase pass, `post` holds the LF unifier's
//! output and feeds the limiter's forward-window minimum. Nothing is emitted
//! until its full look-ahead exists, which is what lets both stages be the
//! offline algorithm rather than a causal approximation of one.

mod analysis;
mod params_update;
mod transport;

use std::sync::Arc;

use crate::kernels::fft::RealFft;
use crate::kernels::stft::hann_periodic;

use crate::stream::master::{
    CausalChain, LfUnifier, StreamingDecorrelator, StreamingLimiter,
};
use crate::stream::meters::{Level, Meters};
use crate::stream::output::OutputStage;
use crate::stream::params::EngineParams;
use crate::stream::params::SendShape;
use crate::stream::routing::{shape_index, LfeBus, StemRouteState};
use crate::stream::state::{OnePole, StreamingCompressor};

/// Run-up rendered and discarded before a seek lands, long enough to cover
/// the Haas delays, the compressor's release, and the LF unifier's horizon.
const SEEK_PREROLL_MS: f64 = 500.0;

/// Frames the LF unifier advances per call. Larger amortizes its zero-phase
/// context further but raises the worst-case cost of a single render.
const UNIFY_STRIDE: usize = 512;

/// Metering window, in frames — matches the pre-Rust preview's 2048-sample
/// analyser tap. `render()` runs one audio-worklet quantum (128 frames) at a
/// time but the worklet only reports at ~30Hz, so measuring just the latest
/// quantum would mostly discard the frames rendered between reports and read
/// as flicker rather than a level; measuring this wider trailing window
/// keeps every report representative of what was actually just heard.
const METER_WINDOW_FRAMES: usize = 2048;

/// Time constant for smoothing a stem's mute/solo/rebalance gain and the
/// master output gain across a live [`PreviewEngine::update_params`] edit,
/// so the step lands as a fast ramp rather than a click.
const GAIN_RAMP_MS: f64 = 8.0;

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
    unifier: Option<LfUnifier>,
    decorrelator: Option<StreamingDecorrelator>,
    limiter: Option<StreamingLimiter>,
    output: OutputStage,
    /// Set once the host ships the decode/XTC banks over their own binary
    /// channel (`set_decode_taps`/`set_xtc_taps`); overrides whatever
    /// `params.decode_taps`/`xtc_taps` carries. `None` falls back to the
    /// JSON-embedded taps, which keeps every offline/test caller that never
    /// touches the setters working unchanged.
    decode_taps_override: Option<Vec<f64>>,
    xtc_taps_override: Option<Vec<f64>>,
    collapsed: Vec<Vec<f64>>,
    /// One smoother per stem, tracking its mute/solo/rebalance gain.
    stem_gain: Vec<OnePole>,
    master_gain: OnePole,
    pre: Queue,
    post: Queue,
    /// One channel per stem, carrying its per-frame duck gain. Routing runs a
    /// whole look-ahead horizon ahead of what is emitted, so the duck readout
    /// has to be queued and read back at the emit position like every other
    /// meter, or it would flash before the hit that caused it.
    duck: Queue,
    unify_done: usize,
    emitted: usize,
    total_frames: usize,
    meters: Meters,
    /// Trailing `METER_WINDOW_FRAMES` of the collapsed output, kept only for
    /// metering — `collapsed` itself holds just the current call's frames.
    output_meter_tail: Vec<Vec<f64>>,
    /// Cached plan for `stem_spectrum`'s centroid FFT — built once at
    /// `METER_WINDOW_FRAMES` length rather than replanned on every call.
    spectrum_fft: RealFft,
    spectrum_window: Vec<f64>,
}

/// `OutputStage::new` reads its taps from whichever engine field currently
/// owns them — the persistent override if the host has set one, otherwise
/// the parameter block's own `decode_taps`/`xtc_taps`.
/// The unifier exists only when the bass block asks for a crossover and the
/// caller resolved somewhere to put the low end.
pub(super) fn build_unifier(
    sample_rate: u32,
    n_channels: usize,
    params: &EngineParams,
    base: usize,
) -> Option<LfUnifier> {
    let bass = params.master.bass?;
    bass.unify_hz?;
    if params.master.lf_targets.is_empty() {
        return None;
    }
    Some(LfUnifier::new(
        sample_rate,
        n_channels,
        params.lfe_index,
        bass,
        params.master.lf_targets.clone(),
        base,
    ))
}

/// Mid-bass decorrelation exists only when the bass block asks for it. Its
/// band split carries a forward pass across calls, so it has to be told which
/// frame the source it will be fed starts at.
pub(super) fn build_decorrelator(
    sample_rate: u32,
    n_channels: usize,
    params: &EngineParams,
    base: usize,
) -> Option<StreamingDecorrelator> {
    let bass = params.master.bass?;
    StreamingDecorrelator::new(sample_rate, n_channels, params.lfe_index, &bass, base)
}

fn build_output(
    sample_rate: u32,
    params: &EngineParams,
    decode_override: &Option<Vec<f64>>,
    xtc_override: &Option<Vec<f64>>,
) -> OutputStage {
    OutputStage::new(
        sample_rate,
        params,
        decode_override.as_deref().unwrap_or(&params.decode_taps),
        xtc_override.as_deref().unwrap_or(&params.xtc_taps),
    )
}

impl PreviewEngine {
    pub fn new(sample_rate: u32, params: EngineParams, stems: Vec<Arc<StemSource>>) -> Self {
        let n_channels = params.speakers.len();
        let params_stems = params.stems.len();
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
            .map(|c| StreamingCompressor::new(c, sample_rate, n_channels));
        let unifier = build_unifier(sample_rate, n_channels, &params, 0);
        let decorrelator = build_decorrelator(sample_rate, n_channels, &params, 0);
        let limiter = params
            .master
            .limiter
            .map(|l| StreamingLimiter::new(l, sample_rate, n_channels));
        let total_frames = stems.iter().map(|s| s.len()).max().unwrap_or(0);

        let stem_gain = params
            .stems
            .iter()
            .map(|s| {
                let target = if s.enabled {
                    10.0_f64.powf(s.rebalance_db / 20.0) * s.route_scale
                } else {
                    0.0
                };
                OnePole::new_at(GAIN_RAMP_MS, sample_rate as f64, target)
            })
            .collect();
        let master_gain = OnePole::new_at(GAIN_RAMP_MS, sample_rate as f64, params.master.output_gain);

        let decode_taps_override = None;
        let xtc_taps_override = None;
        let output = build_output(sample_rate, &params, &decode_taps_override, &xtc_taps_override);
        Self {
            sample_rate,
            lfe_bus: LfeBus::new(sample_rate, &params.sends),
            collapsed: vec![Vec::new(); n_channels.max(2)],
            stem_gain,
            master_gain,
            output,
            decode_taps_override,
            xtc_taps_override,
            params,
            stems,
            routes,
            causal,
            compressor,
            unifier,
            decorrelator,
            limiter,
            pre: Queue::new(n_channels),
            post: Queue::new(n_channels),
            duck: Queue::new(params_stems.max(1)),
            unify_done: 0,
            emitted: 0,
            total_frames,
            meters: Meters::default(),
            output_meter_tail: vec![Vec::new(); 2],
            spectrum_fft: RealFft::new(METER_WINDOW_FRAMES),
            spectrum_window: hann_periodic(METER_WINDOW_FRAMES),
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
        let mut engine = Self::new(self.sample_rate, self.params.clone(), self.stems.clone());
        engine.decode_taps_override = self.decode_taps_override.clone();
        engine.xtc_taps_override = self.xtc_taps_override.clone();
        engine.output = build_output(
            engine.sample_rate,
            &engine.params,
            &engine.decode_taps_override,
            &engine.xtc_taps_override,
        );
        engine
    }

    /// Replace the binaural decode bank, independent of `update_params` — it
    /// travels its own channel because it is large (order-3 ambisonics is 16
    /// channels x 2 ears x several thousand taps) and changes only when the
    /// spatial profile does, unlike the rest of the mix.
    pub fn set_decode_taps(&mut self, taps: Vec<f64>) {
        self.decode_taps_override = Some(taps);
        self.output = build_output(self.sample_rate, &self.params, &self.decode_taps_override, &self.xtc_taps_override);
    }

    /// Replace the crosstalk-cancellation matrix. See `set_decode_taps`.
    pub fn set_xtc_taps(&mut self, taps: Vec<f64>) {
        self.xtc_taps_override = Some(taps);
        self.output = build_output(self.sample_rate, &self.params, &self.decode_taps_override, &self.xtc_taps_override);
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
            let target_gain = if sp.enabled {
                10.0_f64.powf(sp.rebalance_db / 20.0) * sp.route_scale
            } else {
                0.0
            };
            let smoother = &mut self.stem_gain[stem_index];
            if !sp.enabled && smoother.is_settled(0.0) {
                // Already faded out and staying muted — skip the routing and
                // EQ work entirely, same as the old hard cut did.
                continue;
            }
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

            let mut needs_surround = false;
            let mut needs_height = false;
            for (name, weight) in &sp.routing {
                let Some(channel) = self.params.speaker_index(name).filter(|_| *weight != 0.0)
                else {
                    continue;
                };
                match self.params.shapes[channel] {
                    SendShape::SurroundLeft | SendShape::SurroundRight => needs_surround = true,
                    SendShape::HeightLeft | SendShape::HeightRight => needs_height = true,
                    _ => {}
                }
            }
            route.process(&left, &right, needs_surround, needs_height);
            self.duck.channels[stem_index].extend_from_slice(route.duck_trace());

            for i in 0..count {
                let gain = smoother.tick(target_gain);
                let shaped = [
                    left[i],
                    right[i],
                    (left[i] + right[i]) * 0.5,
                    route.send(0)[i],
                    route.send(1)[i],
                    route.send(2)[i],
                    route.send(3)[i],
                ];
                for (name, weight) in &sp.routing {
                    if *weight == 0.0 {
                        continue;
                    }
                    if name == "LFE" {
                        lfe_sum[i] += shaped[shape_index(SendShape::Mono)] * weight * gain;
                        continue;
                    }
                    let Some(channel) = self.params.speaker_index(name) else { continue };
                    let speaker = &self.params.speakers[channel];
                    let signal = shaped[shape_index(self.params.shapes[channel])];
                    bed[channel][i] += signal * weight * speaker.group_gain * gain;
                }
            }
        }

        // A stem skipped above (muted and settled, or with no parameters)
        // still has to advance in lockstep, at unity.
        let duck_len = start + count - self.duck.base;
        for trace in &mut self.duck.channels {
            trace.resize(duck_len, 1.0);
        }

        if let Some(lfe) = self.params.lfe_index {
            let group_gain = self.params.speakers[lfe].group_gain;
            for (i, v) in lfe_sum.iter().enumerate() {
                bed[lfe][i] += self.lfe_bus.tick(*v) * group_gain;
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
                        let rms = comp.linked_rms(&bed, &non_lfe, i);
                        let gain = comp.tick(rms);
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

    /// Samples of `pre` both stages need ahead of what they emit.
    fn look_ahead(&self) -> usize {
        let unify = self.unifier.as_ref().map_or(0, |u| u.look_ahead());
        let decorr = self.decorrelator.as_ref().map_or(0, |d| d.look_ahead());
        unify.max(decorr)
    }

    /// Run the LF unifier until `post` reaches `target` frames.
    fn fill_post(&mut self, target: usize) {
        if self.unify_done >= target.min(self.total_frames) {
            return;
        }
        // The LF unifier's zero-phase pass filters `horizon` samples either
        // side of what it emits, so emitting a render quantum at a time would
        // redo that context ~75 times over. Advance in strides instead.
        let target = target.max(self.unify_done + UNIFY_STRIDE).min(self.total_frames);
        let horizon = self.look_ahead();
        self.fill_pre(target + horizon);

        let start = self.unify_done;
        let end = target.min(self.pre.end());
        if end <= start {
            return;
        }

        let base = self.pre.base;
        let mut window: Vec<Vec<f64>> = self
            .pre
            .channels
            .iter()
            .map(|c| c[(start - base)..(end - base)].to_vec())
            .collect();

        if let Some(unifier) = &mut self.unifier {
            unifier.process(
                &self.pre.channels,
                base,
                self.total_frames,
                &mut window,
                start,
                end,
            );
        }

        // Reads its band out of `pre`, i.e. from before unification, which is
        // the order `bass_control` runs offline.
        if let Some(decorrelator) = &mut self.decorrelator {
            if !self.params.bypass_mastering {
                decorrelator.process(
                    &self.pre.channels,
                    base,
                    self.total_frames,
                    &mut window,
                    start,
                    end,
                );
            }
        }

        // The LFE trim follows the LF unifier, so it runs here rather than in
        // the causal front — see `CausalChain::lfe_trim`.
        let lfe_gain_db = self.params.master.bass.map(|b| b.lfe_gain_db).unwrap_or(0.0);
        for (channel, mut block) in window.into_iter().enumerate() {
            if !self.params.bypass_mastering {
                self.causal[channel].lfe_trim(&mut block, lfe_gain_db);
            }
            self.post.channels[channel].extend(block);
        }
        self.unify_done = end;
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

        // Monitor mute lands here, on the finished bed: every shared stage
        // above (bass bus, linked compressor, limiter) has already run, so
        // silencing one speaker cannot change what the others get.
        let window: Vec<Vec<f64>> = self
            .post
            .channels
            .iter()
            .enumerate()
            .map(|(channel, c)| {
                if self.params.speakers.get(channel).is_some_and(|s| s.muted) {
                    vec![0.0; end - start]
                } else {
                    c[start..end].to_vec()
                }
            })
            .collect();
        // Block-quantized rather than per-sample smoothing: output_gain is a
        // scalar loudness/true-peak correction that changes rarely (mostly
        // from the measurement pass, not a live user gesture), so ramping it
        // once per render call is enough to hide the step without threading
        // a per-sample gain array through the collapse stage.
        let gain = self.master_gain.advance(self.params.master.output_gain, emit);
        self.output.process(&window, emit, gain, &mut self.collapsed);
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
                    return [Level::default(), Level::default()];
                }
                let gain = sp
                    .map(|p| 10.0_f64.powf(p.rebalance_db / 20.0))
                    .unwrap_or(1.0);
                let to = (self.emitted + emit).min(stem.len());
                if self.emitted >= to {
                    return [Level::default(), Level::default()];
                }
                let win_start = to.saturating_sub(METER_WINDOW_FRAMES);
                [
                    Level::measure_f32(&stem.left[win_start..to], gain),
                    Level::measure_f32(&stem.right[win_start..to], gain),
                ]
            })
            .collect();
        let meter_start = end.saturating_sub(METER_WINDOW_FRAMES);
        self.meters.channels = self
            .post
            .channels
            .iter()
            .enumerate()
            .map(|(channel, c)| {
                if self.params.speakers.get(channel).is_some_and(|s| s.muted) {
                    Level::default()
                } else {
                    Level::measure(&c[meter_start..end])
                }
            })
            .collect();
        for (channel, tail) in self.output_meter_tail.iter_mut().enumerate() {
            if let Some(rendered) = self.collapsed.get(channel) {
                tail.extend_from_slice(&rendered[..emit.min(rendered.len())]);
            }
            let drop = tail.len().saturating_sub(METER_WINDOW_FRAMES);
            tail.drain(..drop);
        }
        self.meters.output = [
            Level::measure(&self.output_meter_tail[0]),
            Level::measure(&self.output_meter_tail[1]),
        ];

        self.emitted += emit;
        self.post.drain_to(self.emitted.saturating_sub(METER_WINDOW_FRAMES));
        self.duck.drain_to(self.emitted.saturating_sub(METER_WINDOW_FRAMES));
        self.pre.drain_to(self.emitted.saturating_sub(self.look_ahead()));
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
        self.unifier = build_unifier(self.sample_rate, n_channels, &self.params, 0);
        self.decorrelator = build_decorrelator(self.sample_rate, n_channels, &self.params, 0);
        self.causal = (0..n_channels)
            .map(|i| {
                CausalChain::new(self.sample_rate, &self.params.master, self.params.lfe_index == Some(i))
            })
            .collect();
        self.limiter = self.params.master.limiter.map(|l| StreamingLimiter::new(l, self.sample_rate, n_channels));
        self.output = build_output(self.sample_rate, &self.params, &self.decode_taps_override, &self.xtc_taps_override);
        self.pre = Queue::new(n_channels);
        self.post = Queue::new(n_channels);
        self.duck = Queue::new(self.params.stems.len().max(1));
        self.unify_done = 0;
        self.emitted = 0;
    }
}
