//! Whole-programme loudness measurement, advanced a slice at a time.
//!
//! The correction gain the preview applies is the one a bounce would need, so
//! it has to come from the same BS.1770 measurement over the same programme
//! the export measures. Doing that in one call costs ~0.12x realtime — a
//! minute of solid compute for an eight-minute track — which on the audio
//! thread is a minute of silence.
//!
//! So the pass owns a forked engine (sharing the live one's stems) and a pair
//! of streaming meters, and the host advances it in slices small enough to fit
//! whatever is left of a render quantum. The result is identical to the
//! blocking [`PreviewEngine::measure`]; only its arrival is spread out.
//!
//! A pass can also cover a bounded set of excerpts instead of the whole
//! programme (see [`MeasurementPass::new_excerpts`]) — the preview uses this
//! for a fast first correction, then measures the whole programme in the
//! background and refines it. Gating is unaffected by sampling only part of
//! the programme; see `docs/contracts/preview_export_parity.md` P3.

use crate::loudness_stream::{true_peak_dbtp, IntegratedLoudnessMeter, TruePeakMeter};

use super::engine::PreviewEngine;

/// One excerpt to measure: `[start, end)` of the programme, plus the frames
/// immediately before `start` to render and discard so filter state (Haas
/// sends, compressor) is warm by the time measurement begins.
struct Excerpt {
    start: usize,
    end: usize,
    preroll: usize,
}

/// A measurement in progress, over the whole programme or a set of excerpts.
pub struct MeasurementPass {
    engine: PreviewEngine,
    loudness: IntegratedLoudnessMeter,
    peaks: Vec<TruePeakMeter>,
    channels: usize,
    scratch: Vec<f64>,
    measured: usize,
    total: usize,
    result: Option<(f64, f64)>,
    schedule: Vec<Excerpt>,
    excerpt_index: usize,
    skip: usize,
}

impl MeasurementPass {
    /// `weights` are the BS.1770 channel weights for the collapsed output, in
    /// channel order. Measures the whole programme.
    pub fn new(live: &PreviewEngine, weights: &[f64]) -> Self {
        let total = live.total_frames();
        Self::build(live, weights, Vec::new(), total)
    }

    /// Measure `count` excerpts of `excerpt_frames` each, spread evenly across
    /// the programme, each preceded by `preroll_frames` of warm-up that is
    /// rendered but not measured. Falls back to a single excerpt covering the
    /// whole programme when it is shorter than the plan would need.
    pub fn new_excerpts(
        live: &PreviewEngine,
        weights: &[f64],
        count: usize,
        excerpt_frames: usize,
        preroll_frames: usize,
    ) -> Self {
        let total = live.total_frames();
        let count = count.max(1);
        let excerpt_frames = excerpt_frames.max(1);
        let span = count * excerpt_frames;

        let schedule = if span >= total {
            vec![Excerpt { start: 0, end: total, preroll: 0 }]
        } else {
            let stride = total / count;
            (0..count)
                .map(|i| {
                    let start = i * stride;
                    let end = (start + excerpt_frames).min(total);
                    let preroll = preroll_frames.min(start);
                    Excerpt { start, end, preroll }
                })
                .collect()
        };

        let measured_total: usize = schedule.iter().map(|e| e.end - e.start).sum();
        Self::build(live, weights, schedule, measured_total)
    }

    fn build(live: &PreviewEngine, weights: &[f64], schedule: Vec<Excerpt>, total: usize) -> Self {
        let engine = live.fork();
        let channels = engine.output_channels();
        let sample_rate = engine.sample_rate();
        let padded: Vec<f64> =
            (0..channels).map(|i| weights.get(i).copied().unwrap_or(1.0)).collect();
        let mut pass = Self {
            loudness: IntegratedLoudnessMeter::new(&padded, sample_rate),
            peaks: (0..channels).map(|_| TruePeakMeter::new()).collect(),
            engine,
            channels,
            scratch: Vec::new(),
            measured: 0,
            total,
            result: None,
            schedule,
            excerpt_index: 0,
            skip: 0,
        };
        pass.enter_current_excerpt();
        pass
    }

    /// Position the (forked) engine at the start of the current scheduled
    /// excerpt, cold, with `skip` armed to the excerpt's preroll so the next
    /// `advance` calls render and discard warm-up before anything is measured.
    fn enter_current_excerpt(&mut self) {
        let Some(excerpt) = self.schedule.get(self.excerpt_index) else { return };
        self.engine.jump_to(excerpt.start.saturating_sub(excerpt.preroll));
        self.skip = excerpt.preroll;
    }

    /// Measure up to `frames` more. Returns the result once every excerpt (or
    /// the whole programme) is exhausted, and keeps returning it afterwards.
    pub fn advance(&mut self, frames: usize) -> Option<(f64, f64)> {
        if let Some(result) = self.result {
            return Some(result);
        }
        let mut frames = frames.max(1);
        if let Some(excerpt) = self.schedule.get(self.excerpt_index) {
            let remaining = excerpt.end.saturating_sub(self.engine.position());
            frames = frames.min(remaining.max(1));
        }
        self.scratch.resize(self.channels.max(1) * frames, 0.0);
        let written = self.engine.render(&mut self.scratch, frames);

        if written > 0 {
            let skip = self.skip.min(written);
            self.skip -= skip;
            if skip < written {
                let slices: Vec<&[f64]> = (0..self.channels)
                    .map(|c| &self.scratch[c * frames + skip..c * frames + written])
                    .collect();
                self.loudness.push(&slices);
                for (meter, slice) in self.peaks.iter_mut().zip(slices.iter()) {
                    meter.push(slice);
                }
                self.measured += written - skip;
            }
        }

        let excerpt_exhausted = !self.schedule.is_empty()
            && self
                .schedule
                .get(self.excerpt_index)
                .is_some_and(|e| self.engine.position() >= e.end);
        let programme_exhausted = self.schedule.is_empty() && written < frames;

        if excerpt_exhausted {
            self.excerpt_index += 1;
            self.enter_current_excerpt();
        }

        let done = programme_exhausted
            || (!self.schedule.is_empty() && self.excerpt_index >= self.schedule.len());
        if done {
            let peaks: Vec<f64> = self.peaks.iter_mut().map(|m| m.finish()).collect();
            self.result = Some((self.loudness.finish(), true_peak_dbtp(&peaks)));
        }
        self.result
    }

    /// Fraction of the scheduled programme measured, for a progress indicator.
    pub fn progress(&self) -> f64 {
        if self.result.is_some() || self.total == 0 {
            return 1.0;
        }
        (self.measured as f64 / self.total as f64).clamp(0.0, 1.0)
    }
}
