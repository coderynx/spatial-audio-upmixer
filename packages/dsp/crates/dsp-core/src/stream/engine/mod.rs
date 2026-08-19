//! The preview engine: stems in, mastered speaker bed out, block by block.
//!
//! The worklet owns the decoded stems, so the engine can always look ahead.
//! This module holds the engine's state and its lifecycle; the render loop
//! and the look-ahead queues that feed it live in [`render`].

mod analysis;
mod params_update;
mod render;
mod transport;

use std::sync::Arc;

use crate::kernels::fft::RealFft;
use crate::kernels::stft::hann_periodic;
use crate::loudness_stream::WindowLoudnessMeter;
use crate::spatial::downmix::FoldTo51;

use crate::stream::master::{
    CausalChain, LfUnifier, StreamingDecorrelator, StreamingLimiter,
};
use crate::stream::meters::Meters;
use crate::stream::output::OutputStage;
use crate::stream::params::EngineParams;
use crate::stream::routing::{LfeBus, StemRouteState};
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
    /// One channel, carrying the bus compressor's per-frame gain reduction in
    /// dB. Queued and read at the emit position for the same reason `duck` is.
    comp_gr: Queue,
    unify_done: usize,
    emitted: usize,
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
            .map(|l| StreamingLimiter::new(l, sample_rate, n_channels, params.lfe_index));
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
        let mut engine = Self {
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
            comp_gr: Queue::new(1),
            unify_done: 0,
            emitted: 0,
            total_frames,
            meters: Meters::default(),
            loudness: None,
            meter_fold: None,
            meter_folded: Vec::new(),
            limiter_gr: Vec::new(),
            output_meter_tail: vec![Vec::new(); 2],
            spectrum_fft: RealFft::new(METER_WINDOW_FRAMES),
            spectrum_window: hann_periodic(METER_WINDOW_FRAMES),
        };
        engine.rebuild_loudness_meter();
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
        self.limiter = self
            .params
            .master
            .limiter
            .map(|l| StreamingLimiter::new(l, self.sample_rate, n_channels, self.params.lfe_index));
        self.output = build_output(self.sample_rate, &self.params, &self.decode_taps_override, &self.xtc_taps_override);
        self.pre = Queue::new(n_channels);
        self.post = Queue::new(n_channels);
        self.duck = Queue::new(self.params.stems.len().max(1));
        self.comp_gr = Queue::new(1);
        self.limiter_gr.clear();
        self.rebuild_loudness_meter();
        self.unify_done = 0;
        self.emitted = 0;
    }
}
