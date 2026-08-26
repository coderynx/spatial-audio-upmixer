//! Live parameter edits and the rebuilds a topology change forces.

use super::{
    build_decorrelator, build_output, build_stem_mix_routes, build_unifier, PreviewEngine,
    GAIN_RAMP_MS,
};
use crate::mastering::dyneq::DynamicEq;
use crate::spatial::panner::PannerLayout;
use crate::stream::limiter::StreamingLimiter;
use crate::stream::params::{EngineParams, SendParams, StemParams};
use crate::stream::routing::StemRouteState;
use crate::stream::state::{OnePole, StreamingCompressor};

/// One stem's routing state, including the ambient half when the stem asks
/// for one. Both the initial build and a stem-count change come through here.
pub(crate) fn build_route(
    sample_rate: u32,
    sends: &SendParams,
    stem: &StemParams,
) -> StemRouteState {
    let mut route = StemRouteState::new(sample_rate, sends, &stem.eq_fir);
    route.set_ambient(
        sample_rate,
        sends,
        stem.wants_ambient(),
        stem.ambient_height_crossover_hz,
    );
    route
}

/// Whether anything the per-stem routing reads has moved: the signals it
/// builds, the speakers it sends them to, or the gains on the way.
fn routing_changed(old: &EngineParams, new: &EngineParams, firs_changed: bool) -> bool {
    if old.stems.len() != new.stems.len()
        || old.shapes != new.shapes
        || old
            .speakers
            .iter()
            .map(|speaker| &speaker.name)
            .ne(new.speakers.iter().map(|speaker| &speaker.name))
    {
        return true;
    }
    let gains: Vec<f64> = old.speakers.iter().map(|s| s.group_gain).collect();
    if gains
        != new
            .speakers
            .iter()
            .map(|s| s.group_gain)
            .collect::<Vec<f64>>()
    {
        return true;
    }
    old.stems.iter().zip(&new.stems).any(|(a, b)| {
        a.routing != b.routing
            || (firs_changed && a.eq_fir != b.eq_fir)
            || a.ambient_rear != b.ambient_rear
            || a.ambient_height != b.ambient_height
            || a.ambient_height_crossover_hz != b.ambient_height_crossover_hz
            || a.object_mode != b.object_mode
            || a.object_placement != b.object_placement
    })
}

fn master_changed_without_firs(
    old: &crate::stream::params::MasterParams,
    new: &crate::stream::params::MasterParams,
) -> bool {
    old.head != new.head
        || old.reference_gain != new.reference_gain
        || old.eq_strength != new.eq_strength
        || old.dynamic_eq != new.dynamic_eq
        || old.compressor != new.compressor
        || old.bass != new.bass
        || old.clip != new.clip
        || old.limiter != new.limiter
        || old.lf_targets != new.lf_targets
        || old.output_gain != new.output_gain
}

fn decorrelator_topology_changed(
    old: Option<crate::mastering::bass::BassParams>,
    new: Option<crate::mastering::bass::BassParams>,
) -> bool {
    match (old, new) {
        (Some(a), Some(b)) => {
            a.unify_hz != b.unify_hz
                || a.decorr_low_hz != b.decorr_low_hz
                || a.decorr_high_hz != b.decorr_high_hz
                || a.decorr_sections != b.decorr_sections
                || a.decorr_max_delay_ms != b.decorr_max_delay_ms
                || a.decorr_fast_ms != b.decorr_fast_ms
                || a.decorr_slow_ms != b.decorr_slow_ms
        }
        (None, None) => false,
        _ => true,
    }
}

impl PreviewEngine {
    /// Replace one stem's FIR without putting every tap through JSON.
    pub fn set_stem_eq_taps(&mut self, index: usize, taps: Vec<f64>) {
        let Some(stem) = self.params.stems.get_mut(index) else {
            return;
        };
        if stem.eq_fir == taps {
            return;
        }
        stem.eq_fir = taps;
        if let Some(route) = self.routes.get_mut(index) {
            route.retune(
                self.sample_rate,
                &self.params.sends,
                &self.params.stems[index].eq_fir,
                false,
                true,
            );
        }
        self.clear_route_scales();
    }

