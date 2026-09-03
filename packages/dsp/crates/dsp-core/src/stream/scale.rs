//! Per-stem route normalization, measured the way the export measures it.
//!
//! `StemRouter._route_scale` matches a stem's routed loudness to the stem's
//! own: it takes the gated K-weighted power of every signal the routing
//! actually sends, weights each by BS.1770's channel weight and its route
//! gain, and scales the stem by the square root of the ratio. That number is
//! not derivable from the routing weights — a surround send is band-limited
//! and a height send is elevation-EQ'd — so a preview that estimates it from
//! the weights alone plays the stem at a different level from the export.
//!
//! So the preview measures it too, off the same routed signals the render
//! mixes ([`PreviewEngine::route_stem_block`]), on a forked engine sharing the
//! live one's stems, advanced a slice at a time like
//! [`MeasurementPass`](super::measure::MeasurementPass). Until it lands, the
//! served estimate stands in; after it lands, both sides normalize by the same
//! measurement of the same programme.

use crate::loudness::{gated_power, loudness_channel_weight};
use crate::loudness_stream::IntegratedLoudnessMeter;
use crate::stream::routing::STEM_INPUT;

use super::engine::PreviewEngine;

/// The stem's own level, which the routed sum is matched to.
const INPUT: usize = STEM_INPUT;

/// The part of a block that is metered: the preroll in front of an excerpt is
/// rendered to warm the send filters, then dropped.
fn kept(signal: &[f64], skip: usize) -> &[f64] {
    &signal[skip.min(signal.len())..]
}

/// One stem's meters: a gated K-weighted power per routed signal, plus the
/// stem's own input pair, and the raw energies the offline path falls back to
/// when the material is too short or too quiet to gate.
struct Meters {
    input: [IntegratedLoudnessMeter; 2],
    input_energy: [f64; 2],
    speakers: Vec<IntegratedLoudnessMeter>,
    speaker_energy: Vec<f64>,
}

impl Meters {
    fn new(sample_rate: u32, speakers: usize) -> Self {
        Self {
            input: std::array::from_fn(|_| IntegratedLoudnessMeter::new(&[1.0], sample_rate)),
            input_energy: [0.0; 2],
            speakers: (0..speakers)
                .map(|_| IntegratedLoudnessMeter::new(&[1.0], sample_rate))
                .collect(),
            speaker_energy: vec![0.0; speakers],
        }
    }

    fn push_input(&mut self, index: usize, signal: &[f64]) {
        if signal.is_empty() {
            return;
        }
        self.input[index].push(&[signal]);
        self.input_energy[index] += signal.iter().map(|v| v * v).sum::<f64>();
    }

    fn push_speaker(&mut self, index: usize, signal: &[f64]) {
        if signal.is_empty() {
            return;
        }
        self.speakers[index].push(&[signal]);
        self.speaker_energy[index] += signal.iter().map(|v| v * v).sum::<f64>();
    }

    fn finish(mut self) -> ([f64; 2], [f64; 2], Vec<f64>, Vec<f64>) {
        let input = std::array::from_fn(|index| gated_power(self.input[index].finish()));
        let speakers = self
            .speakers
            .iter_mut()
            .map(|m| gated_power(m.finish()))
            .collect();
        (input, self.input_energy, speakers, self.speaker_energy)
    }
}

/// One span of a stem to measure: `[start, end)`, plus the frames before it to
/// render and discard so the send filters are warm.
struct Excerpt {
    start: usize,
    end: usize,
    preroll: usize,
}

/// A route-scale measurement in progress, one stem at a time.
pub struct RouteScalePass {
    engine: PreviewEngine,
    stem: usize,
    cursor: usize,
    total: usize,
    /// Spans of each stem to measure. Empty measures the whole stem.
    schedule: Vec<Excerpt>,
    excerpt: usize,
    skip: usize,
    meters: Meters,
    scales: Vec<f64>,
    done: bool,
}

impl RouteScalePass {
    /// Measure every stem over the whole programme, as the export does.
    pub fn new(live: &PreviewEngine) -> Self {
        Self::build(live, Vec::new())
    }

    /// Measure `count` excerpts of `excerpt_frames` each, spread evenly across
    /// the programme, each preceded by `preroll_frames` of warm-up that is
    /// rendered but not metered — a first answer in a fraction of the time,
    /// which a whole-programme pass then replaces. Falls back to the whole
    /// programme when it is shorter than the plan needs.
    pub fn new_excerpts(
        live: &PreviewEngine,
        count: usize,
        excerpt_frames: usize,
        preroll_frames: usize,
    ) -> Self {
        let total = live.total_frames();
        let count = count.max(1);
        let excerpt_frames = excerpt_frames.max(1);
        let schedule = if count * excerpt_frames >= total {
            Vec::new()
        } else {
            let stride = total / count;
            (0..count)
                .map(|i| {
                    let start = i * stride;
                    Excerpt {
                        start,
                        end: (start + excerpt_frames).min(total),
                        preroll: preroll_frames.min(start),
                    }
                })
                .collect()
        };
        Self::build(live, schedule)
    }

