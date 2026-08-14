//! Reference-matching parity against the Python implementation.

mod common;

use common::{deterministic_signal, Case};
use upmixer_dsp_core::match_reference::{curve, spectrum};

fn gate() -> spectrum::GateParams {
    spectrum::GateParams { absolute_db: -70.0, relative_offset_db: -10.0, epsilon: 1e-20 }
}

fn curve_params(c: &Case) -> curve::CurveParams {
    let low = c.param_f64_list("taper_low");
    let high = c.param_f64_list("taper_high");
    curve::CurveParams {
        min_freq_hz: c.param_f64("min_freq_hz"),
        max_freq_hz: c.param_f64("max_freq_hz"),
        grid_step_oct: c.param_f64("grid_step_oct"),
        smooth_sigma_oct: c.param_f64("smooth_sigma_oct"),
        norm_low_hz: c.param_f64("norm_low_hz"),
        norm_high_hz: c.param_f64("norm_high_hz"),
        confidence_floor_db: c.param_f64("confidence_floor_db"),
        taper: curve::TaperBand {
            low_start: low[0],
            low_end: low[1],
            high_start: high[0],
            high_end: high[1],
        },
        n_breakpoints: c.param_usize("n_breakpoints"),
    }
}

fn bed(c: &Case) -> Vec<Vec<f64>> {
    let n = c.param_usize("n");
    let sr = c.param_usize("sample_rate") as u32;
    let count = c.meta["params"]["channels"].as_array().expect("channels").len();
    (0..count)
        .map(|i| {
            deterministic_signal(n, sr, i as f64)
                .iter()
                .map(|v| v * (0.55 + 0.12 * i as f64))
                .collect()
        })
        .collect()
}

#[test]
fn log_grid_matches_python() {
    let c = Case::load("mr_log_grid");
    let p = curve_params(&c);
    let got = curve::log_grid(c.param_f64("high_hz"), p.min_freq_hz, p.grid_step_oct);
    c.assert_close(&got, &c.array("grid"), "log grid");
}

#[test]
fn smoothing_matches_python() {
    let c = Case::load("mr_smooth");
    let p = curve_params(&c);
    let got = curve::smooth_log_grid(&c.array("input"), p.smooth_sigma_oct, p.grid_step_oct);
    c.assert_close(&got, &c.array("output"), "smoothed curve");
}

#[test]
fn confidence_taper_matches_python() {
    let c = Case::load("mr_confidence_taper");
    let got = curve::confidence_taper(
        &c.array("correction"),
        &c.array("ref_power_db"),
        c.param_f64("confidence_floor_db"),
    );
    c.assert_close(&got, &c.array("output"), "confidence-tapered curve");
}

#[test]
fn band_edge_taper_matches_python() {
    let c = Case::load("mr_band_edge_taper");
    let p = curve_params(&c);
    let got = curve::band_edge_taper(&c.array("correction"), &c.array("freqs"), &p.taper);
    c.assert_close(&got, &c.array("output"), "band-edge-tapered curve");
}

#[test]
fn soft_clamp_matches_python() {
    let c = Case::load("mr_soft_clamp");
    let got = curve::soft_clamp(
        &c.array("input"),
        c.param_f64("limit_db"),
        c.param_f64("clamp_knee_db"),
    );
    c.assert_close(&got, &c.array("output"), "clamped curve");
}

#[test]
fn weighted_power_spectrum_matches_python() {
    let c = Case::load("mr_spectrum");
    let channels = bed(&c);
    let refs: Vec<&[f64]> = channels.iter().map(|v| v.as_slice()).collect();
    let (freqs, power) = spectrum::weighted_power_spectrum(
        &refs,
        &c.param_f64_list("weights"),
        c.param_usize("sample_rate") as u32,
        c.param_usize("n_fft"),
        &gate(),
    );
    c.assert_close(&freqs, &c.array("freqs"), "spectrum frequencies");
    c.assert_close(&power, &c.array("power"), "spectrum power");
}

#[test]
fn correction_curve_matches_python() {
    let c = Case::load("mr_curve");
    let sr = c.param_usize("sample_rate") as u32;
    let n_fft = c.param_usize("n_fft");
    let n = c.param_usize("n");

    let target = bed(&c);
    let target_refs: Vec<&[f64]> = target.iter().map(|v| v.as_slice()).collect();
    let (freqs_t, power_t) =
        spectrum::weighted_power_spectrum(&target_refs, &c.param_f64_list("target_weights"), sr, n_fft, &gate());

    let scale = c.param_f64("ref_scale");
    let reference: Vec<Vec<f64>> = c
        .param_f64_list("ref_seed_phases")
        .iter()
        .map(|phase| deterministic_signal(n, sr, *phase).iter().map(|v| v * scale).collect())
        .collect();
    let ref_refs: Vec<&[f64]> = reference.iter().map(|v| v.as_slice()).collect();
    let (freqs_r, power_r) =
        spectrum::weighted_power_spectrum(&ref_refs, &c.param_f64_list("ref_weights"), sr, n_fft, &gate());

    let got = curve::correction_curve(&freqs_t, &power_t, &freqs_r, &power_r, sr, &curve_params(&c));
    let got_freqs: Vec<f64> = got.iter().map(|(f, _)| *f).collect();
    let got_gains: Vec<f64> = got.iter().map(|(_, g)| *g).collect();
    c.assert_close(&got_freqs, &c.array("freqs"), "breakpoint frequencies");
    c.assert_close(&got_gains, &c.array("gains_db"), "breakpoint gains");
}

