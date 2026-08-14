//! Mastering-stage parity against the Python implementation.

mod common;

use common::{deterministic_signal, Case};
use upmixer_dsp_core::mastering::{
    bass::{bass_control, BassParams},
    compressor::{bus_compress, CompParams},
    eq::{build_fir, spectral_shape},
    limiter::{lookahead_limit, LimiterParams},
};

/// The 4-channel bed every stage fixture is measured against.
fn stage_bed(c: &Case) -> (Vec<Vec<f64>>, Vec<String>) {
    let names: Vec<String> = c.meta["params"]["channels"]
        .as_array()
        .expect("channel list")
        .iter()
        .map(|v| v.as_str().expect("channel name").to_string())
        .collect();
    let n = c.param_usize("n");
    let sr = c.param_usize("sample_rate") as u32;
    let hot = c.meta["params"]["hot"].as_bool().unwrap_or(false);
    let bed = (0..names.len())
        .map(|i| {
            deterministic_signal(n, sr, i as f64)
                .iter()
                .map(|v| {
                    let scaled = v * (0.55 + 0.12 * i as f64);
                    if hot {
                        (scaled * 3.2).clamp(-1.5, 1.5)
                    } else {
                        scaled
                    }
                })
                .collect()
        })
        .collect();
    (bed, names)
}

fn assert_bed(c: &Case, got: &[Vec<f64>], names: &[String]) {
    for (i, name) in names.iter().enumerate() {
        c.assert_close(&got[i], &c.array(&format!("ch_{name}")), &format!("channel {name}"));
    }
}

#[test]
fn signal_generator_matches_python() {
    let c = Case::load("generator_parity");
    let got = deterministic_signal(
        c.param_usize("n"),
        c.param_usize("sample_rate") as u32,
        c.param_f64("seed_phase"),
    );
    c.assert_close(&got, &c.array("signal"), "generated signal");
}

#[test]
fn eq_filter_design_matches_python() {
    for name in ["eq_fir_spatial_transparent", "eq_fir_spatial_air", "eq_fir_spatial_warm",
                 "eq_fir_spatial_present", "eq_fir_atmos_streaming"] {
        let c = Case::load(name);
        let breakpoints: Vec<(f64, f64)> = c.meta["params"]["breakpoints"]
            .as_array()
            .expect("breakpoint list")
            .iter()
            .map(|p| {
                let pair = p.as_array().expect("breakpoint pair");
                (pair[0].as_f64().expect("hz"), pair[1].as_f64().expect("db"))
            })
            .collect();
        let got = build_fir(&breakpoints, c.param_usize("sample_rate") as u32,
                            c.param_usize("n_taps"));
        c.assert_close(&got, &c.array("taps"), "minimum-phase taps");
    }
}

#[test]
fn eq_application_matches_python() {
    for (name, profile) in [
        ("eq_apply_atmos_streaming_1p0", "atmos-streaming"),
        ("eq_apply_spatial_warm_0p6", "spatial-warm"),
    ] {
        let c = Case::load(name);
        let fir = Case::load(&format!("eq_fir_{}", profile.replace('-', "_")));
        let (mut bed, names) = stage_bed(&c);
        let lfe = names.iter().position(|n| n == "LFE");
        spectral_shape(&mut bed, lfe, &fir.array("taps"), c.param_f64("strength"));
        assert_bed(&c, &bed, &names);
    }
}

#[test]
fn bus_compression_matches_python() {
    for name in ["comp_transparent", "comp_glue", "comp_warm"] {
        let c = Case::load(name);
        let (mut bed, names) = stage_bed(&c);
        let lfe = names.iter().position(|n| n == "LFE");
        let p = CompParams {
            threshold_db: c.param_f64("threshold_db"),
            ratio: c.param_f64("ratio"),
            attack_ms: c.param_f64("attack_ms"),
            release_ms: c.param_f64("release_ms"),
            knee_db: c.param_f64("knee_db"),
            makeup_db: c.param_f64("makeup_db"),
            sidechain_hpf_hz: c.meta["params"]["sidechain_hpf_hz"].as_f64(),
        };
        bus_compress(&mut bed, lfe, c.param_usize("sample_rate") as u32, &p);
        assert_bed(&c, &bed, &names);
    }
}

#[test]
fn bass_control_matches_python() {
    for name in [
        "bass_boost", "bass_cut", "bass_mono", "bass_enhance", "bass_deep", "bass_cinema",
    ] {
        let c = Case::load(name);
        let (mut bed, names) = stage_bed(&c);
        let lfe = names.iter().position(|n| n == "LFE");
        let unify = c.param_f64("unify_hz");
        // `bass.py` resolves the spread and LFE mode into weights; the core
        // only ever sees the resolved table.
        let lf_targets: Vec<(usize, f64)> = c.meta["params"]["lf_targets"]
            .as_array()
            .expect("lf targets")
            .iter()
            .map(|entry| {
                let pair = entry.as_array().expect("target pair");
                (
                    pair[0].as_u64().expect("target index") as usize,
                    pair[1].as_f64().expect("target weight"),
                )
            })
            .collect();
        let p = BassParams {
            sub_gain_db: c.param_f64("sub_gain_db"),
            mid_gain_db: c.param_f64("mid_gain_db"),
            unify_hz: (unify > 0.0).then_some(unify),
            punch: c.param_f64("punch"),
            excite: c.meta["params"]["excite"].as_bool().expect("excite flag"),
            lfe_gain_db: c.param_f64("lfe_gain_db"),
            sub_cutoff_hz: c.param_f64("sub_cutoff_hz"),
            mid_cutoff_hz: c.param_f64("mid_cutoff_hz"),
            excite_blend: c.param_f64("excite_blend"),
            excite_drive: c.param_f64("excite_drive"),
            punch_fast_ms: c.param_f64("punch_fast_ms"),
            punch_slow_ms: c.param_f64("punch_slow_ms"),
            punch_max_db: c.param_f64("punch_max_db"),
        };
        bass_control(&mut bed, lfe, &lf_targets, c.param_usize("sample_rate") as u32, &p);
        assert_bed(&c, &bed, &names);
    }
}

#[test]
fn lookahead_limiter_matches_python() {
    let c = Case::load("limiter_apply");
    let (mut bed, names) = stage_bed(&c);
    let p = LimiterParams {
        ceiling_dbtp: c.param_f64("ceiling_dbtp"),
        lookahead_ms: c.param_f64("lookahead_ms"),
        release_ms: c.param_f64("release_ms"),
        safety_margin_db: c.param_f64("safety_margin_db"),
    };
    let gr = lookahead_limit(&mut bed, c.param_usize("sample_rate") as u32, &p);
    assert!(gr > 0.0, "the fixture bed should drive the limiter");
    assert_bed(&c, &bed, &names);
}
