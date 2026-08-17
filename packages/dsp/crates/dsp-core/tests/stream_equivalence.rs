//! The preview engine must render the same samples regardless of block size,
//! and its mastering stages must agree with the offline chain the export
//! runs. This is the property the whole port rests on: if it holds, the
//! browser preview *is* the export rather than an approximation of it.

mod common;

use common::deterministic_signal;
use upmixer_dsp_core::mastering::{
    bass::{bass_control, BassParams},
    compressor::{bus_compress, CompParams},
    limiter::{lookahead_limit, LimiterParams},
};
use upmixer_dsp_core::stream::engine::{PreviewEngine, StemSource};
use upmixer_dsp_core::stream::params::EngineParams;

const SR: u32 = 48_000;
const N: usize = 24_000;
const N_ACN: usize = 16;
const DECODE_TAPS: usize = 64;

fn params_json(with_master: bool) -> String {
    let master = if with_master {
        r#"
        "master": {
            "compressor": {"threshold_db": -18.0, "ratio": 2.0, "attack_ms": 20.0,
                           "release_ms": 200.0, "knee_db": 6.0, "makeup_db": 0.0,
                           "sidechain_hpf_hz": 100.0},
            "bass": {"sub_gain_db": 1.5, "mid_gain_db": 0.5, "unify_hz": 80.0,
                     "punch": 0.25, "excite": true, "lfe_gain_db": 1.0,
                     "sub_cutoff_hz": 80.0, "mid_cutoff_hz": 200.0,
                     "excite_blend": 0.15, "excite_drive": 3.0,
                     "punch_fast_ms": 10.0, "punch_slow_ms": 120.0, "punch_max_db": 6.0,
                     "decorrelate": 0.35, "decorr_low_hz": 100.0, "decorr_high_hz": 300.0,
                     "decorr_sections": 32, "decorr_max_delay_ms": 30.0,
                     "decorr_fast_ms": 30.0, "decorr_slow_ms": 300.0},
            "limiter": {"ceiling_dbtp": -1.0, "lookahead_ms": 5.0, "release_ms": 50.0,
                        "safety_margin_db": 0.1},
            "lf_targets": [[0, 0.4], [1, 0.4], [2, 0.1], [3, 0.1]]
        },"#
    } else {
        r#""master": {},"#
    };
    format!(
        r#"{{
        "speakers": [
            {{"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
              "downmix": [1.0, 0.0]}},
            {{"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
              "downmix": [0.0, 1.0]}},
            {{"name": "SL", "azimuth_rad": 1.9199, "elevation_rad": 0.0, "group_gain": 0.6,
              "downmix": [0.7071067811865476, 0.0]}},
            {{"name": "LFE", "azimuth_rad": 0.0, "elevation_rad": 0.0, "group_gain": 1.0}}
        ],
        "lfe_index": 3,
        "shapes": ["left", "right", "surround_left", "mono"],
        "sends": {{
            "surround_bass_cutoff_hz": 250.0,
            "height_low_rolloff_hz": 150.0,
            "height_low_rolloff_gain": 0.15,
            "height_crossover_hz": 3000.0,
            "height_high_shelf_gain": 1.5,
            "lfe_cutoff_hz": 120.0,
            "lfe_filter_order": 4,
            "lfe_gain": 0.31622776601683794
        }},
        "stems": [
            {{"routing": [["FL", 0.9], ["FR", 0.9], ["SL", 0.4], ["LFE", 0.3]],
              "rebalance_db": 0.0, "enabled": true}},
            {{"routing": [["FL", 0.5], ["FR", 0.5], ["SL", 0.7], ["LFE", 0.1]],
              "rebalance_db": -2.0, "enabled": true}}
        ],
        {master}
        "output_mode": "native"
    }}"#
    )
}

fn stems() -> Vec<std::sync::Arc<StemSource>> {
    (0..2)
        .map(|i| {
            let left: Vec<f32> = deterministic_signal(N, SR, i as f64)
                .iter()
                .map(|v| (v * 0.6) as f32)
                .collect();
            let right: Vec<f32> = deterministic_signal(N, SR, i as f64 + 3.0)
                .iter()
                .map(|v| (v * 0.6) as f32)
                .collect();
            std::sync::Arc::new(StemSource { left, right })
        })
        .collect()
}

