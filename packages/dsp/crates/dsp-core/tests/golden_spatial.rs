//! Ambisonic, voicing, decode, and crosstalk parity against Python.

mod common;

use common::{deterministic_signal, Case};
use upmixer_dsp_core::spatial::{
    ambisonics::{self, DecodeFilterSet, HoaBus, N_ACN_CHANNELS},
    voicing::{apply_voicing, VoicingParams},
    xtc::{apply_xtc, XtcFilterSet},
};

fn stereo_pair(c: &Case) -> (Vec<f64>, Vec<f64>) {
    let n = c.param_usize("n");
    let sr = c.param_usize("sample_rate") as u32;
    let phases = c.param_f64_list("seed_phases");
    let scale = c.param_f64("scale");
    let make = |phase: f64| -> Vec<f64> {
        deterministic_signal(n, sr, phase)
            .iter()
            .map(|v| v * scale)
            .collect()
    };
    (make(phases[0]), make(phases[1]))
}

#[test]
fn ambisonic_encode_gains_match_python() {
    let c = Case::load("ambi_encode");
    let mut got = Vec::new();
    for entry in c.meta["params"]["directions"]
        .as_array()
        .expect("direction list")
    {
        let pair = entry.as_array().expect("azimuth/elevation pair");
        got.extend_from_slice(&ambisonics::encode_gains(
            pair[0].as_f64().expect("azimuth"),
            pair[1].as_f64().expect("elevation"),
        ));
    }
    c.assert_close(&got, &c.array("gains"), "encode gains");
}

#[test]
fn voicing_matches_python_for_every_profile() {
    for name in [
        "voicing_studio",
        "voicing_listening",
        "voicing_flat",
        "voicing_transaural_stereo",
    ] {
        let c = Case::load(name);
        let (left, right) = stereo_pair(&c);
        let p = VoicingParams {
            crossfeed_amount: c.param_f64("crossfeed_amount"),
            crossfeed_cutoff_hz: c.param_f64("crossfeed_cutoff_hz"),
            bass_shelf_hz: c.param_f64("bass_shelf_hz"),
            bass_shelf_gain_db: c.param_f64("bass_shelf_gain_db"),
            air_shelf_hz: c.param_f64("air_shelf_hz"),
            air_shelf_gain_db: c.param_f64("air_shelf_gain_db"),
            presence_hz: c.param_f64("presence_hz"),
            presence_gain_db: c.param_f64("presence_gain_db"),
            presence_q: c.param_f64("presence_q"),
            stereo_widen: c.param_f64("stereo_widen"),
        };
        let (got_l, got_r) = apply_voicing(&left, &right, c.param_usize("sample_rate") as u32, &p);
        c.assert_close(&got_l, &c.array("left"), "voiced left");
        c.assert_close(&got_r, &c.array("right"), "voiced right");
    }
}

#[test]
fn binaural_decode_matches_python() {
    let c = Case::load("binaural_decode");
    let n = c.param_usize("n");
    let sr = c.param_usize("sample_rate") as u32;
    let n_taps = c.param_usize("n_taps");

    let flat = c.array("taps");
    let taps: Vec<[Vec<f64>; 2]> = (0..N_ACN_CHANNELS)
        .map(|acn| {
            let base = acn * 2 * n_taps;
            [
                flat[base..base + n_taps].to_vec(),
                flat[base + n_taps..base + 2 * n_taps].to_vec(),
            ]
        })
        .collect();

    let mut hoa = HoaBus::new(n);
    for (i, channel) in hoa.channels.iter_mut().enumerate() {
        let scale = 0.1 + 0.02 * i as f64;
        *channel = deterministic_signal(n, sr, i as f64)
            .iter()
            .map(|v| v * scale)
            .collect();
    }
    let (got_l, got_r) = ambisonics::decode_to_binaural(&hoa, &DecodeFilterSet { taps });
    c.assert_close(&got_l, &c.array("left"), "decoded left");
    c.assert_close(&got_r, &c.array("right"), "decoded right");
}

#[test]
fn crosstalk_matrix_matches_python() {
    let c = Case::load("crosstalk_xtc");
    let (left, right) = stereo_pair(&c);
    let n_taps = c.param_usize("n_taps");
    let flat = c.array("taps");
    let tap = |i: usize| flat[i * n_taps..(i + 1) * n_taps].to_vec();
    let filters = XtcFilterSet {
        taps: [[tap(0), tap(1)], [tap(2), tap(3)]],
    };

    let (got_l, got_r) = apply_xtc(&left, &right, &filters);
    c.assert_close(&got_l, &c.array("left"), "crosstalk left");
    c.assert_close(&got_r, &c.array("right"), "crosstalk right");
}
