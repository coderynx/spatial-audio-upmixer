use crate::spatial::panner::{PannerLayout, StemPlacement};
use crate::stream::params::{EngineParams, ObjectMode, SendShape, SpeakerParams, StemParams};
use crate::stream::routing::{shape_index, StemRouteState, AMBIENT_HEIGHT, AMBIENT_SURROUND};

use super::PreviewEngine;

pub(crate) struct StemMixRoute {
    pub regular: Vec<(usize, usize, f64)>,
    pub lfe_weight: f64,
    pub objects: Option<Vec<ObjectMixRoute>>,
    pub ambient: Vec<(usize, usize, f64)>,
    pub needs_surround: bool,
    pub needs_height: bool,
    pub has_surround: bool,
    pub has_height: bool,
}

pub(crate) struct ObjectMixRoute {
    pub authored_channel: usize,
    pub signal: usize,
    pub speakers: Vec<(usize, f64)>,
    pub gain: f64,
}

/// Playback keeps authored objects in their authored channels; normalization
/// projects them onto speakers using panning gains. Object metadata gain is
/// applied later by the speaker renderer, outside this per-stem assembly.
#[derive(Clone, Copy)]
pub(crate) enum StemAssemblyPolicy {
    Render,
    Normalization,
}

/// Add one already-routed stem to a caller-owned bed. LFE remains outside this
/// module because `LfeBus` owns its stateful contribution at bed level.
pub(crate) fn assemble_stem_into(
    mix: &StemMixRoute,
    route: &StemRouteState,
    speakers: &[SpeakerParams],
    count: usize,
    policy: StemAssemblyPolicy,
    bed: &mut [Vec<f64>],
    mut lfe_sum: Option<&mut [f64]>,
    mut gain_at: impl FnMut() -> f64,
) {
    for i in 0..count {
        let gain = gain_at();
        if let Some(objects) = &mix.objects {
            for object in objects {
                let sample = route.signal(object.signal)[i] * gain;
                match policy {
                    StemAssemblyPolicy::Render => {
                        bed[object.authored_channel][i] += sample;
                    }
                    StemAssemblyPolicy::Normalization => {
                        for &(channel, weight) in &object.speakers {
                            bed[channel][i] += sample * weight;
                        }
                    }
                }
            }
        } else {
            for &(channel, signal, weight) in &mix.regular {
                bed[channel][i] +=
                    route.signal(signal)[i] * weight * speakers[channel].group_gain * gain;
            }
        }
        for &(channel, signal, weight) in &mix.ambient {
            bed[channel][i] += route.signal(signal)[i] * weight * gain;
        }
        if let Some(lfe_sum) = lfe_sum.as_deref_mut() {
            lfe_sum[i] += route.signal(shape_index(SendShape::Mono))[i] * mix.lfe_weight * gain;
        }
    }
}

pub(crate) fn build_stem_mix_routes(
    params: &EngineParams,
    layout: &PannerLayout,
) -> Vec<StemMixRoute> {
    let mut next_object = params.speakers.len();
    params
        .stems
        .iter()
        .map(|stem| {
            let objects = direct_object_routes(params, stem, layout).map(|(gain, routes)| {
                routes
                    .into_iter()
                    .map(|(signal, speakers)| {
                        let authored_channel = next_object;
                        next_object += 1;
                        ObjectMixRoute {
                            authored_channel,
                            signal,
                            speakers,
                            gain,
                        }
                    })
                    .collect()
            });
            let mut lfe_weight = 0.0;
            let mut regular = Vec::new();
            let mut needs_surround = false;
            let mut needs_height = false;
            for (name, weight) in &stem.routing {
                if *weight == 0.0 {
                    continue;
                }
                if name == "LFE" {
                    lfe_weight += weight;
                } else if objects.is_none() {
                    if let Some(channel) = params.speaker_index(name) {
                        needs_surround |= matches!(
                            params.shapes[channel],
                            SendShape::SurroundLeft | SendShape::SurroundRight
                        );
                        needs_height |= matches!(
                            params.shapes[channel],
                            SendShape::HeightLeft | SendShape::HeightRight
                        );
                        regular.push((channel, shape_index(params.shapes[channel]), *weight));
                    }
                }
            }
            StemMixRoute {
                regular,
                lfe_weight,
                objects,
                ambient: ambient_feeds(params, stem),
                needs_surround,
                needs_height,
                has_surround: params.ambient_share(SendShape::SurroundLeft) > 0.0,
                has_height: params.ambient_share(SendShape::HeightLeft) > 0.0,
            }
        })
        .collect()
}

fn direct_object_routes(
    params: &EngineParams,
    stem: &StemParams,
    layout: &PannerLayout,
) -> Option<(f64, Vec<(usize, Vec<(usize, f64)>)>)> {
    let mode = stem.object_mode?;
    let placement = stem.object_placement.as_ref()?;
    let point = StemPlacement::new(
        placement.azimuth_deg,
        placement.elevation_deg,
        placement.width_deg,
        placement.object_size,
        0.0,
    );
    let routes: Vec<(usize, Vec<f64>)> = match mode {
        ObjectMode::LinkedStereo => layout
            .object_routes_with_metadata(
                &point,
                placement.channel_lock,
                &placement
                    .zone_exclusion
                    .iter()
                    .map(String::as_str)
                    .collect::<Vec<_>>(),
            )
            .into_iter()
            .enumerate()
            .collect(),
        ObjectMode::Mono => vec![(
            2,
            layout.exact_object_route_with_metadata(
                placement.azimuth_deg,
                placement.elevation_deg,
                placement.object_size,
                placement.channel_lock,
                &placement
                    .zone_exclusion
                    .iter()
                    .map(String::as_str)
                    .collect::<Vec<_>>(),
            ),
        )],
    };
    Some((
        placement.gain.max(0.0),
        routes
            .into_iter()
            .map(|(signal, route)| {
                let speakers = route
                    .into_iter()
                    .enumerate()
                    .filter_map(|(channel, gain)| {
                        (params.lfe_index != Some(channel) && gain > 0.0).then_some((channel, gain))
                    })
                    .collect();
                (signal, speakers)
            })
            .collect(),
    ))
}

fn ambient_feeds(params: &EngineParams, stem: &StemParams) -> Vec<(usize, usize, f64)> {
    let mut feeds = Vec::new();
    for (channel, shape) in params.shapes.iter().enumerate() {
        let (amount, slot) = match shape {
            SendShape::SurroundLeft => (stem.ambient_rear, AMBIENT_SURROUND),
            SendShape::SurroundRight => (stem.ambient_rear, AMBIENT_SURROUND + 1),
            SendShape::HeightLeft => (stem.ambient_height, AMBIENT_HEIGHT),
            SendShape::HeightRight => (stem.ambient_height, AMBIENT_HEIGHT + 1),
            _ => continue,
        };
        if amount <= 0.0 {
            continue;
        }
        let weight = amount * params.ambient_share(*shape) * params.speakers[channel].group_gain;
        feeds.push((channel, slot, weight));
    }
    feeds
}

impl PreviewEngine {
    pub(crate) fn assemble_stem_for_normalization_into(
        &self,
        stem: usize,
        count: usize,
        speakers: &mut [Vec<f64>],
    ) {
        let Some(mix) = self.stem_mix_routes.get(stem) else {
            return;
        };
        let route = &self.routes[stem];
        assemble_stem_into(
            mix,
            route,
            &self.params.speakers,
            count,
            StemAssemblyPolicy::Normalization,
            speakers,
            None,
            || 1.0,
        );
    }
}
