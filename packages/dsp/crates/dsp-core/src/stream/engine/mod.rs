//! The preview engine: stems in, mastered speaker bed out, block by block.
//!
//! The worklet owns the decoded stems, so the engine can always look ahead.
//! This module holds the engine's state and its lifecycle; the render loop
//! and the look-ahead queues that feed it live in [`render`].

mod analysis;
mod mix;
mod params_update;
pub(crate) mod render;
mod transport;

use std::sync::Arc;

use crate::kernels::fft::RealFft;
use crate::kernels::stft::hann_periodic;
use crate::loudness_stream::WindowLoudnessMeter;
use crate::mastering::dyneq::DynamicEq;
use crate::spatial::downmix::FoldTo51;
use crate::spatial::panner::PannerLayout;

use crate::stream::limiter::StreamingLimiter;
use crate::stream::master::{
    CausalChain, LfUnifier, StreamingDecorrelator, DECORR_HORIZON_MS, UNIFY_HORIZON_MS,
};
use crate::stream::meters::Meters;
use crate::stream::output::OutputStage;
use crate::stream::params::EngineParams;
use crate::stream::routing::{LfeBus, StemRouteState};
use crate::stream::state::{OnePole, StreamingCompressor};
use mix::{build_stem_mix_routes, StemMixRoute};

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
pub(super) const GAIN_RAMP_MS: f64 = 8.0;

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
        Self {
            base: 0,
            channels: vec![Vec::new(); n_channels],
        }
    }

    fn end(&self) -> usize {
        self.base + self.channels[0].len()
    }

    fn drain_to(&mut self, absolute: usize) {
        let keep_from = absolute
            .saturating_sub(self.base)
            .min(self.channels[0].len());
        if keep_from < 128 {
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
    panner_layout: PannerLayout,
    stem_mix_routes: Vec<StemMixRoute>,
    authored_channels: usize,
    rendered_channels: Vec<usize>,
    speaker_render_scratch: Vec<Vec<f64>>,
    /// Per-stem route normalization once `RouteScalePass` has measured it.
    /// Empty until then, and cleared whenever a parameter the routing reads
    /// moves, so the host's estimate stands in rather than a stale
    /// measurement of a mix that no longer exists.
    measured_scales: Vec<Option<f64>>,
    lfe_bus: LfeBus,
    causal: Vec<CausalChain>,
    dyn_eq: Option<DynamicEq>,
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
    monitor_gain: OnePole,
    pre: Queue,
    post: Queue,
    /// One channel, carrying the bus compressor's per-frame gain reduction in
    /// dB. Queued and read at the emit position, or it would flash before the
    /// block that caused it.
    comp_gr: Queue,
    unify_stride: usize,
    unify_done: usize,
    emitted: usize,
    seek_target: Option<usize>,
    seek_scratch: Vec<f64>,
    total_frames: usize,
    meters: Meters,
    /// EBU Tech 3341 windows over the delivered programme, or `None` when the
    /// engine has no channels to meter.
    loudness: Option<WindowLoudnessMeter>,
    /// The 5.1 re-render the loudness windows read, matching what a
    /// `MeasurementPass` measures on this output (see `measurement_fold`).
    meter_fold: Option<FoldTo51>,
    meter_folded: Vec<Vec<f64>>,
    /// `(frames, mains GR dB, LFE GR dB)` per render call, trimmed to the
    /// meter window — one render quantum of limiter GR on its own would miss
    /// every peak between two ~30 Hz reports.
    limiter_gr: Vec<(usize, f64, f64)>,
    /// Trailing `METER_WINDOW_FRAMES` of the collapsed output, kept only for
    /// metering — `collapsed` itself holds just the current call's frames.
    output_meter_tail: Vec<Vec<f64>>,
    /// Cached plan for `stem_spectrum`'s centroid FFT — built once at
    /// `METER_WINDOW_FRAMES` length rather than replanned on every call.
    spectrum_fft: RealFft,
    spectrum_window: Vec<f64>,
    render_scratch: Vec<f64>,
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

fn mastering_topology(
    n_speakers: usize,
    lfe: Option<usize>,
    routes: &[StemMixRoute],
) -> (usize, Vec<usize>, usize) {
    let authored = routes
        .iter()
        .flat_map(|route| {
            route
                .objects
                .iter()
                .flatten()
                .map(|object| object.authored_channel + 1)
        })
        .max()
        .unwrap_or(n_speakers);
    let mut next_rendered = authored;
    let rendered = (0..n_speakers)
        .map(|channel| {
            if authored == n_speakers || lfe == Some(channel) {
                channel
            } else {
                let index = next_rendered;
                next_rendered += 1;
                index
            }
        })
        .collect();
    (authored, rendered, next_rendered)
}

impl PreviewEngine {
    pub fn new(sample_rate: u32, params: EngineParams, stems: Vec<Arc<StemSource>>) -> Self {
        let n_channels = params.speakers.len();
        let speaker_names: Vec<&str> = params
            .speakers
            .iter()
            .map(|speaker| speaker.name.as_str())
            .collect();
        let panner_layout = PannerLayout::new(&speaker_names);
        let stem_mix_routes = build_stem_mix_routes(&params, &panner_layout);
        let (authored_channels, rendered_channels, post_channels) =
            mastering_topology(n_channels, params.lfe_index, &stem_mix_routes);
        let routes = params
            .stems
            .iter()
            .map(|s| params_update::build_route(sample_rate, &params.sends, s))
            .collect();
        let causal = (0..authored_channels)
            .map(|i| CausalChain::new(sample_rate, &params.master, params.lfe_index == Some(i)))
            .collect();
        let dyn_eq = if authored_channels > n_channels {
            DynamicEq::new_linked(
                sample_rate,
                authored_channels,
                params.lfe_index,
                n_channels,
                params.lfe_index,
                &params.master.dynamic_eq,
            )
        } else {
            DynamicEq::new(
                sample_rate,
                n_channels,
                params.lfe_index,
                &params.master.dynamic_eq,
            )
        };
        let compressor = params.master.compressor.map(|c| {
            StreamingCompressor::new(
                c,
                sample_rate,
                if authored_channels > n_channels {
                    n_channels
                } else {
                    authored_channels
                },
            )
        });
        let unifier = build_unifier(sample_rate, n_channels, &params, 0);
        let decorrelator = build_decorrelator(sample_rate, n_channels, &params, 0);
        let limiter = params
            .master
            .limiter
            .map(|l| StreamingLimiter::new(l, sample_rate, post_channels, params.lfe_index));
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
        let master_gain =
            OnePole::new_at(GAIN_RAMP_MS, sample_rate as f64, params.master.output_gain);
        let monitor_gain = OnePole::new_at(
            GAIN_RAMP_MS,
            sample_rate as f64,
            params.master.monitor_output_gain,
        );

        let decode_taps_override = None;
        let xtc_taps_override = None;
        let output = build_output(
            sample_rate,
            &params,
            &decode_taps_override,
            &xtc_taps_override,
        );
        let mut engine = Self {
            sample_rate,
            lfe_bus: LfeBus::new(sample_rate, &params.sends),
            collapsed: vec![Vec::new(); n_channels.max(2)],
            stem_gain,
            master_gain,
            monitor_gain,
            output,
            decode_taps_override,
            xtc_taps_override,
            params,
            stems,
            routes,
            panner_layout,
            stem_mix_routes,
            authored_channels,
            rendered_channels,
            speaker_render_scratch: vec![Vec::new(); n_channels],
            measured_scales: Vec::new(),
            causal,
            dyn_eq,
            compressor,
            unifier,
            decorrelator,
            limiter,
            pre: Queue::new(authored_channels),
            post: Queue::new(post_channels),
            comp_gr: Queue::new(1),
            unify_stride: if authored_channels > n_channels {
                UNIFY_STRIDE / 2
            } else {
                UNIFY_STRIDE
            },
            unify_done: 0,
            emitted: 0,
            seek_target: None,
            seek_scratch: Vec::new(),
            total_frames,
            meters: Meters::default(),
            loudness: None,
            meter_fold: None,
            meter_folded: Vec::new(),
            limiter_gr: Vec::new(),
            output_meter_tail: vec![Vec::new(); 2],
            spectrum_fft: RealFft::new(METER_WINDOW_FRAMES),
            spectrum_window: hann_periodic(METER_WINDOW_FRAMES),
            render_scratch: Vec::new(),
        };
        engine.rebuild_loudness_meter();
        engine.prime_output(128);
        engine
    }

    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    /// Add a decoded stem, in the order its entry appears in `params.stems`.
    pub fn push_stem(&mut self, stem: StemSource) {
        self.total_frames = self.total_frames.max(stem.len());
        self.stems.push(Arc::new(stem));
    }

    /// One stem's routing state, for a caller reading the signals
    /// [`Self::route_stem_block`] just produced.
    pub fn route(&self, index: usize) -> &crate::stream::routing::StemRouteState {
        &self.routes[index]
    }

    /// Stems that have both a source and a parameter block, which is what a
    /// caller walking them one at a time can safely index.
    pub fn stem_count(&self) -> usize {
        self.stems.len().min(self.params.stems.len())
    }

    pub fn stem_params(&self, index: usize) -> Option<&crate::stream::params::StemParams> {
        self.params.stems.get(index)
    }

    pub fn params(&self) -> &EngineParams {
        &self.params
    }

    /// The normalization one stem renders at: the measured scalar once the
    /// route-scale pass has produced it, and the host's estimate until then.
    pub fn route_scale(&self, index: usize) -> f64 {
        self.measured_scales
            .get(index)
            .copied()
            .flatten()
            .unwrap_or_else(|| self.params.stems.get(index).map_or(1.0, |s| s.route_scale))
    }

    /// Whether a route-scale measurement is standing behind what this engine
    /// renders.
    pub fn has_route_scales(&self) -> bool {
        !self.measured_scales.is_empty()
    }

    /// Adopt a finished route-scale measurement.
    pub fn set_route_scales(&mut self, scales: &[f64]) {
        self.measured_scales = scales.iter().map(|s| Some(*s)).collect();
    }

    /// Drop any measured scales, so the host's estimate stands until a new
    /// pass lands. Called when a parameter the routing reads has moved.
    pub fn clear_route_scales(&mut self) {
        self.measured_scales.clear();
    }

    /// An analysis engine over the same programme, without monitor controls.
    /// The stems are shared, not copied, so this costs filter state only.
    pub fn fork(&self) -> Self {
        let mut params = self.params.clone();
        params.master.output_gain = 1.0;
        if self.authored_channels > params.speakers.len() {
            params.output_mode = crate::stream::params::OutputMode::Native;
        }
        for speaker in &mut params.speakers {
            speaker.muted = false;
        }
        if let Some(taps) = &self.decode_taps_override {
            params.decode_taps = taps.clone();
        }
        if let Some(taps) = &self.xtc_taps_override {
            params.xtc_taps = taps.clone();
        }
        let mut engine = Self::new(self.sample_rate, params, self.stems.clone());
        engine.decode_taps_override = self.decode_taps_override.clone();
        engine.xtc_taps_override = self.xtc_taps_override.clone();
        engine.measured_scales = self.measured_scales.clone();
        engine
    }

    pub(crate) fn measurement_monitor_stage(&self) -> Option<OutputStage> {
        if self.authored_channels <= self.params.speakers.len()
            || self.params.output_mode == crate::stream::params::OutputMode::Native
        {
            return None;
        }
        let mut params = self.params.clone();
        params.soft_limit_threshold = 0.0;
        Some(build_output(
            self.sample_rate,
            &params,
            &self.decode_taps_override,
            &self.xtc_taps_override,
        ))
    }

    /// Render directly into the Web Audio sample format without allocating a
    /// temporary f64 block for every worklet callback.
    pub fn render_f32(&mut self, out: &mut [f32], n_frames: usize) -> usize {
        let mut scratch = std::mem::take(&mut self.render_scratch);
        scratch.resize(self.output.output_channels() * n_frames, 0.0);
        let written = self.render(&mut scratch, n_frames);
        for (dst, source) in out.iter_mut().zip(&scratch) {
            *dst = *source as f32;
        }
        self.render_scratch = scratch;
        written
    }

    /// Replace the binaural decode bank, independent of `update_params` — it
    /// travels its own channel because it is large (order-3 ambisonics is 16
    /// channels x 2 ears x several thousand taps) and changes only when the
    /// spatial profile does, unlike the rest of the mix.
    pub fn set_decode_taps(&mut self, taps: Vec<f64>) {
        self.decode_taps_override = Some(taps);
        self.output = build_output(
            self.sample_rate,
            &self.params,
            &self.decode_taps_override,
            &self.xtc_taps_override,
        );
        self.prime_output(128);
    }

    /// Replace the crosstalk-cancellation matrix. See `set_decode_taps`.
    pub fn set_xtc_taps(&mut self, taps: Vec<f64>) {
        self.xtc_taps_override = Some(taps);
        self.output = build_output(
            self.sample_rate,
            &self.params,
            &self.decode_taps_override,
            &self.xtc_taps_override,
        );
        self.prime_output(128);
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

    pub(crate) fn set_unify_stride(&mut self, stride: usize) {
        self.unify_stride = stride.max(1);
    }

    fn non_lfe(&self) -> Vec<usize> {
        (0..self.authored_channels)
            .filter(|i| self.params.lfe_index != Some(*i))
            .collect()
    }

    /// Reset transport and every filter state to the top of the programme.
    pub fn rewind(&mut self) {
        self.seek_target = None;
        for route in &mut self.routes {
            route.reset();
        }
        self.lfe_bus.reset();
        if let Some(dyn_eq) = &mut self.dyn_eq {
            dyn_eq.reset();
        }
        if let Some(comp) = &mut self.compressor {
            comp.reset();
        }
        let n_channels = self.params.speakers.len();
        self.unifier = build_unifier(self.sample_rate, n_channels, &self.params, 0);
        self.decorrelator = build_decorrelator(self.sample_rate, n_channels, &self.params, 0);
        for chain in &mut self.causal {
            chain.reset();
        }
        let post_channels = self.rendered_channels.iter().max().map_or(0, |i| i + 1);
        self.limiter = self.params.master.limiter.map(|l| {
            StreamingLimiter::new(l, self.sample_rate, post_channels, self.params.lfe_index)
        });
        self.output.reset();
        self.pre = Queue::new(self.authored_channels);
        self.post = Queue::new(post_channels);
        self.comp_gr = Queue::new(1);
        self.limiter_gr.clear();
        self.rebuild_loudness_meter();
        self.unify_done = 0;
        self.emitted = 0;
    }
}