fn render_in_blocks(block: usize, with_master: bool) -> Vec<Vec<f64>> {
    let params: EngineParams =
        serde_json::from_str(&params_json(with_master)).expect("engine params");
    let n_channels = params.speakers.len();
    let mut engine = PreviewEngine::new(SR, params, stems());

    let mut out = vec![Vec::with_capacity(N); n_channels];
    let mut scratch = vec![0.0; n_channels * block];
    loop {
        let written = engine.render(&mut scratch, block);
        if written == 0 {
            break;
        }
        for channel in 0..n_channels {
            out[channel].extend_from_slice(&scratch[channel * block..channel * block + written]);
        }
    }
    out
}

fn assert_same(a: &[Vec<f64>], b: &[Vec<f64>], tolerance: f64, what: &str) {
    assert_eq!(a.len(), b.len(), "{what}: channel count");
    for (ch, (x, y)) in a.iter().zip(b.iter()).enumerate() {
        assert_eq!(x.len(), y.len(), "{what}: channel {ch} length");
        for (i, (p, q)) in x.iter().zip(y.iter()).enumerate() {
            assert!(
                (p - q).abs() <= tolerance,
                "{what}: channel {ch} sample {i}: {p} vs {q}"
            );
        }
    }
}

#[test]
fn routing_output_is_independent_of_block_size() {
    let reference = render_in_blocks(N, false);
    for block in [128usize, 512, 4096] {
        assert_same(&render_in_blocks(block, false), &reference, 1e-9,
                    &format!("routing at block {block}"));
    }
}

#[test]
fn full_chain_output_is_independent_of_block_size() {
    let reference = render_in_blocks(N, true);
    assert!(
        reference.iter().any(|c| c.iter().any(|v| v.abs() > 1e-6)),
        "the fixture should produce audible output"
    );
    // 128 is the Web Audio render quantum; the others check that nothing in
    // the two look-ahead queues depends on a particular block length.
    for block in [128usize, 333, 4096] {
        assert_same(&render_in_blocks(block, true), &reference, 1e-8,
                    &format!("full chain at block {block}"));
    }
}

#[test]
fn streaming_mastering_matches_the_offline_chain() {
    // Take the engine's unmastered bed, run the offline stages over it, and
    // compare against the engine rendering the same bed with mastering on.
    let bed = render_in_blocks(N, false);
    let mut offline = bed.clone();
    let lfe = Some(3usize);

    bus_compress(
        &mut offline,
        lfe,
        SR,
        &CompParams {
            threshold_db: -18.0,
            ratio: 2.0,
            attack_ms: 20.0,
            release_ms: 200.0,
            knee_db: 6.0,
            makeup_db: 0.0,
            sidechain_hpf_hz: Some(100.0),
        },
    );
    bass_control(
        &mut offline,
        lfe,
        &[(0, 0.4), (1, 0.4), (2, 0.1), (3, 0.1)],
        SR,
        &BassParams {
            sub_gain_db: 1.5,
            mid_gain_db: 0.5,
            unify_hz: Some(80.0),
            punch: 0.25,
            excite: true,
            lfe_gain_db: 1.0,
            sub_cutoff_hz: 80.0,
            mid_cutoff_hz: 200.0,
            excite_blend: 0.15,
            excite_drive: 3.0,
            punch_fast_ms: 10.0,
            punch_slow_ms: 120.0,
            punch_max_db: 6.0,
            decorrelate: 0.35,
            decorr_low_hz: 100.0,
            decorr_high_hz: 300.0,
            decorr_sections: 32,
            decorr_max_delay_ms: 30.0,
            decorr_fast_ms: 30.0,
            decorr_slow_ms: 300.0,
        },
    );
    lookahead_limit(
        &mut offline,
        SR,
        &LimiterParams {
            ceiling_dbtp: -1.0,
            lookahead_ms: 5.0,
            release_ms: 50.0,
            safety_margin_db: 0.1,
        },
    );

    assert_same(&render_in_blocks(128, true), &offline, 1e-6, "streaming vs offline mastering");
}

