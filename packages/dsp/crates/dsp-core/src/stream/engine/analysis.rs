//! Loudness/true-peak measurement and the meter + spectrum readouts.

use super::{PreviewEngine, METER_WINDOW_FRAMES};
use crate::loudness_stream::WindowLoudnessMeter;
use crate::mastering::limiter::LimiterInfo;
use crate::spatial::downmix::{FoldTo51, FOLD_51_WEIGHTS};
use crate::stream::meters::{Level, Meters};
use crate::stream::params::OutputMode;

impl PreviewEngine {
    /// Rebuild the momentary/short-term meters for the channels the collapse
    /// currently delivers. Called wherever that set or its weights can move —
    /// construction, `rewind`, and an output-mode or speaker edit — because
    /// the meters carry K-weighting state per channel, so there is nothing to
    /// retune in place.
    pub(super) fn rebuild_loudness_meter(&mut self) {
        self.meter_fold = self.measurement_fold();
        self.meter_folded.clear();
        let weights: Vec<f64> = match &self.meter_fold {
            Some(_) => FOLD_51_WEIGHTS.to_vec(),
            None => (0..self.output_channels())
                .map(|i| self.params.meter_weights.get(i).copied().unwrap_or(1.0))
                .collect(),
        };
        self.loudness =
            (!weights.is_empty()).then(|| WindowLoudnessMeter::new(&weights, self.sample_rate));
    }

    /// Fold the loudness readouts and the dynamics stages' gain reduction of
    /// the render just emitted into [`Meters`].
    ///
    /// Everything here reads the emit position: the loudness windows are fed
    /// the collapsed output that was just written, the limiter reports on the
    /// region it just gained, and the compressor's per-frame trace — which is
    /// produced a whole look-ahead earlier — is read back out of its queue at
    /// the frames now being heard.
    pub(super) fn master_meters(&mut self, emit: usize, limiter: LimiterInfo) {
        let channels = self.output_channels();
        let frames = self
            .collapsed
            .iter()
            .take(channels)
            .map(|c| c.len().min(emit))
            .min()
            .unwrap_or(0);
        if let Some(meter) = &mut self.loudness {
            if frames > 0 {
                let slices: Vec<&[f64]> = self
                    .collapsed
                    .iter()
                    .take(channels)
                    .map(|c| &c[..frames])
                    .collect();
                match &self.meter_fold {
                    Some(fold) => {
                        fold.apply(&slices, frames, &mut self.meter_folded);
                        let refs: Vec<&[f64]> =
                            self.meter_folded.iter().map(|c| c.as_slice()).collect();
                        meter.push(&refs);
                    }
                    None => meter.push(&slices),
                }
            }
            self.meters.master.momentary_lkfs = meter.momentary();
            self.meters.master.short_term_lkfs = meter.short_term();
        }

        self.limiter_gr
            .push((emit, limiter.max_gr_db, limiter.lfe_max_gr_db));
        let mut held: usize = self.limiter_gr.iter().map(|entry| entry.0).sum();
        let mut stale = 0;
        for entry in &self.limiter_gr {
            if held - entry.0 < METER_WINDOW_FRAMES {
                break;
            }
            held -= entry.0;
            stale += 1;
        }
        self.limiter_gr.drain(..stale);
        self.meters.master.limiter_gr_db = self
            .limiter_gr
            .iter()
            .fold(0.0_f64, |m, entry| m.max(entry.1));
        self.meters.master.limiter_lfe_gr_db = self
            .limiter_gr
            .iter()
            .fold(0.0_f64, |m, entry| m.max(entry.2));

        let to = self.emitted + emit;
        let from = to.saturating_sub(METER_WINDOW_FRAMES);
        let trace = &self.comp_gr.channels[0];
        let lo = from.saturating_sub(self.comp_gr.base).min(trace.len());
        let hi = to.saturating_sub(self.comp_gr.base).min(trace.len());
        self.meters.master.comp_gr_db = trace[lo..hi].iter().copied().fold(0.0_f64, f64::max);
    }

