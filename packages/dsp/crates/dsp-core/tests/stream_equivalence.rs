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

fn params_json(with_master: bool) -> String {
    let master = if with_master {
        r#"
        "master": {
            "compressor": {"threshold_db": -18.0, "ratio": 2.0, "attack_ms": 20.0,
                           "release_ms": 200.0, "knee_db": 6.0, "makeup_db": 0.0},
            "bass": {"sub_gain_db": 1.5, "mid_gain_db": 0.5, "mono_cutoff_hz": 80.0,
                     "excite": true, "lfe_gain_db": 1.0, "sub_cutoff_hz": 80.0,
                     "mid_cutoff_hz": 200.0, "excite_blend": 0.15, "excite_drive": 3.0},
            "limiter": {"ceiling_dbtp": -1.0, "lookahead_ms": 5.0, "release_ms": 50.0,
                        "safety_margin_db": 0.1},
            "stereo_pairs": [[0, 1]]
        },"#
    } else {
        r#""master": {},"#
    };
    format!(
        r#"{{
        "speakers": [
            {{"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0, "group_gain": 1.0}},
            {{"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0}},
            {{"name": "SL", "azimuth_rad": 1.9199, "elevation_rad": 0.0, "group_gain": 0.6}},
            {{"name": "LFE", "azimuth_rad": 0.0, "elevation_rad": 0.0, "group_gain": 1.0}}
        ],
        "lfe_index": 3,
        "shapes": ["left", "right", "surround_left", "mono"],
        "sends": {{
            "surround_bass_cutoff_hz": 250.0,
            "surround_haas_ms": [31.0, 37.0],
            "height_haas_ms": [23.0, 29.0],
            "diffuse_blend": 0.55,
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

fn stems() -> Vec<StemSource> {
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
            StemSource { left, right }
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
        },
    );
    bass_control(
        &mut offline,
        lfe,
        &[(0, 1)],
        SR,
        &BassParams {
            sub_gain_db: 1.5,
            mid_gain_db: 0.5,
            mono_cutoff_hz: Some(80.0),
            excite: true,
            lfe_gain_db: 1.0,
            sub_cutoff_hz: 80.0,
            mid_cutoff_hz: 200.0,
            excite_blend: 0.15,
            excite_drive: 3.0,
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
