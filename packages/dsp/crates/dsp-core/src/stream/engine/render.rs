//! Producing samples: the two look-ahead queues in front of the stages that
//! need one, and the render call that empties them.
//!
//! `pre` holds the causal chain's output and feeds the LF unifier's
//! zero-phase pass; `post` holds the unifier's output and feeds the limiter's
//! forward-window minimum. Nothing is emitted until its full look-ahead
//! exists, which is what lets both stages be the offline algorithm rather
//! than a causal approximation of one.

use super::{PreviewEngine, METER_WINDOW_FRAMES, UNIFY_STRIDE};
use crate::stream::meters::Level;
use crate::stream::params::SendShape;
use crate::stream::routing::shape_index;

impl PreviewEngine {
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
                    let trace = &mut self.comp_gr.channels[0];
                    for i in 0..count {
                        let rms = comp.linked_rms(&bed, &non_lfe, i);
                        let (gain, gr_db) = comp.tick(rms);
                        trace.push(gr_db);
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

        // A bypassed, absent or LFE-only compressor still advances in
        // lockstep, at no reduction — same discipline as the duck trace above.
        self.comp_gr.channels[0].resize(start + count - self.comp_gr.base, 0.0);

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
        let limiter_info = match &mut self.limiter {
            Some(limiter) => limiter.process(&mut self.post.channels, start, end),
            None => Default::default(),
        };

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
        self.master_meters(emit, limiter_info);

        self.emitted += emit;
        self.post.drain_to(self.emitted.saturating_sub(METER_WINDOW_FRAMES));
        self.duck.drain_to(self.emitted.saturating_sub(METER_WINDOW_FRAMES));
        self.comp_gr.drain_to(self.emitted.saturating_sub(METER_WINDOW_FRAMES));
        self.pre.drain_to(self.emitted.saturating_sub(self.look_ahead()));
        emit
    }
}