/// Swap the collapse stage in without changing anything else.
fn params_with_mode(mode: &str) -> String {
    params_json(true).replace(r#""output_mode": "native""#, &format!(r#""output_mode": "{mode}""#))
}

fn render_mode(mode: &str, block: usize) -> Vec<Vec<f64>> {
    let params: EngineParams = serde_json::from_str(&params_with_mode(mode)).expect("engine params");
    let bed_channels = params.speakers.len();
    let mut engine = PreviewEngine::new(SR, params, stems());
    let out_channels = engine.output_channels();

    let mut out = vec![Vec::with_capacity(N); out_channels];
    let mut scratch = vec![0.0; bed_channels.max(2) * block];
    loop {
        let written = engine.render(&mut scratch, block);
        if written == 0 {
            break;
        }
        for channel in 0..out_channels {
            out[channel].extend_from_slice(&scratch[channel * block..channel * block + written]);
        }
    }
    out
}

#[test]
fn stereo_collapse_is_two_channels_and_block_size_independent() {
    let reference = render_mode("stereo", N);
    assert_eq!(reference.len(), 2);
    assert!(reference[0].iter().any(|v| v.abs() > 1e-6), "downmix should carry signal");
    assert_same(&render_mode("stereo", 128), &reference, 1e-8, "stereo downmix");
}

#[test]
fn binaural_collapse_is_two_channels_and_block_size_independent() {
    // A synthetic decode bank stands in for the shipped HRIR set; the point
    // here is the streaming convolution and the LFE-before-voicing order,
    // not the filters themselves.
    let taps: Vec<String> = (0..N_ACN * 2 * DECODE_TAPS)
        .map(|i| format!("{:.6}", ((i % 17) as f64 - 8.0) / 400.0))
        .collect();
    let with_decode = |mode: &str| {
        params_with_mode(mode).replace(
            r#""output_mode""#,
            &format!(r#""decode_taps": [{}], "output_mode""#, taps.join(",")),
        )
    };

    let render = |block: usize| -> Vec<Vec<f64>> {
        let params: EngineParams =
            serde_json::from_str(&with_decode("binaural")).expect("engine params");
        let bed_channels = params.speakers.len();
        let mut engine = PreviewEngine::new(SR, params, stems());
        let mut out = vec![Vec::with_capacity(N); 2];
        let mut scratch = vec![0.0; bed_channels.max(2) * block];
        loop {
            let written = engine.render(&mut scratch, block);
            if written == 0 {
                break;
            }
            for channel in 0..2 {
                out[channel].extend_from_slice(&scratch[channel * block..channel * block + written]);
            }
        }
        out
    };

    let reference = render(N);
    assert!(reference[0].iter().any(|v| v.abs() > 1e-6), "binaural should carry signal");
    assert_same(&render(128), &reference, 1e-8, "binaural collapse");
}

#[test]
fn measuring_leaves_the_transport_where_it_found_it() {
    let params: EngineParams =
        serde_json::from_str(&params_with_mode("stereo")).expect("engine params");
    let mut engine = PreviewEngine::new(SR, params, stems());

    let (lkfs, dbtp) = engine.measure(&[1.0, 1.0]);
    assert!(lkfs > -70.0, "the fixture should be measurable, got {lkfs} LKFS");
    assert!(dbtp > -120.0 && dbtp < 6.0, "implausible true peak {dbtp} dBTP");
    assert_eq!(engine.position(), 0, "measuring must rewind");

    // Measuring twice must agree; a carried filter state would show up here.
    let (again, _) = engine.measure(&[1.0, 1.0]);
    assert!((again - lkfs).abs() < 1e-12, "{again} vs {lkfs}");
}

#[test]
fn seeking_resumes_the_same_audio_the_first_pass_produced() {
    let params: EngineParams = serde_json::from_str(&params_json(true)).expect("engine params");
    let n_channels = params.speakers.len();
    let mut engine = PreviewEngine::new(SR, params, stems());

    let block = 512;
    let mut scratch = vec![0.0; n_channels * block];
    let mut straight = Vec::new();
    loop {
        let written = engine.render(&mut scratch, block);
        if written == 0 {
            break;
        }
        straight.extend_from_slice(&scratch[..written]);
    }

    // Seek past the point where every filter state has settled, then compare.
    let target = 12_000;
    engine.seek(target);
    assert_eq!(engine.position(), target);
    let written = engine.render(&mut scratch, block);
    assert_eq!(written, block);

    // The run-up settles every state, so the seek lands on the same audio a
    // straight play-through produces there.
    for i in 0..block {
        let diff = (scratch[i] - straight[target + i]).abs();
        assert!(diff < 1e-6, "sample {i} after seek: {} vs {}", scratch[i], straight[target + i]);
    }
}

#[test]
fn seeking_to_the_top_silences_the_stale_meters_a_straight_seek_would_leave() {
    // Regression: stopping (pause, then seek to 0) used to leave the meters
    // reporting whatever was last playing — `jump_to` didn't reset them, and
    // a seek to frame 0 has no preroll render to overwrite them for real.
    let params: EngineParams = serde_json::from_str(&params_json(true)).expect("engine params");
    let n_channels = params.speakers.len();
    let mut engine = PreviewEngine::new(SR, params, stems());
    let mut scratch = vec![0.0; n_channels * 4096];
    engine.render(&mut scratch, 4096);
    assert!(engine.meters().output[0].peak > 0.0, "should be playing something first");

    engine.seek(0);

    let meters = engine.meters();
    assert!(meters.stems.iter().all(|pair| pair[0].peak == 0.0 && pair[1].peak == 0.0));
    assert!(meters.channels.iter().all(|level| level.peak == 0.0));
    assert!(meters.output.iter().all(|level| level.peak == 0.0 && level.rms == 0.0));
}

#[test]
fn stem_spectrum_registers_a_playing_stem_and_silences_a_disabled_one() {
    let params: EngineParams = serde_json::from_str(&params_json(false)).expect("engine params");
    let n_channels = params.speakers.len();
    let mut engine = PreviewEngine::new(SR, params, stems());
    let mut scratch = vec![0.0; n_channels * 4096];
    engine.render(&mut scratch, 4096);

    let spectrum = engine.stem_spectrum();
    assert_eq!(spectrum.len(), 2, "one (level, centroid) pair per stem");
    for &(level, centroid) in &spectrum {
        assert!(level > 0.0 && level <= 1.0, "level {level} out of range");
        assert!((0.0..=1.0).contains(&centroid), "centroid {centroid} out of range");
    }

    let muted = params_json(false).replace(
        r#"{"routing": [["FL", 0.9], ["FR", 0.9], ["SL", 0.4], ["LFE", 0.3]],
              "rebalance_db": 0.0, "enabled": true}"#,
        r#"{"routing": [["FL", 0.9], ["FR", 0.9], ["SL", 0.4], ["LFE", 0.3]],
              "rebalance_db": 0.0, "enabled": false}"#,
    );
    engine.update_params(serde_json::from_str(&muted).expect("engine params"));
    engine.render(&mut scratch, 4096);
    let spectrum = engine.stem_spectrum();
    assert_eq!(spectrum[0], (0.0, 0.0), "a disabled stem reports silence");
    assert!(spectrum[1].0 > 0.0, "the other stem keeps registering");
}

