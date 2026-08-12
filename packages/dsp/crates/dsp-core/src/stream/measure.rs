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

use crate::loudness_stream::{true_peak_dbtp, IntegratedLoudnessMeter, TruePeakMeter};

use super::engine::PreviewEngine;

/// A measurement in progress.
pub struct MeasurementPass {
    engine: PreviewEngine,
    loudness: IntegratedLoudnessMeter,
    peaks: Vec<TruePeakMeter>,
    channels: usize,
    scratch: Vec<f64>,
    measured: usize,
    total: usize,
    result: Option<(f64, f64)>,
}

impl MeasurementPass {
    /// `weights` are the BS.1770 channel weights for the collapsed output, in
    /// channel order.
    pub fn new(live: &PreviewEngine, weights: &[f64]) -> Self {
        let engine = live.fork();
        let channels = engine.output_channels();
        let sample_rate = engine.sample_rate();
        let total = engine.total_frames();
        let padded: Vec<f64> =
            (0..channels).map(|i| weights.get(i).copied().unwrap_or(1.0)).collect();
        Self {
            loudness: IntegratedLoudnessMeter::new(&padded, sample_rate),
            peaks: (0..channels).map(|_| TruePeakMeter::new()).collect(),
            engine,
            channels,
            scratch: Vec::new(),
            measured: 0,
            total,
            result: None,
        }
    }

    /// Measure up to `frames` more. Returns the result once the programme is
    /// exhausted, and keeps returning it afterwards.
    pub fn advance(&mut self, frames: usize) -> Option<(f64, f64)> {
        if let Some(result) = self.result {
            return Some(result);
        }
        let frames = frames.max(1);
        self.scratch.resize(self.channels.max(1) * frames, 0.0);
        let written = self.engine.render(&mut self.scratch, frames);
        if written > 0 {
            let slices: Vec<&[f64]> = (0..self.channels)
                .map(|c| &self.scratch[c * frames..c * frames + written])
                .collect();
            self.loudness.push(&slices);
            for (meter, slice) in self.peaks.iter_mut().zip(slices.iter()) {
                meter.push(slice);
            }
            self.measured += written;
        }
        if written < frames {
            let peaks: Vec<f64> = self.peaks.iter_mut().map(|m| m.finish()).collect();
            self.result = Some((self.loudness.finish(), true_peak_dbtp(&peaks)));
        }
        self.result
    }

    /// Fraction of the programme measured, for a progress indicator.
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
                "sends": {"surround_bass_cutoff_hz": 250.0, "surround_haas_ms": [31.0, 37.0],
                          "height_haas_ms": [23.0, 29.0], "diffuse_blend": 0.55,
                          "height_low_rolloff_hz": 150.0, "height_low_rolloff_gain": 0.15,
                          "height_crossover_hz": 3000.0, "height_high_shelf_gain": 1.5,
                          "lfe_cutoff_hz": 120.0, "lfe_filter_order": 4, "lfe_gain": 0.316},
                "stems": [{"routing": [["FL", 0.9], ["FR", 0.9]], "rebalance_db": 0.0,
                           "enabled": true, "eq_fir": [], "route_scale": 1.0}],
                "master": {"stereo_pairs": [[0, 1]]},
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
}
