//! Producing samples: the two look-ahead queues in front of the stages that
//! need one, and the render call that empties them.
//!
//! `pre` holds the causal chain's output and feeds the LF unifier's
//! zero-phase pass; `post` holds the unifier's output and feeds the limiter's
//! forward-window minimum. Nothing is emitted until its full look-ahead
//! exists, which is what lets both stages be the offline algorithm rather
//! than a causal approximation of one.

use super::mix::{assemble_stem_into, StemAssemblyPolicy};
use super::{PreviewEngine, METER_WINDOW_FRAMES};
use crate::mastering::clip::ClipCurve;
use crate::spatial::downmix::{apply_stereo_downmix_lock, DownmixRole};
use crate::stream::params::{OutputMode, SendShape};
use crate::stream::routing::{shape_index, SIGNALS, STEM_INPUT};

impl PreviewEngine {
    /// Run one stem's routing chain over `count` frames from `start`, leaving
    /// the routed signals in its [`StemRouteState`] for the caller to read.
    ///
    /// The render mixes them into the bed; [`RouteScalePass`] meters them.
    /// Both go through here, so the normalization is measured off the signals
    /// that are actually played rather than off a second assembly of the same
    /// chain.
    ///
    /// [`RouteScalePass`]: crate::stream::scale::RouteScalePass
    pub(crate) fn route_stem_block(&mut self, stem_index: usize, start: usize, count: usize) {
        let Some(sp) = self.params.stems.get(stem_index) else {
            return;
        };
        let stem = &self.stems[stem_index];
        let mix = &self.stem_mix_routes[stem_index];
        // A send the layout has no speaker for gets no ambient: the amount
        // is taken out of the dry pair, so sending it nowhere would be a hole
        // rather than a move.
        let rear = sp.ambient_rear * f64::from(mix.has_surround);
        let height = sp.ambient_height * f64::from(mix.has_height);

        let route = &mut self.routes[stem_index];
        route.process_block(
            &stem.left,
            &stem.right,
            start,
            count,
            rear,
            height,
            mix.needs_surround,
            mix.needs_height,
        );
    }