    fn refresh_levels(&mut self) {
        self.meters.stems = self
            .stems
            .iter()
            .enumerate()
            .map(|(i, stem)| {
                let sp = self.params.stems.get(i);
                if !sp.map(|p| p.enabled).unwrap_or(true) {
                    return [Level::default(), Level::default()];
                }
                let gain = sp
                    .map(|p| 10.0_f64.powf(p.rebalance_db / 20.0))
                    .unwrap_or(1.0);
                let to = self.emitted.min(stem.len());
                let win_start = to.saturating_sub(METER_WINDOW_FRAMES);
                [
                    Level::measure_f32(&stem.left[win_start..to], gain),
                    Level::measure_f32(&stem.right[win_start..to], gain),
                ]
            })
            .collect();
        self.meters.stem_dynamics_gr_db = self
            .routes
            .iter()
            .map(crate::stream::routing::StemRouteState::dynamics_gain_reduction_db)
            .collect();
        self.meters.stem_dynamic_eq_gr_db = self
            .routes
            .iter()
            .map(crate::stream::routing::StemRouteState::dynamic_eq_gain_reduction_db)
            .collect();
        let meter_start = self
            .emitted
            .saturating_sub(METER_WINDOW_FRAMES)
            .saturating_sub(self.post.base);
        let meter_end = self.emitted.saturating_sub(self.post.base);
        self.meters.channels = self
            .rendered_channels
            .iter()
            .enumerate()
            .map(|(channel, source)| {
                if self.params.speakers.get(channel).is_some_and(|s| s.muted) {
                    Level::default()
                } else {
                    let c = &self.post.channels[*source];
                    Level::measure(&c[meter_start..meter_end])
                }
            })
            .collect();
        self.meters.output = [
            Level::measure(&self.output_meter_tail[0]),
            Level::measure(&self.output_meter_tail[1]),
        ];
    }

    /// The 5.1 re-render integrated loudness is measured on, for a native
    /// output wider than 5.1 — `None` when the delivered output is already
    /// the programme (see `docs/standards/loudness_dsp_bs1770.md`
    /// §"Measurement programme").
    pub fn measurement_fold(&self) -> Option<FoldTo51> {
        if self.params.output_mode != OutputMode::Native {
            return None;
        }
        let names: Vec<&str> = self
            .params
            .speakers
            .iter()
            .map(|s| s.name.as_str())
            .collect();
        FoldTo51::new(&names)
    }

    pub fn measurement_weights(&self, fallback: &[f64]) -> Vec<f64> {
        if self.authored_channels > self.params.speakers.len() {
            return self
                .params
                .speakers
                .iter()
                .map(|speaker| crate::loudness::loudness_channel_weight(&speaker.name))
                .collect();
        }
        (0..self.output_channels())
            .map(|i| fallback.get(i).copied().unwrap_or(1.0))
            .collect()
    }

    /// Measure the whole collapsed programme: integrated loudness in LKFS and
    /// true peak in dBTP.
    ///
    /// This replaces the browser's old excerpt-sampled estimate (ledger D4)
    /// with the real BS.1770 measurement over the actual render, so the
    /// correction gain the preview applies is the one a bounce would need.
    /// It renders the programme once into memory — two channels for every
    /// collapse mode — and rewinds afterwards, so the transport is untouched.
    ///
    /// Loudness comes off the measurement programme (the 5.1 re-render for a
    /// native bed wider than 5.1, whose weights the fold fixes and `weights`
    /// no longer describes); true peak stays on the delivered channels, which
    /// are what the ceiling applies to.
    pub fn measure(&mut self, weights: &[f64]) -> (f64, f64) {
        if self.authored_channels > self.params.speakers.len()
            && self.params.output_mode != OutputMode::Native
        {
            return self.fork().measure(weights);
        }
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
        let mut folded = Vec::new();
        let programme: Vec<(f64, &[f64])> = match self.measurement_fold() {
            Some(fold) => {
                let frames = refs.first().map(|c| c.len()).unwrap_or(0);
                fold.apply(&refs, frames, &mut folded);
                FOLD_51_WEIGHTS
                    .iter()
                    .copied()
                    .zip(folded.iter().map(|c| c.as_slice()))
                    .collect()
            }
            None => self
                .measurement_weights(weights)
                .into_iter()
                .zip(refs.iter().copied())
                .collect(),
        };
        (
            crate::loudness::measure_integrated_loudness(&programme, self.sample_rate),
            crate::loudness::measure_true_peak(&refs),
        )
    }

    /// Levels from the most recent render.
    pub fn meters(&mut self) -> &Meters {
        self.refresh_levels();
        &self.meters
    }

    /// Per-stem `(level, spectral centroid)` for the haze/elevation displays
    /// — both roughly 0..1, `centroid` sqrt-scaled toward the low end like a
    /// listener's own frequency perception. Computed here rather than kept
    /// current in `meters`/`render()`: the FFT this needs is too heavy to run
    /// every 128-frame quantum, but is cheap enough to run at the worklet's
    /// ~30Hz report cadence, called on demand from the same trailing
    /// `METER_WINDOW_FRAMES` window the meters use.
    pub fn stem_spectrum(&mut self) -> Vec<(f64, f64)> {
        self.refresh_levels();
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
                    let sample =
                        (stem.left[win_start + j] as f64 + stem.right[win_start + j] as f64) * 0.5;
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
}
