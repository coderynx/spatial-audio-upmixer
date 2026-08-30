use crate::spatial::panner::{PannerLayout, StemPlacement};
use crate::stream::params::{EngineParams, ObjectMode, SendShape, StemParams};
use crate::stream::routing::{shape_index, AMBIENT_HEIGHT, AMBIENT_SURROUND};

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
            let objects = direct_object_routes(params, stem, layout).map(|routes| {
                routes
                    .into_iter()
                    .map(|(signal, speakers)| {
                        let authored_channel = next_object;
                        next_object += 1;
                        ObjectMixRoute {
                            authored_channel,
                            signal,
                            speakers,
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
) -> Option<Vec<(usize, Vec<(usize, f64)>)>> {
    let mode = stem.object_mode?;
    let placement = stem.object_placement?;
    let point = StemPlacement::new(
        placement.azimuth_deg,
        placement.elevation_deg,
        placement.width_deg,
        placement.object_size,
        0.0,
    );
    let routes: Vec<(usize, Vec<f64>)> = match mode {
        ObjectMode::LinkedStereo => layout
            .object_routes(&point)
            .into_iter()
            .enumerate()
            .collect(),
        ObjectMode::Mono => vec![(
            2,
            layout.exact_object_route(
                placement.azimuth_deg,
                placement.elevation_deg,
                placement.object_size,
            ),
        )],
    };
    Some(
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
    )
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
    pub(crate) fn mixed_stem_speakers(&self, stem: usize, count: usize) -> Vec<Vec<f64>> {
        let mut speakers = vec![vec![0.0; count]; self.params.speakers.len()];
        let Some(mix) = self.stem_mix_routes.get(stem) else {
            return speakers;
        };
        let route = &self.routes[stem];
        if let Some(objects) = &mix.objects {
            for object in objects {
                let signal = route.signal(object.signal);
                for &(channel, weight) in &object.speakers {
                    let gain = weight * self.params.speakers[channel].group_gain;
                    for (output, sample) in speakers[channel].iter_mut().zip(signal) {
                        *output += gain * sample;
                    }
                }
            }
        } else {
            for &(channel, signal, weight) in &mix.regular {
                let gain = weight * self.params.speakers[channel].group_gain;
                for (output, sample) in speakers[channel].iter_mut().zip(route.signal(signal)) {
                    *output += gain * sample;
                }
            }
        }
        for &(channel, signal, gain) in &mix.ambient {
            for (output, sample) in speakers[channel].iter_mut().zip(route.signal(signal)) {
                *output += gain * sample;
            }
        }
        speakers
    }
}
