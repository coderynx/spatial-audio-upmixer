//! Kernel-level parity against SciPy, one test per kernel family.

mod common;

use common::Case;
use upmixer_dsp_core::kernels::{
    biquad, butter::{butter_sos, BandType}, filtfilt, fir_design,
    minfilter::{self, BorderMode}, upfirdn,
};

fn band(name: &str) -> BandType {
    match name {
        "low" => BandType::Low,
        "high" => BandType::High,
        other => panic!("unsupported band type {other}"),
    }
}

fn flatten(sos: &[[f64; 6]]) -> Vec<f64> {
    sos.iter().flat_map(|r| r.iter().copied()).collect()
}

#[test]
fn butter_coefficients_match_scipy() {
    for name in [
        "butter_1_low_0p00625",
        "butter_2_low_0p05",
        "butter_4_low_0p005",
        "butter_2_high_0p125",
        "butter_1_high_0p2",
        "butter_4_low_0p25",
        "butter_2_low_0p0033333333333333335",
        "butter_3_low_0p1",
        "butter_3_high_0p3",
        "butter_5_low_0p15",
    ] {
        let c = Case::load(name);
        let got = butter_sos(c.param_usize("order"), c.param_f64("wn"), band(&c.param_str("btype")));
        c.assert_close(&flatten(&got), &c.array("sos"), "sos");
    }
}

#[test]
fn butter_bandpass_coefficients_match_scipy() {
    for name in [
        "butter_bp_2_0p05_0p2",
        "butter_bp_2_0p1234_0p1466",
        "butter_bp_1_0p01_0p5",
        "butter_bp_3_0p2_0p35",
    ] {
        let c = Case::load(name);
        let got = upmixer_dsp_core::kernels::butter::butter_bandpass_sos(
            c.param_usize("order"),
            c.param_f64("low"),
            c.param_f64("high"),
        );
        c.assert_close(&flatten(&got), &c.array("sos"), "bandpass sos");
    }
}

#[test]
fn sosfilt_matches_scipy() {
    for name in ["sosfilt_2_low_0p05", "sosfilt_4_low_0p005", "sosfilt_2_high_0p125"] {
        let c = Case::load(name);
        let got = biquad::sosfilt(&c.sos("sos"), &c.array("input"));
        c.assert_close(&got, &c.array("output"), "filtered signal");
    }
}

#[test]
fn sosfilt_zi_matches_scipy() {
    for name in ["sosfilt_2_low_0p05_zi", "sosfilt_4_low_0p005_zi", "sosfilt_2_high_0p125_zi"] {
        let c = Case::load(name);
        let got: Vec<f64> = biquad::sosfilt_zi(&c.sos("sos"))
            .iter()
            .flat_map(|z| z.iter().copied())
            .collect();
        c.assert_close(&got, &c.array("zi"), "initial conditions");
    }
}

#[test]
fn sosfiltfilt_matches_scipy() {
    for name in ["sosfiltfilt_n4096_o2", "sosfiltfilt_n513_o4", "sosfiltfilt_n37_o2"] {
        let c = Case::load(name);
        let sos = c.sos("sos");
        let input = c.array("input");
        let got = filtfilt::sosfiltfilt(&sos, &input)
            .unwrap_or_else(|| panic!("{name}: expected a zero-phase result"));
        c.assert_close(&got, &c.array("output"), "zero-phase signal");
    }
}

#[test]
fn sosfiltfilt_declines_signals_scipy_would_reject() {
    // n = 8 sits under SciPy's padlen for a 2nd-order section, where the
    // pipeline falls back to a single forward pass instead.
    let c = Case::load("sosfiltfilt_n8_o2");
    let sos = c.sos("sos");
    let input = c.array("input");
    assert!(filtfilt::sosfiltfilt(&sos, &input).is_none());
    c.assert_close(&biquad::sosfilt(&sos, &input), &c.array("output"), "forward-only fallback");
}

#[test]
fn lfilter_matches_scipy() {
    for name in ["lfilter_onepole_0p001", "lfilter_onepole_0p05", "lfilter_onepole_0p5"] {
        let c = Case::load(name);
        let alpha = c.param_f64("alpha");
        let got = biquad::lfilter(&[alpha], &[1.0, -(1.0 - alpha)], &c.array("input"));
        c.assert_close(&got, &c.array("output"), "one-pole output");
    }
}

#[test]
fn upfirdn_matches_scipy() {
    let c = Case::load("upfirdn_truepeak_4x");
    let got = upfirdn::upfirdn_up(&c.array("fir"), &c.array("input"), c.param_usize("up"));
    c.assert_close(&got, &c.array("output"), "oversampled signal");
}

#[test]
fn minimum_filter1d_matches_scipy() {
    for (name, mode) in [
        ("minfilter_centered_3", BorderMode::Reflect),
        ("minfilter_centered_13", BorderMode::Reflect),
        ("minfilter_centered_241", BorderMode::Reflect),
        ("minfilter_nearest_3", BorderMode::Nearest),
        ("minfilter_nearest_13", BorderMode::Nearest),
    ] {
        let c = Case::load(name);
        let got = minfilter::minimum_filter1d(&c.array("input"), c.param_usize("size"), mode);
        c.assert_close(&got, &c.array("output"), "running minimum");
    }
}

#[test]
fn firwin2_matches_scipy() {
    for name in ["firwin2_1023", "firwin2_511", "firwin2_65"] {
        let c = Case::load(name);
        let got = fir_design::firwin2(
            c.param_usize("ntaps"),
            &c.param_f64_list("freq"),
            &c.param_f64_list("gain"),
        );
        c.assert_close(&got, &c.array("taps"), "linear-phase taps");
    }
}

#[test]
fn minimum_phase_matches_scipy() {
    for name in ["minimum_phase_1023", "minimum_phase_511"] {
        let c = Case::load(name);
        let got = fir_design::minimum_phase(&c.array("linear"));
        c.assert_close(&got, &c.array("minphase"), "minimum-phase taps");
    }
}