    fn build(live: &PreviewEngine, schedule: Vec<Excerpt>) -> Self {
        let engine = live.fork();
        let sample_rate = engine.sample_rate();
        let total = engine.total_frames();
        let stems = engine.stem_count();
        let speakers = engine.params().speakers.len();
        let mut pass = Self {
            engine,
            stem: 0,
            cursor: 0,
            total,
            schedule,
            excerpt: 0,
            skip: 0,
            meters: Meters::new(sample_rate, speakers),
            scales: Vec::with_capacity(stems),
            done: stems == 0 || total == 0,
        };
        pass.enter_excerpt();
        pass
    }

    /// Position the cursor at the current excerpt's preroll.
    fn enter_excerpt(&mut self) {
        let Some(excerpt) = self.schedule.get(self.excerpt) else {
            return;
        };
        self.cursor = excerpt.start - excerpt.preroll;
        self.skip = excerpt.preroll;
    }

    /// Frames left in the span being measured.
    fn remaining(&self) -> usize {
        match self.schedule.get(self.excerpt) {
            Some(excerpt) => excerpt.end.saturating_sub(self.cursor),
            None => self.total.saturating_sub(self.cursor),
        }
    }

    /// Measure up to `frames` more. Returns every stem's scale once the last
    /// one is done, and keeps returning it afterwards.
    pub fn advance(&mut self, frames: usize) -> Option<&[f64]> {
        if self.done {
            return Some(&self.scales);
        }
        let count = frames.max(1).min(self.remaining()).max(1);
        self.engine.route_stem_block(self.stem, self.cursor, count);
        let skip = self.skip.min(count);
        {
            let route = self.engine.route(self.stem);
            self.meters.push_input(0, kept(route.signal(INPUT), skip));
            self.meters
                .push_input(1, kept(route.signal(INPUT + 1), skip));
        }
        let mut speakers = vec![vec![0.0; count]; self.engine.params().speakers.len()];
        self.engine
            .assemble_stem_for_normalization_into(self.stem, count, &mut speakers);
        for (channel, signal) in speakers.iter().enumerate() {
            self.meters.push_speaker(channel, kept(signal, skip));
        }
        self.cursor += count;
        self.skip -= skip;
        if self.remaining() == 0 && self.excerpt + 1 < self.schedule.len() {
            self.excerpt += 1;
            self.enter_excerpt();
            return None;
        }

        if self.remaining() == 0 {
            let sample_rate = self.engine.sample_rate();
            let speakers = self.engine.params().speakers.len();
            let meters = std::mem::replace(&mut self.meters, Meters::new(sample_rate, speakers));
            self.scales.push(scale_from(&meters.finish(), &self.engine));
            self.stem += 1;
            self.cursor = 0;
            self.excerpt = 0;
            self.skip = 0;
            self.enter_excerpt();
            if self.stem >= self.engine.stem_count() {
                self.done = true;
            }
        }
        self.done.then_some(&self.scales[..])
    }

    /// Fraction of the work done, for a progress indicator.
    pub fn progress(&self) -> f64 {
        let stems = self.engine.stem_count().max(1);
        let spans = self.schedule.len().max(1);
        let within = if self.total == 0 {
            1.0
        } else {
            self.cursor as f64 / self.total as f64
        };
        let stem_fraction = if self.schedule.is_empty() {
            within
        } else {
            self.excerpt as f64 / spans as f64
        };
        ((self.stem as f64 + stem_fraction) / stems as f64).clamp(0.0, 1.0)
    }
}

/// The scalar `StemRouter._route_scale` computes, from one stem's measured
/// signal powers: routed loudness matched to the stem's own, K-weighted, with
/// LFE outside both sums and raw energy as the fallback for material too short
/// or too quiet to gate.
fn scale_from(measured: &([f64; 2], [f64; 2], Vec<f64>, Vec<f64>), engine: &PreviewEngine) -> f64 {
    let (input_powers, input_energies, powers, energies) = measured;
    let params = engine.params();
    let routed_power = params
        .speakers
        .iter()
        .zip(powers)
        .map(|(speaker, power)| loudness_channel_weight(&speaker.name) * power)
        .sum::<f64>();
    let routed_energy = energies.iter().sum::<f64>();
    let input_power = input_powers.iter().sum::<f64>();
    if input_power > 0.0 && routed_power > 0.0 {
        return (input_power / routed_power).sqrt();
    }
    let input_energy = input_energies.iter().sum::<f64>();
    if routed_energy > 1e-20 {
        (input_energy / routed_energy).sqrt()
    } else {
        1.0
    }
}