#[test]
fn meters_track_the_emitted_block() {
    let params: EngineParams = serde_json::from_str(&params_json(true)).expect("engine params");
    let n_channels = params.speakers.len();
    let mut engine = PreviewEngine::new(SR, params, stems());
    let mut scratch = vec![0.0; n_channels * 512];
    engine.render(&mut scratch, 512);

    let meters = engine.meters();
    assert_eq!(meters.stems.len(), 2, "one left/right pair per stem");
    assert_eq!(meters.channels.len(), n_channels);
    assert!(
        meters.stems.iter().all(|pair| pair[0].peak > 0.0 && pair[1].peak > 0.0),
        "both channels of every stem should register"
    );
    assert!(meters.channels[0].peak > 0.0, "FL should register");
    assert!(meters.channels[0].rms <= meters.channels[0].peak);
}

#[test]
fn disabling_a_stem_through_update_params_silences_it_without_a_reload() {
    let params: EngineParams = serde_json::from_str(&params_json(false)).expect("engine params");
    let n_channels = params.speakers.len();
    let mut engine = PreviewEngine::new(SR, params, stems());
    let mut scratch = vec![0.0; n_channels * 512];
    engine.render(&mut scratch, 512);
    let before = engine.meters().stems[0][0].peak;
    assert!(before > 0.0);

    let muted = params_json(false).replace(
        r#"{"routing": [["FL", 0.9], ["FR", 0.9], ["SL", 0.4], ["LFE", 0.3]],
              "rebalance_db": 0.0, "enabled": true}"#,
        r#"{"routing": [["FL", 0.9], ["FR", 0.9], ["SL", 0.4], ["LFE", 0.3]],
              "rebalance_db": 0.0, "enabled": false}"#,
    );
    engine.update_params(serde_json::from_str(&muted).expect("engine params"));
    engine.render(&mut scratch, 512);
    assert_eq!(engine.meters().stems[0][0].peak, 0.0);
    assert_eq!(engine.meters().stems[0][1].peak, 0.0);
    assert!(engine.meters().stems[1][0].peak > 0.0, "the other stem keeps playing");
}

