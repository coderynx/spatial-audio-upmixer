//! Live parameter edits and the rebuilds a topology change forces.

use super::{build_decorrelator, build_unifier, PreviewEngine, GAIN_RAMP_MS};
use crate::mastering::dyneq::DynamicEq;
use crate::stream::master::StreamingLimiter;
use crate::stream::params::EngineParams;
use crate::stream::routing::StemRouteState;
use crate::stream::state::{OnePole, StreamingCompressor};

/// Whether anything the per-stem routing reads has moved: the signals it
/// builds, the speakers it sends them to, or the gains on the way.
fn routing_changed(old: &EngineParams, new: &EngineParams) -> bool {
    if old.stems.len() != new.stems.len() || old.shapes != new.shapes {
        return true;
    }
    let gains: Vec<f64> = old.speakers.iter().map(|s| s.group_gain).collect();
    if gains != new.speakers.iter().map(|s| s.group_gain).collect::<Vec<f64>>() {
        return true;
    }
    old.stems
        .iter()
        .zip(&new.stems)
        .any(|(a, b)| a.routing != b.routing || a.eq_fir != b.eq_fir)
}

impl PreviewEngine {
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
        let old = std::mem::replace(&mut self.params, params);

        let topology_changed =
            old.speakers.len() != self.params.speakers.len() || old.lfe_index != self.params.lfe_index;
        if topology_changed {
            // Rare — the web client tears the whole worklet down for a
            // speaker-layout change before this can even fire in practice —
            // so it keeps the old full-rebuild-then-seek behavior rather
            // than earning its own diff logic.
            let position = self.emitted;
            self.rebuild_for_new_topology();
            self.seek(position);
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
        if sends_changed || routing_changed(&old, &self.params) {
            self.clear_route_scales();
        }
        for (i, route) in self.routes.iter_mut().enumerate() {
            let new_eq = self.params.stems.get(i).map(|s| s.eq_fir.as_slice()).unwrap_or(&[]);
            let old_eq = old.stems.get(i).map(|s| s.eq_fir.as_slice()).unwrap_or(&[]);
            let eq_changed = new_eq != old_eq;
            if sends_changed || eq_changed {
                route.retune(self.sample_rate, &self.params.sends, new_eq, sends_changed, eq_changed);
            }
        }

        let n_channels = self.params.speakers.len();
        // Rebuilt only when the band list actually moved: a rebuild restarts
        // every detector envelope cold.
        if !self.dyn_eq.as_ref().is_some_and(|s| s.matches(&self.params.master.dynamic_eq)) {
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

        // The unifier carries the punch envelopes, so it is rebuilt only when
        // its own configuration moved — a rebuild restarts them cold.
        if old.master.bass != self.params.master.bass
            || old.master.lf_targets != self.params.master.lf_targets
        {
            self.unifier =
                build_unifier(self.sample_rate, n_channels, &self.params, self.unify_done);
            self.decorrelator =
                build_decorrelator(self.sample_rate, n_channels, &self.params, self.unify_done);
        }

        if old.master != self.params.master {
            for chain in &mut self.causal {
                chain.retune(self.sample_rate, &old.master, &self.params.master);
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
        self.unifier =
            build_unifier(self.sample_rate, n_channels, &self.params, self.unify_done);
        self.decorrelator =
            build_decorrelator(self.sample_rate, n_channels, &self.params, self.unify_done);
    }

    /// Rebuild the per-stem routing state and gain smoothers to match
    /// `self.params.stems` — used when a stem was added or removed, where
    /// there is no previous per-index state to retune.
    fn rebuild_routes(&mut self) {
        self.routes = self
            .params
            .stems
            .iter()
            .map(|s| StemRouteState::new(self.sample_rate, &self.params.sends, &s.eq_fir))
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
    }
}
