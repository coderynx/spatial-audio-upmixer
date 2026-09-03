use crate::mastering::dyneq::DynamicEq;
use crate::spatial::panner::PannerLayout;
use crate::stream::limiter::StreamingLimiter;
use crate::stream::master::{CausalChain, LfUnifier, StreamingDecorrelator};
use crate::stream::output::OutputStage;
use crate::stream::params::EngineParams;
use crate::stream::routing::{LfeBus, StemRouteState};
use crate::stream::state::StreamingCompressor;

use super::mix::{build_stem_mix_routes, StemMixRoute};
use super::{build_decorrelator, build_unifier, mastering_topology, params_update};

pub(super) struct EngineGraph {
    pub panner_layout: PannerLayout,
    pub routes: Vec<StemRouteState>,
    pub stem_mix_routes: Vec<StemMixRoute>,
    pub authored_channels: usize,
    pub rendered_channels: Vec<usize>,
    pub speaker_render_scratch: Vec<Vec<f64>>,
    pub lfe_bus: LfeBus,
    pub causal: Vec<CausalChain>,
    pub dyn_eq: Option<DynamicEq>,
    pub compressor: Option<StreamingCompressor>,
    pub unifier: Option<LfUnifier>,
    pub decorrelator: Option<StreamingDecorrelator>,
    pub limiter: Option<StreamingLimiter>,
    pub output: OutputStage,
}

impl EngineGraph {
    pub fn new(
        sample_rate: u32,
        params: &EngineParams,
        decode_override: Option<&[f64]>,
        xtc_override: Option<&[f64]>,
        base: usize,
    ) -> Self {
        let n_channels = params.speakers.len();
        let speaker_names: Vec<&str> = params
            .speakers
            .iter()
            .map(|speaker| speaker.name.as_str())
            .collect();
        let panner_layout = PannerLayout::new(&speaker_names);
        let stem_mix_routes = build_stem_mix_routes(params, &panner_layout);
        let (authored_channels, rendered_channels, post_channels) =
            mastering_topology(n_channels, params.lfe_index, &stem_mix_routes);
        let routes = params
            .stems
            .iter()
            .map(|stem| params_update::build_route(sample_rate, &params.sends, stem))
            .collect();
        let causal = (0..authored_channels)
            .map(|index| {
                CausalChain::new(sample_rate, &params.master, params.lfe_index == Some(index))
            })
            .collect();
        let dyn_eq = if authored_channels > n_channels {
            DynamicEq::new_linked(
                sample_rate,
                authored_channels,
                params.lfe_index,
                n_channels,
                params.lfe_index,
                &params.master.dynamic_eq,
            )
        } else {
            DynamicEq::new(
                sample_rate,
                n_channels,
                params.lfe_index,
                &params.master.dynamic_eq,
            )
        };
        let compressor = params.master.compressor.map(|params| {
            StreamingCompressor::new(
                params,
                sample_rate,
                if authored_channels > n_channels {
                    n_channels
                } else {
                    authored_channels
                },
            )
        });
        let output = OutputStage::new(
            sample_rate,
            params,
            decode_override.unwrap_or(&params.decode_taps),
            xtc_override.unwrap_or(&params.xtc_taps),
        );
        Self {
            panner_layout,
            routes,
            stem_mix_routes,
            authored_channels,
            rendered_channels,
            speaker_render_scratch: vec![Vec::new(); n_channels],
            lfe_bus: LfeBus::new(sample_rate, &params.sends),
            causal,
            dyn_eq,
            compressor,
            unifier: build_unifier(sample_rate, n_channels, params, base),
            decorrelator: build_decorrelator(sample_rate, n_channels, params, base),
            limiter: params.master.limiter.map(|limiter| {
                StreamingLimiter::new(limiter, sample_rate, post_channels, params.lfe_index)
            }),
            output,
        }
    }

    pub fn post_channels(&self) -> usize {
        self.rendered_channels
            .iter()
            .max()
            .map_or(0, |index| index + 1)
    }
}
