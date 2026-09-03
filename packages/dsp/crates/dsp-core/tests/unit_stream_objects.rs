use std::sync::Arc;

use upmixer_dsp_core::stream::engine::{PreviewEngine, StemSource};
use upmixer_dsp_core::stream::measure::MeasurementPass;
use upmixer_dsp_core::stream::params::EngineParams;
use upmixer_dsp_core::stream::scale::RouteScalePass;

const N: usize = 1024;

fn engine_at(
    bypass_mastering: bool,
    output_mode: &str,
    limited: bool,
    elevation_deg: f64,
    object_gain: f64,
) -> PreviewEngine {
    let limiter = if limited {
        r#", "limiter": {"ceiling_dbtp": -6.0, "lookahead_ms": 5.0,
                             "release_ms": 50.0, "safety_margin_db": 0.1}"#
    } else {
        ""
    };
    let params: EngineParams = serde_json::from_str(&format!(
        r#"{{
            "speakers": [
                {{"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0, "group_gain": 1.0}},
                {{"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0}},
                {{"name": "TFL", "azimuth_rad": 0.7854, "elevation_rad": 0.7854, "group_gain": 1.0}},
                {{"name": "TFR", "azimuth_rad": -0.7854, "elevation_rad": 0.7854, "group_gain": 1.0}}
            ],
            "shapes": ["left", "right", "height_left", "height_right"],
            "surround_downmix_coeff": 0.7071067811865476,
            "height_downmix_coeff": 0.7071067811865476,
            "sends": {{"surround_bass_cutoff_hz": 250.0,
                      "height_low_rolloff_hz": 150.0, "height_low_rolloff_gain": 0.15,
                      "height_crossover_hz": 3000.0, "height_high_shelf_gain": 1.5,
                      "height_directional_band_hz": 8000.0,
                      "height_directional_band_gain": 1.0,
                      "lfe_cutoff_hz": 120.0, "lfe_filter_order": 4, "lfe_gain": 1.0}},
            "stems": [{{"routing": [], "enabled": true, "route_scale": 1.0,
                       "object_mode": "linked-stereo",
                       "object_placement": {{"azimuth_deg": 0.0, "elevation_deg": {elevation_deg},
                                              "width_deg": 0.0, "object_size": 0.0,
                                              "gain": {object_gain}}}}}],
            "master": {{"clip": {{"ceiling_dbtp": -12.0, "clip_db": 0.5, "knee": 1.0}}{limiter}}},
            "output_mode": "{output_mode}",
            "bypass_mastering": {bypass_mastering},
            "soft_limit_threshold": 0.0
        }}"#,
    )).expect("object engine parameters");
    let source = vec![2.0f32; N];
    let stem = Arc::new(StemSource {
        left: source.clone(),
        right: source,
    });
    PreviewEngine::new(48_000, params, vec![stem])
}

fn engine(bypass_mastering: bool, output_mode: &str, limited: bool) -> PreviewEngine {
    engine_at(bypass_mastering, output_mode, limited, 0.0, 1.0)
}

fn render(bypass_mastering: bool, block: usize) -> Vec<f64> {
    let mut engine = engine(bypass_mastering, "native", false);
    let mut channels = vec![Vec::new(); 4];
    let mut scratch = vec![0.0; 4 * block];
    loop {
        let written = engine.render(&mut scratch, block);
        if written == 0 {
            break;
        }
        for (channel, output) in channels.iter_mut().enumerate() {
            output.extend_from_slice(&scratch[channel * block..channel * block + written]);
        }
    }
    channels.into_iter().flatten().collect()
}

#[test]
fn clip_processes_object_tracks_before_the_speaker_render() {
    let mastered = render(false, N);
    let bypassed = render(true, N);
    let ceiling = 10.0_f64.powf(-12.0 / 20.0);
    let peak = mastered
        .iter()
        .copied()
        .fold(0.0_f64, |m, v| m.max(v.abs()));

    assert!(
        peak > ceiling * 1.1,
        "objects were clipped after summing: {peak}"
    );
    assert!(
        peak < bypassed
            .iter()
            .copied()
            .fold(0.0_f64, |m, v| m.max(v.abs()))
    );
}

#[test]
fn object_mastering_is_block_size_independent() {
    let whole = render(false, N);
    let blocked = render(false, 128);
    assert!(whole
        .iter()
        .zip(blocked)
        .all(|(a, b)| (a - b).abs() < 1e-10));
}

#[test]
fn object_measurement_uses_the_speaker_render() {
    let engine = engine(false, "binaural", false);
    assert_eq!(engine.output_channels(), 2);
    assert_eq!(engine.fork().output_channels(), 4);
}

#[test]
fn object_measurement_also_reports_the_uncapped_monitor_render() {
    let engine = engine_at(false, "stereo", false, 30.0, 1.0);
    let mut pass = MeasurementPass::new(&engine, &[1.0, 1.0]);
    let result = loop {
        if let Some(result) = pass.advance(128) {
            break result;
        }
    };

    let [speaker_lkfs, speaker_dbtp, monitor_lkfs, monitor_dbtp] = result;
    assert!(monitor_lkfs.is_finite());
    assert!(monitor_dbtp.is_finite());
    assert!((speaker_lkfs - monitor_lkfs).abs() > 0.1 || (speaker_dbtp - monitor_dbtp).abs() > 0.1);
}

#[test]
fn object_gain_scales_the_rendered_speakers() {
    let peak = |gain| {
        let mut engine = engine_at(true, "native", false, 0.0, gain);
        let mut out = vec![0.0; 4 * N];
        engine.render(&mut out, N);
        out.into_iter()
            .fold(0.0_f64, |max, sample| max.max(sample.abs()))
    };
    assert!((peak(0.25) / peak(1.0) - 0.25).abs() < 1e-12);

    let route_scale = |gain| {
        let engine = engine_at(true, "native", false, 0.0, gain);
        let mut pass = RouteScalePass::new(&engine);
        loop {
            if let Some(scales) = pass.advance(128) {
                return scales[0];
            }
        }
    };
    assert!((route_scale(0.25) - route_scale(1.0)).abs() < 1e-12);
}

#[test]
fn object_meters_follow_delivered_speakers() {
    let mut engine = engine(false, "native", false);
    let mut out = vec![0.0; 4 * N];
    engine.render(&mut out, N);

    let meters = engine.meters();
    assert_eq!(meters.channels.len(), 4);
    for (channel, level) in meters.channels.iter().enumerate() {
        let peak = out[channel * N..(channel + 1) * N]
            .iter()
            .fold(0.0_f64, |max, sample| max.max(sample.abs()));
        assert!((level.peak - peak).abs() < 1e-12);
    }
}

#[test]
fn limiter_caps_the_rendered_object_programme() {
    let mut engine = engine(false, "native", true);
    let mut out = vec![0.0; 4 * N];
    engine.render(&mut out, N);

    let ceiling = 10.0_f64.powf((-6.0 - 0.1) / 20.0);
    let peak = out
        .iter()
        .fold(0.0_f64, |max, sample| max.max(sample.abs()));
    assert!(peak <= ceiling + 1e-6, "object limiter leaked to {peak}");
}
