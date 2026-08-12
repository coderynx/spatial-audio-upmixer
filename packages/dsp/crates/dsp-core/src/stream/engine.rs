//! The preview engine: stems in, mastered speaker bed out, block by block.
//!
//! The worklet owns the decoded stems, so the engine can always look ahead.
//! Two queues carry that look-ahead: `pre` holds the causal chain's output
//! and feeds the mono-maker's zero-phase pass, `post` holds the mono-maker's
//! output and feeds the limiter's forward-window minimum. Nothing is emitted
//! until its full look-ahead exists, which is what lets both stages be the
//! offline algorithm rather than a causal approximation of one.

use std::sync::Arc;

use crate::kernels::fft::RealFft;
use crate::kernels::stft::hann_periodic;

use super::master::{linked_rms, CausalChain, MonoMaker, StreamingLimiter, MONO_HORIZON_MS};
use super::meters::{Level, Meters};
use super::output::OutputStage;
use super::params::EngineParams;
use super::routing::{shape_index, LfeBus, StemRouteState};
use super::state::{OnePole, StreamingCompressor};

/// Run-up rendered and discarded before a seek lands, long enough to cover
/// the Haas delays, the compressor's release, and the mono-maker's horizon.
const SEEK_PREROLL_MS: f64 = 500.0;

/// Frames the mono-maker advances per call. Larger amortizes its zero-phase
/// context further but raises the worst-case cost of a single render.
const MONO_STRIDE: usize = 512;

/// Metering window, in frames — matches the pre-Rust preview's 2048-sample
/// analyser tap. `render()` runs one audio-worklet quantum (128 frames) at a
/// time but the worklet only reports at ~30Hz, so measuring just the latest
/// quantum would mostly discard the frames rendered between reports and read
/// as flicker rather than a level; measuring this wider trailing window
/// keeps every report representative of what was actually just heard.
const METER_WINDOW_FRAMES: usize = 2048;

