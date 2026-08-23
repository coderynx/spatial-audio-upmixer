//! The route-scale pass: the normalization the preview measures for itself.

use std::sync::Arc;
use upmixer_dsp_core::stream::engine::{PreviewEngine, StemSource};
use upmixer_dsp_core::stream::params::EngineParams;
use upmixer_dsp_core::stream::scale::RouteScalePass;

const SR: u32 = 48_000;
const FRAMES: usize = SR as usize * 4;

/// A dense stereo stem: a centred tone under a decorrelated bed, so a
/// band-limited send has both content to keep and content to lose.
fn stem() -> Arc<StemSource> {
    let mut noise = 12345u32;
    let mut random = || {
        noise = noise.wrapping_mul(1664525).wrapping_add(1013904223);
        (noise >> 9) as f32 / 8388608.0 - 1.0
    };
    let mut left = Vec::with_capacity(FRAMES);
    let mut right = Vec::with_capacity(FRAMES);
    for i in 0..FRAMES {
        let tone = 0.3 * (2.0 * std::f64::consts::PI * 220.0 * i as f64 / SR as f64).sin() as f32;
        left.push(tone + 0.2 * random());
        right.push(tone + 0.2 * random());
    }
    Arc::new(StemSource { left, right })
}

fn engine(routing: &str) -> PreviewEngine {
    let params: EngineParams = serde_json::from_str(&format!(
        r#"{{
            "speakers": [
                {{"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                 "downmix": [1.0, 0.0]}},
                {{"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                 "downmix": [0.0, 1.0]}},
                {{"name": "SL", "azimuth_rad": 1.92, "elevation_rad": 0.0, "group_gain": 1.0,
                 "downmix": [0.7071, 0.0]}},
                {{"name": "SR", "azimuth_rad": -1.92, "elevation_rad": 0.0, "group_gain": 1.0,
                 "downmix": [0.0, 0.7071]}}
            ],
            "shapes": ["left", "right", "surround_left", "surround_right"],
            "sends": {{"surround_bass_cutoff_hz": 250.0,
                      "height_low_rolloff_hz": 150.0, "height_low_rolloff_gain": 0.15,
                      "height_crossover_hz": 3000.0, "height_high_shelf_gain": 1.5,
                      "height_directional_band_hz": 8000.0,
                      "height_directional_band_gain": 1.0,
                      "lfe_cutoff_hz": 120.0, "lfe_filter_order": 4, "lfe_gain": 1.0}},
            "stems": [{{"routing": {routing}, "rebalance_db": 0.0,
                       "enabled": true, "eq_fir": [], "route_scale": 1.0}}],
            "master": {{}},
            "output_mode": "native",
            "bypass_mastering": true,
            "soft_limit_threshold": 0.0
        }}"#
    ))
    .expect("engine parameters");
    PreviewEngine::new(SR, params, vec![stem()])
}

fn measure(engine: &PreviewEngine) -> f64 {
    let mut pass = RouteScalePass::new(engine);
    for _ in 0..1000 {
        if let Some(scales) = pass.advance(SR as usize / 4) {
            return scales[0];
        }
    }
    panic!("route-scale pass never finished");
}

/// `masteringProfiles.ts::estimateRouteScale`, which is what the host serves
/// and what the engine renders on until the pass lands.
fn estimate(route: &[(&str, f64)]) -> f64 {
    let sum: f64 = route
        .iter()
        .map(|(name, weight)| {
            let channel_weight = if *name == "SL" || *name == "SR" { 1.41 } else { 1.0 };
            channel_weight * weight * weight
        })
        .sum();
    1.0 / sum.sqrt()
}

#[test]
fn a_stem_on_the_front_pair_alone_normalizes_to_its_own_route_gain() {
    // Nothing is filtered, so the routed power is the input power scaled by
    // the route gain and the answer is exact — and the estimate agrees.
    for gain in [0.5, 0.9] {
        let engine = engine(&format!(r#"[["FL", {gain}], ["FR", {gain}]]"#));
        let scale = measure(&engine);
        assert!(
            (scale - 1.0 / gain).abs() < 1e-6,
            "gain {gain} measured {scale}, wanted {}",
            1.0 / gain
        );
    }
}

#[test]
fn a_band_limited_send_moves_the_measurement_the_route_weights_cannot_see() {
    // The estimate reads the same weights either way; what it cannot read is
    // how much of this stem survives the surround send's 250 Hz highpass and
    // its decorrelator.
    let route = [("FL", 0.4), ("FR", 0.4), ("SL", 0.3), ("SR", 0.3)];
    let routing = r#"[["FL", 0.4], ["FR", 0.4], ["SL", 0.3], ["SR", 0.3]]"#;
    let measured = measure(&engine(routing));

    let db = 20.0 * (measured / estimate(&route)).log10();
    assert!(db.abs() > 0.5, "estimate was already within {db} dB of the measurement");
}

#[test]
fn a_measurement_belongs_to_the_mix_it_was_measured_on() {
    let routing = r#"[["FL", 0.4], ["FR", 0.4], ["SL", 0.3], ["SR", 0.3]]"#;
    let mut engine = engine(routing);
    let scales = [measure(&engine)];
    engine.set_route_scales(&scales);
    assert!((engine.route_scale(0) - scales[0]).abs() < 1e-12);

    // A fader move keeps it; a routing move drops it back to the estimate the
    // host served.
    let mut params = engine.params().clone();
    params.stems[0].rebalance_db = -3.0;
    engine.update_params(params.clone());
    assert!((engine.route_scale(0) - scales[0]).abs() < 1e-12);

    params.stems[0].routing[0].1 = 0.8;
    engine.update_params(params);
    assert!((engine.route_scale(0) - 1.0).abs() < 1e-12);
}