#[test]
fn update_params_mid_playback_never_starves_the_next_quantum() {
    // The regression this guards: `update_params` used to end with a seek,
    // which re-renders a 500 ms preroll synchronously — on the worklet's
    // audio thread that starves the render deadline and drops the very next
    // quantum. It must not do that: the call itself should be cheap, and the
    // quantum right after it should come back full and at the expected
    // position, exactly like any other quantum.
    let params: EngineParams = serde_json::from_str(&params_json(true)).expect("engine params");
    let n_channels = params.speakers.len();
    let mut engine = PreviewEngine::new(SR, params, stems());
    let mut scratch = vec![0.0; n_channels * 128];

    for _ in 0..10 {
        assert_eq!(engine.render(&mut scratch, 128), 128);
    }
    let position_before = engine.position();

    let muted = params_json(true).replace(
        r#"{"routing": [["FL", 0.9], ["FR", 0.9], ["SL", 0.4], ["LFE", 0.3]],
              "rebalance_db": 0.0, "enabled": true}"#,
        r#"{"routing": [["FL", 0.9], ["FR", 0.9], ["SL", 0.4], ["LFE", 0.3]],
              "rebalance_db": 0.0, "enabled": false}"#,
    );
    engine.update_params(serde_json::from_str(&muted).expect("engine params"));

    assert_eq!(engine.position(), position_before, "update_params must not move the playhead");
    let written = engine.render(&mut scratch, 128);
    assert_eq!(written, 128, "the quantum right after a param update must not come back short");
    assert_eq!(engine.position(), position_before + 128);
}

#[test]
fn update_params_with_unchanged_params_is_a_true_no_op() {
    // A no-op edit (e.g. a redundant `apply()`) must not perturb the stream:
    // an engine that gets the same params handed back through
    // `update_params` mid-playback has to keep producing exactly what an
    // engine that was never touched produces, because every filter it
    // touches gets retuned to the value it already had.
    let params_a: EngineParams = serde_json::from_str(&params_json(true)).expect("engine params");
    let params_b: EngineParams = serde_json::from_str(&params_json(true)).expect("engine params");
    let n_channels = params_a.speakers.len();
    let mut untouched = PreviewEngine::new(SR, params_a, stems());
    let mut touched = PreviewEngine::new(SR, params_b, stems());

    let mut scratch_a = vec![0.0; n_channels * 512];
    let mut scratch_b = vec![0.0; n_channels * 512];
    untouched.render(&mut scratch_a, 512);
    touched.render(&mut scratch_b, 512);
    assert_eq!(scratch_a, scratch_b, "identical construction should render identically");

    touched.update_params(serde_json::from_str(&params_json(true)).expect("engine params"));

    for _ in 0..8 {
        let wa = untouched.render(&mut scratch_a, 512);
        let wb = touched.render(&mut scratch_b, 512);
        assert_eq!(wa, wb);
        for (i, (a, b)) in scratch_a[..wa * n_channels].iter().zip(&scratch_b[..wb * n_channels]).enumerate() {
            assert!((a - b).abs() < 1e-12, "sample {i} diverged after a no-op update_params: {a} vs {b}");
        }
    }
}

#[test]
fn clearing_the_limiter_through_update_params_actually_removes_it() {
    // `rewind` used to only ever assign `self.limiter` when the new config
    // was `Some`, so a limiter switched off mid-session stayed live with its
    // old ceiling forever. Drive a signal that clips the ceiling, confirm
    // the limiter is holding it down, then remove it and confirm the peak is
    // freed to exceed that ceiling.
    let ceiling_linear = 10.0_f64.powf((-6.0 - 0.1) / 20.0);
    let hot_params = params_json(true)
        .replace(r#""rebalance_db": 0.0, "enabled": true"#, r#""rebalance_db": 24.0, "enabled": true"#)
        .replace(r#""ceiling_dbtp": -1.0"#, r#""ceiling_dbtp": -6.0"#);
    let params: EngineParams = serde_json::from_str(&hot_params).expect("engine params");
    let n_channels = params.speakers.len();
    let mut engine = PreviewEngine::new(SR, params, stems());
    let mut scratch = vec![0.0; n_channels * 4096];

    engine.render(&mut scratch, 4096);
    let limited_peak = engine.meters().output[0].peak.max(engine.meters().output[1].peak);
    assert!(
        limited_peak <= ceiling_linear + 1e-3,
        "limiter should hold the boosted signal at its ceiling, got {limited_peak}"
    );

    let no_limiter = hot_params.replace(
        r#""limiter": {"ceiling_dbtp": -6.0, "lookahead_ms": 5.0, "release_ms": 50.0,
                        "safety_margin_db": 0.1},"#,
        "",
    );
    engine.update_params(serde_json::from_str(&no_limiter).expect("engine params"));

    // The look-ahead queues still hold audio the (now-removed) limiter had
    // already shaped; render past that horizon before checking the peak.
    for _ in 0..6 {
        engine.render(&mut scratch, 4096);
    }
    let unlimited_peak = engine.meters().output[0].peak.max(engine.meters().output[1].peak);
    assert!(
        unlimited_peak > ceiling_linear + 0.05,
        "removing the limiter should free the peak above its old ceiling, got {unlimited_peak}"
    );
}