/// Time constant for smoothing a stem's mute/solo/rebalance gain and the
/// master output gain across a live [`PreviewEngine::update_params`] edit,
/// so the step lands as a fast ramp rather than a click. Matches the deleted
/// `previewGraph.ts`'s `GAIN_RAMP_TIME_CONSTANT` from the pre-Rust preview.
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
    mono: Option<MonoMaker>,
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
    mono_done: usize,
    emitted: usize,
    mono_horizon: usize,
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
            mono,
            limiter,
            pre: Queue::new(n_channels),
            post: Queue::new(n_channels),
            mono_done: 0,
            emitted: 0,
            mono_horizon: (sample_rate as f64 * MONO_HORIZON_MS / 1000.0) as usize,
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

            for i in 0..count {
                let gain = smoother.tick(target_gain);
                let shaped = route.tick(left[i], right[i]);
                for (name, weight) in &sp.routing {
                    if *weight == 0.0 {
                        continue;
                    }
                    if name == "LFE" {
                        lfe_sum[i] += shaped[shape_index(super::params::SendShape::Mono)] * weight * gain;
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
            .map(|c| Level::measure(&c[meter_start..end]))
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

    /// Per-stem `(level, spectral centroid)` for the haze/elevation displays
    /// — both roughly 0..1, `centroid` sqrt-scaled toward the low end like a
    /// listener's own frequency perception. Computed here rather than kept
    /// current in `meters`/`render()`: the FFT this needs is too heavy to
    /// run every 128-frame quantum, but is cheap enough to run at the
    /// worklet's ~30Hz report cadence, called on demand from the same
    /// trailing `METER_WINDOW_FRAMES` window the meters use.
    pub fn stem_spectrum(&self) -> Vec<(f64, f64)> {
        self.stems
            .iter()
            .enumerate()
            .map(|(i, stem)| {
                let sp = self.params.stems.get(i);
                if !sp.map(|p| p.enabled).unwrap_or(true) {
                    return (0.0, 0.0);
                }
                let gain = sp
                    .map(|p| 10.0_f64.powf(p.rebalance_db / 20.0))
                    .unwrap_or(1.0);
                let to = self.emitted.min(stem.len());
                let win_start = to.saturating_sub(METER_WINDOW_FRAMES);
                if to <= win_start {
                    return (0.0, 0.0);
                }

                let level = self
                    .meters
                    .stems
                    .get(i)
                    .map(|pair| ((pair[0].rms + pair[1].rms) * 0.5 * 2.5).min(1.0))
                    .unwrap_or(0.0);

                let mut windowed = vec![0.0; self.spectrum_window.len()];
                for (j, w) in self.spectrum_window.iter().enumerate().take(to - win_start) {
                    let sample = (stem.left[win_start + j] as f64 + stem.right[win_start + j] as f64) * 0.5;
                    windowed[j] = sample * gain * w;
                }
                let bins = self.spectrum_fft.rfft(&windowed);
                let mut weighted = 0.0;
                let mut total = 0.0;
                for (bin, c) in bins.iter().enumerate() {
                    let amplitude = c.norm();
                    weighted += amplitude * bin as f64;
                    total += amplitude;
                }
                let centroid = if total > 0.0 && bins.len() > 1 {
                    ((weighted / total) / (bins.len() - 1) as f64).sqrt()
                } else {
                    0.0
                };
                (level, centroid)
            })
            .collect()
    }

    /// Reset filter states and drop the playhead at `frame`, with no run-up.
    ///
    /// Leaves every filter cold at `frame`, so the caller is responsible for
    /// warming it back up (see [`Self::seek`]) or for a use that tolerates a
    /// cold start, such as a measurement excerpt where a `preroll` of real
    /// audio is rendered and discarded before anything is measured.
    pub(crate) fn jump_to(&mut self, frame: usize) {
        let target = frame.min(self.total_frames);
        self.rewind();
        self.emitted = target;
        self.mono_done = target;
        self.pre.base = target;
        self.post.base = target;
        // A cold jump has rendered nothing at the new position yet, so the
        // last render's levels are stale — reset them rather than reporting
        // whatever was playing before the jump. `seek`'s own preroll render
        // (when there is one) overwrites this with real levels right after;
        // when there isn't one (e.g. landing exactly on frame 0), this is
        // what `stem_spectrum`'s own live-position read already agrees on.
        for pair in &mut self.meters.stems {
            *pair = [Level::default(); 2];
        }
        for level in &mut self.meters.channels {
            *level = Level::default();
        }
        self.meters.output = [Level::default(); 2];
        for tail in &mut self.output_meter_tail {
            tail.clear();
        }
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
        let preroll = (self.sample_rate as f64 * SEEK_PREROLL_MS / 1000.0) as usize;
        self.jump_to(target.saturating_sub(preroll));

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

    /// Replace the parameter block, keeping the loaded stems, the playhead,
    /// and — outside a channel-layout change — every filter's carried state
    /// and both look-ahead queues.
    ///
    /// Mute, solo, rebalance, routing, mastering and output-mode changes all
    /// arrive this way, so there is one path for "the mix changed" rather
    /// than a special case per control. Each stage only re-derives the parts
    /// of itself that actually moved: nothing here re-renders a preroll or
    /// discards `pre`/`post`, so playback never gaps. The new mix reaches
    /// the speakers once the look-ahead already rendered under the old
    /// params has drained — audible lag on the order of the mono-maker's
    /// horizon plus the limiter's lookahead, not audible silence.
    pub fn update_params(&mut self, params: EngineParams) {
        let old = std::mem::replace(&mut self.params, params);

        let topology_changed =
            old.speakers.len() != self.params.speakers.len() || old.lfe_index != self.params.lfe_index;
        if topology_changed {
            // Rare — the web client tears the whole worklet down for a
            // speaker-layout change before this can even fire in practice —
            // so it keeps the old full-rebuild-then-seek behavior rather
            // than earning its own diff logic.
            let position = self.emitted;
            self.rebuild_for_new_topology();
            self.seek(position);
            return;
        }

        if self.routes.len() != self.params.stems.len() {
            self.rebuild_routes();
        }

        let sends_changed = old.sends != self.params.sends;
        if sends_changed {
            self.lfe_bus.retune(self.sample_rate, &self.params.sends);
        }
        for (i, route) in self.routes.iter_mut().enumerate() {
            let new_eq = self.params.stems.get(i).map(|s| s.eq_fir.as_slice()).unwrap_or(&[]);
            let old_eq = old.stems.get(i).map(|s| s.eq_fir.as_slice()).unwrap_or(&[]);
            let eq_changed = new_eq != old_eq;
            if sends_changed || eq_changed {
                route.retune(self.sample_rate, &self.params.sends, new_eq, sends_changed, eq_changed);
            }
        }

        match self.params.master.compressor {
            None => self.compressor = None,
            Some(c) => match &mut self.compressor {
                Some(existing) => existing.retune(c, self.sample_rate),
                None => self.compressor = Some(StreamingCompressor::new(c, self.sample_rate)),
            },
        }

        let old_mono_cutoff = old.master.bass.and_then(|b| b.mono_cutoff_hz);
        let new_mono_cutoff = self.params.master.bass.and_then(|b| b.mono_cutoff_hz);
        if old_mono_cutoff != new_mono_cutoff || old.master.stereo_pairs != self.params.master.stereo_pairs {
            // Stateless across calls (see `MonoMaker::process`), so a plain
            // rebuild is already the cheap path here.
            self.mono = new_mono_cutoff
                .map(|hz| MonoMaker::new(self.sample_rate, hz, self.params.master.stereo_pairs.clone()));
        }

        if old.master != self.params.master {
            for chain in &mut self.causal {
                chain.retune(self.sample_rate, &old.master, &self.params.master);
            }
        }

        match self.params.master.limiter {
            None => self.limiter = None,
            Some(l) if self.limiter.is_none() || old.master.limiter != Some(l) => {
                self.limiter = Some(StreamingLimiter::new(l, self.sample_rate, self.params.speakers.len()));
            }
            Some(_) => {}
        }

        if old.speakers != self.params.speakers
            || old.output_mode != self.params.output_mode
            || old.voicing != self.params.voicing
            || old.soft_limit_threshold != self.params.soft_limit_threshold
        {
            self.output.retune(self.sample_rate, &self.params);
        }
    }

    /// Full rebuild for a channel-count/LFE-position change — every stage
    /// keyed by `n_channels` has to move, so there is nothing cheaper to do
    /// than what [`Self::new`] would build fresh. `pre`/`post`/`causal`/
    /// `output`/`limiter`/`emitted` are left to the `seek` call the caller
    /// makes right after this, whose `rewind` already rebuilds them at the
    /// new topology.
    fn rebuild_for_new_topology(&mut self) {
        let n_channels = self.params.speakers.len();
        self.collapsed = vec![Vec::new(); n_channels.max(2)];
        self.rebuild_routes();
        self.lfe_bus.retune(self.sample_rate, &self.params.sends);
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
    }

    /// Rebuild the per-stem routing state and gain smoothers to match
    /// `self.params.stems` — used when a stem was added or removed, where
    /// there is no previous per-index state to retune.
    fn rebuild_routes(&mut self) {
        self.routes = self
            .params
            .stems
            .iter()
            .map(|s| StemRouteState::new(self.sample_rate, &self.params.sends, &s.eq_fir))
            .collect();
        self.stem_gain = self
            .params
            .stems
            .iter()
            .map(|s| {
                let target = if s.enabled {
                    10.0_f64.powf(s.rebalance_db / 20.0) * s.route_scale
                } else {
                    0.0
                };
                OnePole::new_at(GAIN_RAMP_MS, self.sample_rate as f64, target)
            })
            .collect();
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
        self.limiter = self.params.master.limiter.map(|l| StreamingLimiter::new(l, self.sample_rate, n_channels));
        self.output = build_output(self.sample_rate, &self.params, &self.decode_taps_override, &self.xtc_taps_override);
        self.pre = Queue::new(n_channels);
        self.post = Queue::new(n_channels);
        self.mono_done = 0;
        self.emitted = 0;
    }
}