    /// Replace the mastering EQ FIR without putting its taps through JSON.
    pub fn set_master_eq_taps(&mut self, taps: Vec<f64>) {
        if self.params.master.eq_fir == taps {
            return;
        }
        self.params.master.eq_fir = taps;
        for chain in &mut self.causal {
            chain.set_eq_fir(&self.params.master.eq_fir);
        }
    }

    /// Replace the reference-match FIR without putting its taps through JSON.
    pub fn set_reference_taps(&mut self, taps: Vec<f64>) {
        if self.params.master.reference_fir == taps {
            return;
        }
        self.params.master.reference_fir = taps;
        for chain in &mut self.causal {
            chain.set_reference_fir(&self.params.master.reference_fir);
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
    /// params has drained — audible lag on the order of the LF unifier's
    /// horizon plus the limiter's lookahead, not audible silence.
    pub fn update_params(&mut self, params: EngineParams) {
        let firs_changed = !params.transferred_firs;
        let mut old = std::mem::replace(&mut self.params, params);
        if !firs_changed {
            for (new, old) in self.params.stems.iter_mut().zip(&mut old.stems) {
                new.eq_fir = std::mem::take(&mut old.eq_fir);
            }
            self.params.master.reference_fir = std::mem::take(&mut old.master.reference_fir);
            self.params.master.eq_fir = std::mem::take(&mut old.master.eq_fir);
        }

        let topology_changed = old.speakers.len() != self.params.speakers.len()
            || old.lfe_index != self.params.lfe_index;
        if topology_changed {
            let position = self.emitted;
            self.rebuild_for_new_topology();
            self.begin_seek(position);
            return;
        }

        if self.routes.len() != self.params.stems.len() {
            self.rebuild_routes();
        }

        let sends_changed = old.sends != self.params.sends;
        if sends_changed {
            self.lfe_bus.retune(self.sample_rate, &self.params.sends);
        }
        // A measured route scale belongs to the mix it was measured on. Every
        // input the routing reads is compared here rather than the whole
        // block, so a fader or a mastering edit — which change neither the
        // routed signals nor their weights — keeps the measurement.
        let routes_changed = routing_changed(&old, &self.params, firs_changed);
        if sends_changed || routes_changed {
            self.clear_route_scales();
        }
        if routes_changed {
            self.stem_mix_routes = build_stem_mix_routes(&self.params, &self.panner_layout);
        }
        for (i, route) in self.routes.iter_mut().enumerate() {
            let new_eq = self
                .params
                .stems
                .get(i)
                .map(|s| s.eq_fir.as_slice())
                .unwrap_or(&[]);
            let old_eq = old.stems.get(i).map(|s| s.eq_fir.as_slice()).unwrap_or(&[]);
            let eq_changed = firs_changed && new_eq != old_eq;
            if sends_changed || eq_changed {
                route.retune(
                    self.sample_rate,
                    &self.params.sends,
                    new_eq,
                    sends_changed,
                    eq_changed,
                );
            }
            let wants_ambient = self.params.stems.get(i).is_some_and(|s| s.wants_ambient());
            let crossover_changed = old.stems.get(i).is_none_or(|stem| {
                stem.ambient_height_crossover_hz != self.params.stems[i].ambient_height_crossover_hz
            });
            if wants_ambient != route.has_ambient() || sends_changed || crossover_changed {
                route.set_ambient(
                    self.sample_rate,
                    &self.params.sends,
                    wants_ambient,
                    self.params.stems[i].ambient_height_crossover_hz,
                );
            }
        }

        let n_channels = self.params.speakers.len();
        // Rebuilt only when the band list actually moved: a rebuild restarts
        // every detector envelope cold.
        if !self
            .dyn_eq
            .as_ref()
            .is_some_and(|s| s.matches(&self.params.master.dynamic_eq))
        {
            self.dyn_eq = DynamicEq::new(
                self.sample_rate,
                n_channels,
                self.params.lfe_index,
                &self.params.master.dynamic_eq,
            );
        }
        match self.params.master.compressor {
            None => self.compressor = None,
            Some(c) => match &mut self.compressor {
                Some(existing) => existing.retune(c, self.sample_rate),
                None => {
                    self.compressor =
                        Some(StreamingCompressor::new(c, self.sample_rate, n_channels))
                }
            },
        }

        let old_unify_hz = old.master.bass.and_then(|bass| bass.unify_hz);
        let new_unify_hz = self.params.master.bass.and_then(|bass| bass.unify_hz);
        let old_unifier_active = old_unify_hz.is_some() && !old.master.lf_targets.is_empty();
        let new_unifier_active = new_unify_hz.is_some() && !self.params.master.lf_targets.is_empty();
        if !new_unifier_active {
            self.unifier = None;
        } else if old_unifier_active && old_unify_hz == new_unify_hz {
            if let (Some(unifier), Some(bass)) = (&mut self.unifier, self.params.master.bass) {
                unifier.retune(bass, self.params.master.lf_targets.clone());
            } else {
                self.unifier =
                    build_unifier(self.sample_rate, n_channels, &self.params, self.unify_done);
            }
        } else {
            self.unifier =
                build_unifier(self.sample_rate, n_channels, &self.params, self.unify_done);
        }
        if decorrelator_topology_changed(old.master.bass, self.params.master.bass) {
            self.decorrelator =
                build_decorrelator(self.sample_rate, n_channels, &self.params, self.unify_done);
        } else if let (Some(decorrelator), Some(bass)) =
            (&mut self.decorrelator, self.params.master.bass)
        {
            decorrelator.retune_amount(bass.decorrelate);
        } else if self.params.master.bass.is_some_and(|bass| bass.decorrelate > 0.0) {
            self.decorrelator =
                build_decorrelator(self.sample_rate, n_channels, &self.params, self.unify_done);
            if let Some(decorrelator) = &mut self.decorrelator {
                decorrelator.fade_in();
            }
        }

        if (firs_changed && old.master != self.params.master)
            || (!firs_changed && master_changed_without_firs(&old.master, &self.params.master))
        {
            for chain in &mut self.causal {
                chain.retune(self.sample_rate, &old.master, &self.params.master, firs_changed);
            }
        }

        match self.params.master.limiter {
            None => self.limiter = None,
            Some(l) if self.limiter.is_none() || old.master.limiter != Some(l) => {
                self.limiter = Some(StreamingLimiter::new(
                    l,
                    self.sample_rate,
                    self.params.speakers.len(),
                    self.params.lfe_index,
                ));
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

        if old.speakers != self.params.speakers
            || old.output_mode != self.params.output_mode
            || old.meter_weights != self.params.meter_weights
        {
            self.rebuild_loudness_meter();
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
        let speaker_names: Vec<&str> = self
            .params
            .speakers
            .iter()
            .map(|speaker| speaker.name.as_str())
            .collect();
        self.panner_layout = PannerLayout::new(&speaker_names);
        self.rebuild_routes();
        self.lfe_bus.retune(self.sample_rate, &self.params.sends);
        self.dyn_eq = DynamicEq::new(
            self.sample_rate,
            n_channels,
            self.params.lfe_index,
            &self.params.master.dynamic_eq,
        );
        self.compressor = self
            .params
            .master
            .compressor
            .map(|c| StreamingCompressor::new(c, self.sample_rate, n_channels));
        self.unifier = build_unifier(self.sample_rate, n_channels, &self.params, self.unify_done);
        self.decorrelator =
            build_decorrelator(self.sample_rate, n_channels, &self.params, self.unify_done);
        self.output = build_output(
            self.sample_rate,
            &self.params,
            &self.decode_taps_override,
            &self.xtc_taps_override,
        );
        self.prime_output(128);
    }

    /// Rebuild the per-stem routing state and gain smoothers to match
    /// `self.params.stems` — used when a stem was added or removed, where
    /// there is no previous per-index state to retune.
    fn rebuild_routes(&mut self) {
        self.routes = self
            .params
            .stems
            .iter()
            .map(|s| build_route(self.sample_rate, &self.params.sends, s))
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
        self.stem_mix_routes = build_stem_mix_routes(&self.params, &self.panner_layout);
    }
}