    /// Route and run the causal chain until `pre` reaches `target` frames.
    fn fill_pre(&mut self, target: usize) {
        let target = target.min(self.total_frames);
        if self.pre.end() >= target {
            return;
        }
        let start = self.pre.end();
        let count = target - start;
        let n_channels = self.authored_channels;

        let mut bed = vec![vec![0.0; count]; n_channels];
        let mut lfe_sum = vec![0.0; count];

        for stem_index in 0..self.stems.len() {
            let Some(sp) = self.params.stems.get(stem_index) else {
                continue;
            };
            let target_gain = if sp.enabled {
                10.0_f64.powf(sp.rebalance_db / 20.0) * self.route_scale(stem_index)
            } else {
                0.0
            };
            if !sp.enabled && self.stem_gain[stem_index].is_settled(0.0) {
                // Already faded out and staying muted — skip the routing and
                // EQ work entirely, same as the old hard cut did.
                continue;
            }
            self.route_stem_block(stem_index, start, count);

            let smoother = &mut self.stem_gain[stem_index];
            let route = &self.routes[stem_index];
            let shaped: [&[f64]; SIGNALS] = std::array::from_fn(|i| route.signal(i));
            let mix = &self.stem_mix_routes[stem_index];
            let ambient = route.has_ambient().then_some(&mix.ambient);

            if !self.params.spatial_downmix_lock
                || self.authored_channels > self.params.speakers.len()
            {
                assemble_stem_into(
                    mix,
                    route,
                    &self.params.speakers,
                    count,
                    StemAssemblyPolicy::Render,
                    &mut bed,
                    Some(&mut lfe_sum),
                    || smoother.tick(target_gain),
                );
            } else {
                let mut routed = vec![vec![0.0; count]; bed.len()];
                let mut input_left = vec![0.0; count];
                let mut input_right = vec![0.0; count];
                for i in 0..count {
                    let gain = smoother.tick(target_gain);
                    input_left[i] = shaped[STEM_INPUT][i] * gain;
                    input_right[i] = shaped[STEM_INPUT + 1][i] * gain;
                    if let Some(objects) = &mix.objects {
                        for object in objects {
                            bed[object.authored_channel][i] += shaped[object.signal][i] * gain;
                        }
                    }
                    if mix.lfe_weight != 0.0 {
                        lfe_sum[i] +=
                            shaped[shape_index(SendShape::Mono)][i] * mix.lfe_weight * gain;
                    }
                    for (channel, signal, weight) in &mix.regular {
                        routed[*channel][i] += shaped[*signal][i]
                            * weight
                            * self.params.speakers[*channel].group_gain
                            * gain;
                    }
                    if let Some(feeds) = ambient {
                        for (channel, slot, weight) in feeds {
                            routed[*channel][i] += shaped[*slot][i] * weight * gain;
                        }
                    }
                }
                apply_stereo_downmix_lock(
                    self.params
                        .speakers
                        .iter()
                        .map(|speaker| DownmixRole::from_name(&speaker.name)),
                    &mut routed,
                    &input_left,
                    &input_right,
                    self.params.surround_downmix_coeff,
                    self.params.height_downmix_coeff,
                );
                for (target, source) in bed.iter_mut().zip(routed) {
                    for (target, source) in target.iter_mut().zip(source) {
                        *target += source;
                    }
                }
            }
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
            // Between the static EQ and the compressor: surgical correction
            // before glue, and still a shared curve across the bed.
            let object_sources = self.authored_channels > self.params.speakers.len();
            let mut detector = object_sources.then(|| {
                let mut rendered = std::mem::take(&mut self.speaker_render_scratch);
                self.render_authored_into(&bed, &mut rendered);
                rendered
            });
            if let Some(dyn_eq) = &mut self.dyn_eq {
                if let Some(rendered) = &detector {
                    dyn_eq.process_linked(&mut bed, rendered);
                } else {
                    dyn_eq.process(&mut bed);
                }
            }
            let targets = self.non_lfe();
            if let Some(rendered) = &mut detector {
                self.render_authored_into(&bed, rendered);
            }
            let detector_channels = if object_sources {
                (0..self.params.speakers.len())
                    .filter(|i| self.params.lfe_index != Some(*i))
                    .collect()
            } else {
                targets.clone()
            };
            if let Some(comp) = &mut self.compressor {
                if !targets.is_empty() {
                    let trace = &mut self.comp_gr.channels[0];
                    for i in 0..count {
                        let rms = if let Some(rendered) = &detector {
                            comp.linked_rms(rendered, &detector_channels, i)
                        } else {
                            comp.linked_rms(&bed, &detector_channels, i)
                        };
                        let (gain, gr_db) = comp.tick(rms);
                        trace.push(gr_db);
                        for &ch in &targets {
                            bed[ch][i] *= gain;
                        }
                    }
                }
            }
            if let Some(rendered) = detector {
                self.speaker_render_scratch = rendered;
            }
            for (channel, block) in bed.iter_mut().enumerate() {
                self.causal[channel].band_gains(block);
            }
        }

        // A bypassed, absent or LFE-only compressor still advances in
        // lockstep, at no reduction.
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

    fn render_authored_into(&self, authored: &[Vec<f64>], rendered: &mut Vec<Vec<f64>>) {
        let n_speakers = self.params.speakers.len();
        rendered.resize_with(n_speakers, Vec::new);
        for (target, source) in rendered.iter_mut().zip(authored) {
            target.clear();
            target.extend_from_slice(source);
        }
        for route in &self.stem_mix_routes {
            for object in route.objects.iter().flatten() {
                let audio = &authored[object.authored_channel];
                for &(speaker, gain) in &object.speakers {
                    for (target, source) in rendered[speaker].iter_mut().zip(audio) {
                        *target += gain * object.gain * source;
                    }
                }
            }
        }
    }

    /// Run the LF unifier until `post` reaches `target` frames.
    fn fill_post(&mut self, target: usize) {
        if self.unify_done >= target.min(self.total_frames) {
            return;
        }
        // The LF unifier's zero-phase pass filters `horizon` samples either
        // side of what it emits, so emitting a render quantum at a time would
        // redo that context ~75 times over. Advance in strides instead.
        let target = target
            .max(self.unify_done + self.unify_stride)
            .min(self.total_frames);
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
        let lfe_gain_db = self
            .params
            .master
            .bass
            .map(|b| b.lfe_gain_db)
            .unwrap_or(0.0);
        // Clipped on the way into `post`, not at the emit point: the limiter
        // detects a whole look-ahead past what it emits, and that window has
        // to be clipped too (parity contract §1).
        let apply_source_gain = !self.params.bypass_mastering
            || self.params.output_mode == OutputMode::Native
            || self.authored_channels > self.params.speakers.len();
        let source_gain = if apply_source_gain
            && (self.params.master.output_gain != 1.0 || !self.master_gain.is_settled(1.0))
        {
            Some(
                (0..end - start)
                    .map(|_| self.master_gain.tick(self.params.master.output_gain))
                    .collect::<Vec<_>>(),
            )
        } else {
            None
        };
        let clip = self.params.master.clip.map(|c| ClipCurve::new(&c));
        for (channel, block) in window.iter_mut().enumerate() {
            if !self.params.bypass_mastering {
                self.causal[channel].lfe_trim(block, lfe_gain_db);
                if let Some(gains) = &source_gain {
                    for (sample, gain) in block.iter_mut().zip(gains) {
                        *sample *= gain;
                    }
                }
                if let Some(curve) = clip
                    .as_ref()
                    .filter(|_| self.params.lfe_index != Some(channel))
                {
                    curve.apply(block);
                }
            } else if let Some(gains) = &source_gain {
                for (sample, gain) in block.iter_mut().zip(gains) {
                    *sample *= gain;
                }
            }
            self.post.channels[channel].extend_from_slice(block);
        }
        if self.authored_channels > self.params.speakers.len() {
            let mut rendered = std::mem::take(&mut self.speaker_render_scratch);
            self.render_authored_into(&window, &mut rendered);
            for (speaker, block) in rendered.iter().enumerate() {
                if self.params.lfe_index != Some(speaker) {
                    self.post.channels[self.rendered_channels[speaker]].extend_from_slice(block);
                }
            }
            self.speaker_render_scratch = rendered;
        }
        self.unify_done = end;
    }

    pub(crate) fn prepare_render(&mut self, frames: usize, step: usize) -> bool {
        let limiter = self
            .limiter
            .as_ref()
            .map(|limiter| limiter.required_lookahead())
            .unwrap_or(0);
        let target = (self.emitted + frames + limiter + self.look_ahead()).min(self.total_frames);
        if self.pre.end() >= target {
            let end = (self.emitted + frames + limiter).min(self.total_frames);
            let base = self.pre.base;
            let unifier = self.unifier.as_mut().map_or(true, |unifier| {
                unifier.prewarm(&self.pre.channels, base, self.total_frames, end, step)
            });
            let decorrelator = self.decorrelator.as_mut().map_or(true, |decorrelator| {
                decorrelator.prewarm(&self.pre.channels, base, self.total_frames, end, step)
            });
            return unifier && decorrelator;
        }
        self.fill_pre((self.pre.end() + step.max(1)).min(target));
        false
    }

    pub(crate) fn prime_output(&mut self, frames: usize) {
        let bed = vec![vec![0.0; frames]; self.params.speakers.len()];
        self.output.process(&bed, frames, 1.0, &mut self.collapsed);
        self.output.reset();
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

        let lookahead = self
            .limiter
            .as_ref()
            .map(|l| l.required_lookahead())
            .unwrap_or(0);
        self.fill_post(self.emitted + emit + lookahead);

        let start = self.emitted - self.post.base;
        let end = start + emit;
        let post_base = self.post.base;
        let final_input = self.post.end() == self.total_frames
            && post_base + end + lookahead >= self.total_frames;
        let limiter_info = match &mut self.limiter {
            Some(limiter) => {
                limiter.process(&mut self.post.channels, post_base, start, end, final_input)
            }
            None => Default::default(),
        };

        // Monitor mute lands here, on the finished bed: every shared stage
        // above (bass bus, linked compressor, limiter) has already run, so
        // silencing one speaker cannot change what the others get.
        let window: Vec<Vec<f64>> = self
            .rendered_channels
            .iter()
            .enumerate()
            .map(|(channel, source)| {
                let c = &self.post.channels[*source];
                if self.params.speakers.get(channel).is_some_and(|s| s.muted) {
                    vec![0.0; end - start]
                } else {
                    c[start..end].to_vec()
                }
            })
            .collect();
        let gain = if self.params.output_mode == OutputMode::Native {
            1.0
        } else if self.authored_channels > self.params.speakers.len() {
            self.monitor_gain
                .advance(self.params.master.monitor_output_gain, emit)
        } else if self.params.bypass_mastering {
            self.master_gain
                .advance(self.params.master.output_gain, emit)
        } else {
            1.0
        };
        self.output
            .process(&window, emit, gain, &mut self.collapsed);
        for (channel, rendered) in self.collapsed.iter().enumerate().take(out_channels) {
            let base = channel * n_frames;
            let count = emit.min(rendered.len());
            if base + count > out.len() {
                break;
            }
            out[base..base + count].copy_from_slice(&rendered[..count]);
        }

        for (channel, tail) in self.output_meter_tail.iter_mut().enumerate() {
            if let Some(rendered) = self.collapsed.get(channel) {
                tail.extend_from_slice(&rendered[..emit.min(rendered.len())]);
            }
            let drop = tail.len().saturating_sub(METER_WINDOW_FRAMES);
            tail.drain(..drop);
        }
        self.master_meters(emit, limiter_info);

        self.emitted += emit;
        self.post
            .drain_to(self.emitted.saturating_sub(METER_WINDOW_FRAMES));
        self.comp_gr
            .drain_to(self.emitted.saturating_sub(METER_WINDOW_FRAMES));
        self.pre
            .drain_to(self.emitted.saturating_sub(self.look_ahead()));
        emit
    }
}
