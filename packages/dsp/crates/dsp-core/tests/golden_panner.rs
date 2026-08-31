//! MDAP stem panning parity against the Python original.

use upmixer_dsp_core::spatial::panner::{
    object_routes_with_metadata, resolve_placement, StemPlacement,
};

#[test]
fn object_size_routes_are_constant_power() {
    let speakers = [
        "FL", "FR", "C", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR",
    ];
    let placement = StemPlacement::new(0.0, 20.0, 0.0, 0.5, 0.0);
    let [left, right] = upmixer_dsp_core::spatial::panner::object_routes(&placement, &speakers);
    assert!((left.iter().map(|value| value * value).sum::<f64>() - 1.0).abs() < 1e-12);
    assert_eq!(left, right);
}

#[test]
fn object_size_matches_bs2127_reference() {
    let speakers = [
        "FL", "FR", "C", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR",
    ];
    let expected = [
        0.21203650155303891,
        0.21203650155303891,
        0.92226178446293861,
        0.1575595984595608,
        0.1575595984595608,
        1.8045445188519192e-7,
        1.8045445188519189e-7,
        0.070228878689574711,
        0.070228878689574697,
        2.2209369959247762e-8,
        2.2209369959247762e-8,
    ];
    let placement = StemPlacement::new(0.0, 0.0, 0.0, 0.2, 0.0);
    let [route, _] = upmixer_dsp_core::spatial::panner::object_routes(&placement, &speakers);
    assert!(
        route
            .iter()
            .zip(expected)
            .all(|(actual, expected)| (actual - expected).abs() < 1e-12),
        "{route:?}",
    );
}

#[test]
fn point_objects_keep_their_direction() {
    let speakers = [
        "FL", "FR", "C", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR",
    ];
    let placement = StemPlacement::new(70.0, 0.0, 0.0, 0.0, 0.0);
    let [route, _] = upmixer_dsp_core::spatial::panner::object_routes(&placement, &speakers);
    assert!(route[3] > route[4]);
    assert!(route[3] > route[1]);
}

#[test]
fn point_objects_match_bs2127_reference_in_dolby_layouts() {
    let placement = StemPlacement::new(52.0, 23.0, 0.0, 0.0, 0.0);
    let cases: &[(&[&str], &[f64])] = &[
        (
            &["FL", "FR", "C", "SL", "SR"],
            &[
                0.8562937996270786,
                0.0,
                0.39415727909660503,
                0.3260336788387672,
                0.07143534362093623,
            ],
        ),
        (
            &["FL", "FR", "C", "SL", "SR", "BL", "BR"],
            &[
                0.7059949450262826,
                0.0,
                0.3249737960367183,
                0.6146747566644035,
                0.13467781185621472,
                0.0,
                0.0,
            ],
        ),
        (
            &["FL", "FR", "C", "SL", "SR", "BL", "BR", "TFL", "TFR"],
            &[
                0.5771429681618441,
                0.0,
                0.2656624421191444,
                0.5024897359601441,
                0.11009760427866028,
                0.0,
                0.0,
                0.5625985418130784,
                0.12326769522154092,
            ],
        ),
    ];

    for (speakers, expected) in cases {
        let [route, _] = upmixer_dsp_core::spatial::panner::object_routes(&placement, speakers);
        assert!(
            route
                .iter()
                .zip(*expected)
                .all(|(actual, expected)| (actual - expected).abs() < 1e-12),
            "{speakers:?}: {route:?}",
        );
    }
}

#[test]
fn placement_projection_matches_python() {
    let channels = [
        "FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR",
    ];
    let placement =
        resolve_placement("balanced", "Lead Vocals", &channels).expect("balanced lead vocal");
    assert_eq!(placement.object_size, 0.1);
}

#[test]
fn full_size_activates_every_positional_speaker() {
    let speakers = [
        "FL", "FR", "C", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR",
    ];
    let placement = StemPlacement::new(0.0, 0.0, 0.0, 1.0, 0.0);
    let [route, _] = upmixer_dsp_core::spatial::panner::object_routes(&placement, &speakers);
    assert!(route.into_iter().all(|gain| gain > 0.0));
}

#[test]
fn dolby_channel_lock_and_zone_exclusion_follow_bs2127() {
    let speakers = ["FL", "FR", "C", "SL", "SR", "BL", "BR", "TFL", "TFR"];
    let placement = StemPlacement::new(10.0, 0.0, 0.0, 0.0, 0.0);
    let [locked, _] = object_routes_with_metadata(&placement, &speakers, true, &[]);
    assert_eq!(locked.iter().filter(|gain| **gain > 0.0).count(), 1);

    let [excluded, _] = object_routes_with_metadata(&placement, &speakers, false, &["ZM5"]);
    assert!(excluded[..3].iter().all(|gain| *gain == 0.0));
    assert!((excluded.iter().map(|gain| gain * gain).sum::<f64>() - 1.0).abs() < 1e-12);

    let top_side = StemPlacement::new(90.0, 45.0, 0.0, 0.0, 0.0);
    let [locked, _] = object_routes_with_metadata(&top_side, &speakers, true, &[]);
    assert_eq!(locked[7], 1.0);
}
