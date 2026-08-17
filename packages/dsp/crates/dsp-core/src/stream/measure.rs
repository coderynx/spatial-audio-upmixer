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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stream::engine::StemSource;
    use crate::stream::params::EngineParams;
    use std::sync::Arc;

    fn engine(frames: usize) -> PreviewEngine {
        let params: EngineParams = serde_json::from_str(
            r#"{
                "speakers": [
                    {"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [1.0, 0.0]},
                    {"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.0, 1.0]}
                ],
                "lfe_index": null,
                "shapes": ["left", "right"],
                "sends": {"surround_bass_cutoff_hz": 250.0,
                          "height_low_rolloff_hz": 150.0, "height_low_rolloff_gain": 0.15,
                          "height_crossover_hz": 3000.0, "height_high_shelf_gain": 1.5,
                          "lfe_cutoff_hz": 120.0, "lfe_filter_order": 4, "lfe_gain": 0.316},
                "stems": [{"routing": [["FL", 0.9], ["FR", 0.9]], "rebalance_db": 0.0,
                           "enabled": true, "eq_fir": [], "route_scale": 1.0}],
                "master": {"lf_targets": [[0, 0.5], [1, 0.5]]},
                "output_mode": "stereo",
                "soft_limit_threshold": 0.0
            }"#,
        )
        .expect("engine parameters");

        let tone: Vec<f32> = (0..frames)
            .map(|i| {
                let t = i as f64 / 48_000.0;
                (0.4 * (2.0 * std::f64::consts::PI * 220.0 * t).sin()
                    + 0.1 * (2.0 * std::f64::consts::PI * 3300.0 * t).sin()) as f32
            })
            .collect();
        PreviewEngine::new(
            48_000,
            params,
            vec![Arc::new(StemSource { left: tone.clone(), right: tone })],
        )
    }

    #[test]
    fn slicing_a_measurement_matches_the_blocking_one() {
        let mut reference = engine(120_000);
        let want = reference.measure(&[1.0, 1.0]);

        for slice in [128usize, 1024, 9000] {
            let live = engine(120_000);
            let mut pass = MeasurementPass::new(&live, &[1.0, 1.0]);
            let mut result = None;
            let mut guard = 0;
            while result.is_none() {
                result = pass.advance(slice);
                guard += 1;
                assert!(guard < 100_000, "slice {slice} never finished");
            }
            let (lkfs, dbtp) = result.expect("measured");
            assert!((lkfs - want.0).abs() < 1e-9, "slice {slice}: {lkfs} vs {want:?}");
            assert!((dbtp - want.1).abs() < 1e-9, "slice {slice}: {dbtp} vs {want:?}");
        }
    }

    #[test]
    fn measuring_leaves_the_live_transport_alone() {
        let mut live = engine(48_000);
        let mut out = vec![0.0; 2 * 4096];
        live.render(&mut out, 4096);
        let before = live.position();

        let mut pass = MeasurementPass::new(&live, &[1.0, 1.0]);
        while pass.advance(4096).is_none() {}

        assert_eq!(live.position(), before);
        let mut next = vec![0.0; 2 * 4096];
        assert_eq!(live.render(&mut next, 4096), 4096);
    }

    #[test]
    fn progress_climbs_to_one() {
        let live = engine(48_000);
        let mut pass = MeasurementPass::new(&live, &[1.0, 1.0]);
        assert_eq!(pass.progress(), 0.0);
        pass.advance(4096);
        let partial = pass.progress();
        assert!(partial > 0.0 && partial < 1.0, "{partial}");
        while pass.advance(4096).is_none() {}
        assert_eq!(pass.progress(), 1.0);
    }

    fn run(pass: &mut MeasurementPass, slice: usize) -> (f64, f64) {
        let mut result = None;
        let mut guard = 0;
        while result.is_none() {
            result = pass.advance(slice);
            guard += 1;
            assert!(guard < 100_000, "never finished");
        }
        result.expect("measured")
    }

    #[test]
    fn an_excerpt_plan_spanning_the_whole_programme_matches_the_blocking_measurement() {
        let mut reference = engine(120_000);
        let want = reference.measure(&[1.0, 1.0]);

        let live = engine(120_000);
        let mut pass = MeasurementPass::new_excerpts(&live, &[1.0, 1.0], 1, 120_000, 0);
        let (lkfs, dbtp) = run(&mut pass, 1024);
        assert!((lkfs - want.0).abs() < 1e-9, "{lkfs} vs {want:?}");
        assert!((dbtp - want.1).abs() < 1e-9, "{dbtp} vs {want:?}");
    }

    #[test]
    fn a_sparse_excerpt_plan_lands_close_to_the_whole_programme_measurement() {
        let mut reference = engine(480_000);
        let want = reference.measure(&[1.0, 1.0]);

        let live = engine(480_000);
        let mut pass = MeasurementPass::new_excerpts(&live, &[1.0, 1.0], 5, 20_000, 2_000);
        let (lkfs, dbtp) = run(&mut pass, 1024);
        assert!((lkfs - want.0).abs() < 1.0, "{lkfs} vs {want:?}");
        assert!((dbtp - want.1).abs() < 1.0, "{dbtp} vs {want:?}");
    }

    #[test]
    fn excerpt_progress_climbs_to_one() {
        let live = engine(480_000);
        let mut pass = MeasurementPass::new_excerpts(&live, &[1.0, 1.0], 5, 20_000, 2_000);
        assert_eq!(pass.progress(), 0.0);
        pass.advance(1024);
        let partial = pass.progress();
        assert!(partial > 0.0 && partial < 1.0, "{partial}");
        run(&mut pass, 1024);
        assert_eq!(pass.progress(), 1.0);
    }

    #[test]
    fn a_short_programme_falls_back_to_a_single_excerpt() {
        let mut reference = engine(48_000);
        let want = reference.measure(&[1.0, 1.0]);

        let live = engine(48_000);
        let mut pass = MeasurementPass::new_excerpts(&live, &[1.0, 1.0], 5, 20_000, 2_000);
        let (lkfs, dbtp) = run(&mut pass, 1024);
        assert!((lkfs - want.0).abs() < 1e-9, "{lkfs} vs {want:?}");
        assert!((dbtp - want.1).abs() < 1e-9, "{dbtp} vs {want:?}");
    }
}
