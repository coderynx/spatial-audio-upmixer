//! BS.1770 loudness and true-peak parity against the Python implementation.

mod common;

use common::{deterministic_signal, Case};
use upmixer_dsp_core::loudness;

fn flatten(sos: &[[f64; 6]]) -> Vec<f64> {
    sos.iter().flat_map(|r| r.iter().copied()).collect()
}

#[test]
fn k_weighting_matches_python_at_every_rate() {
    for (name, sr) in [("k_weighting_44100", 44100u32), ("k_weighting_48000", 48000),
                       ("k_weighting_96000", 96000)] {
        let c = Case::load(name);
        c.assert_close(&flatten(&loudness::k_weighting_sos(sr)), &c.array("sos"), "K-weighting SOS");
    }
}

#[test]
fn integrated_loudness_and_true_peak_match_python() {
    let c = Case::load("loudness_514");
    let sr = c.param_usize("sample_rate") as u32;
    let names: Vec<String> = c.meta["params"]["channels"]
        .as_array()
        .expect("channel list")
        .iter()
        .map(|v| v.as_str().expect("channel name").to_string())
        .collect();
    let weights = c.param_f64_list("weights");

    let n = c.param_usize("n");
    let samples: Vec<Vec<f64>> = (0..names.len())
        .map(|i| {
            deterministic_signal(n, sr, i as f64)
                .iter()
                .map(|v| v * (0.5 + 0.1 * i as f64))
                .collect()
        })
        .collect();
    let weighted: Vec<(f64, &[f64])> = weights
        .iter()
        .zip(samples.iter())
        .filter(|(w, _)| **w != 0.0)
        .map(|(w, s)| (*w, s.as_slice()))
        .collect();

    let lkfs = loudness::measure_integrated_loudness(&weighted, sr);
    let want_lkfs = c.param_f64("lkfs");
    assert!(
        (lkfs - want_lkfs).abs() <= c.tolerance,
        "integrated loudness {lkfs} vs {want_lkfs}"
    );

    let all: Vec<&[f64]> = samples.iter().map(|s| s.as_slice()).collect();
    let dbtp = loudness::measure_true_peak(&all);
    let want_dbtp = c.param_f64("true_peak_dbtp");
    assert!(
        (dbtp - want_dbtp).abs() <= c.tolerance,
        "true peak {dbtp} vs {want_dbtp}"
    );
}
