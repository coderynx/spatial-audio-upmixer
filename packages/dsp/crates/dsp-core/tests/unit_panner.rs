//! MDAP panner invariants: hull behaviour, symmetry, and constant power.

use upmixer_dsp_core::spatial::panner::{
    build_stem_routing, fold_route_to_stereo, has_height, object_routes, placement_route,
    placement_route_with_controls, project, PannerLayout, StemPlacement,
};

const FULL: [&str; 12] = [
    "FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR",
];
const BED_51: [&str; 6] = ["FL", "FR", "C", "LFE", "SL", "SR"];
const BED_712: [&str; 10] = ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR"];

fn point(azimuth: f64, elevation: f64) -> StemPlacement {
    StemPlacement::new(azimuth, elevation, 0.0, 0.0, 0.0)
}

fn gain(route: &[f64], channels: &[&str], channel: &str) -> f64 {
    let index = channels
        .iter()
        .position(|name| *name == channel)
        .expect("channel present");
    route[index]
}

fn power(route: &[f64], channels: &[&str]) -> f64 {
    route
        .iter()
        .zip(channels)
        .filter(|(_, channel)| **channel != "LFE")
        .map(|(gain, _)| gain * gain)
        .sum::<f64>()
        .sqrt()
}

#[test]
fn a_point_placement_lands_on_the_speaker_it_names() {
    let route = placement_route(&point(0.0, 0.0), &FULL);
    assert!(
        gain(&route, &FULL, "C") > 0.99,
        "front centre should be all C"
    );
    for channel in ["SL", "SR", "BL", "BR", "TBL", "TBR"] {
        assert_eq!(
            gain(&route, &FULL, channel),
            0.0,
            "{channel} should be silent"
        );
    }
}

#[test]
fn direct_speaker_positions_follow_the_selected_layout() {
    assert!(gain(&placement_route(&point(90.0, 0.0), &FULL), &FULL, "SL") > 0.99);
    assert!(
        gain(
            &placement_route(&point(90.0, 30.0), &BED_712),
            &BED_712,
            "TFL"
        ) > 0.99
    );
}

#[test]
fn panning_is_constant_power_across_a_rotation() {
    for step in 0..72 {
        let azimuth = step as f64 * 5.0;
        let route = placement_route(&point(azimuth, 0.0), &FULL);
        let power = power(&route, &FULL);
        assert!(
            (power - 1.0).abs() < 1e-9,
            "power at {azimuth}° was {power}, expected unity",
        );
    }
}

#[test]
fn bed_controls_spread_to_all_speakers_and_offset_center() {
    let placement = point(0.0, 0.0);
    let diverse = placement_route_with_controls(&placement, &FULL, 1.0, 0.0);
    let positional: Vec<f64> = diverse
        .iter()
        .zip(FULL)
        .filter_map(|(gain, channel)| (channel != "LFE").then_some(*gain))
        .collect();
    assert!(positional
        .windows(2)
        .all(|pair| (pair[0] - pair[1]).abs() < 1e-12));
    assert!((power(&diverse, &FULL) - 1.0).abs() < 1e-12);

    let quiet_center = placement_route_with_controls(&placement, &FULL, 0.0, -6.0);
    assert!((gain(&quiet_center, &FULL, "C") - 10.0_f64.powf(-6.0 / 20.0)).abs() < 1e-12);
    let muted_center = placement_route_with_controls(&placement, &FULL, 0.0, -83.0);
    assert_eq!(gain(&muted_center, &FULL, "C"), 0.0);
}

#[test]
fn mirrored_azimuths_mirror_the_speaker_gains() {
    let left = placement_route(&point(45.0, 10.0), &FULL);
    let right = placement_route(&point(-45.0, 10.0), &FULL);
    for (side, mirror) in [("FL", "FR"), ("SL", "SR"), ("BL", "BR"), ("TFL", "TFR")] {
        let a = gain(&left, &FULL, side);
        let b = gain(&right, &FULL, mirror);
        assert!(
            (a - b).abs() < 1e-12,
            "{side}/{mirror} broke mirror symmetry: {a} vs {b}"
        );
    }
}

#[test]
fn rotation_is_continuous_around_the_ring() {
    // No speaker may jump discontinuously as the image rotates: the coplanar
    // rear/height walls admit two triangulations and picking one by score
    // would step here.
    let mut previous = placement_route(&point(0.0, 0.0), &FULL);
    for step in 1..=360 {
        let route = placement_route(&point(step as f64, 0.0), &FULL);
        for (index, channel) in FULL.iter().enumerate() {
            let jump = (route[index] - previous[index]).abs();
            assert!(jump < 0.12, "{channel} jumped {jump} at {step}°");
        }
        previous = route;
    }
}

#[test]
fn a_rear_placement_pins_to_the_side_pair_on_a_bed_with_no_rears() {
    let route = placement_route(&point(180.0, 0.0), &BED_51);
    let left = gain(&route, &BED_51, "SL");
    let right = gain(&route, &BED_51, "SR");
    assert!(
        left > 0.4 && right > 0.4,
        "rear should project onto SL/SR, got {left}/{right}"
    );
    assert!(
        (left - right).abs() < 1e-12,
        "a centred rear should stay centred"
    );
    assert_eq!(gain(&route, &BED_51, "C"), 0.0, "rear must not leak into C");
}

#[test]
fn elevation_is_clamped_to_what_the_layout_spans() {
    let overhead = placement_route(&point(0.0, 90.0), &FULL);
    let ceiling = placement_route(&point(0.0, 34.9), &FULL);
    for index in 0..FULL.len() {
        assert!(
            (overhead[index] - ceiling[index]).abs() < 0.05,
            "{} differed past the height layer",
            FULL[index],
        );
    }
    let below = placement_route(&point(0.0, -45.0), &FULL);
    let flat = placement_route(&point(0.0, 0.0), &FULL);
    assert_eq!(below, flat, "elevation below the floor should clamp to it");
}

#[test]
fn a_flat_layout_spends_elevation_on_width() {
    let placement = StemPlacement::new(0.0, 20.0, 40.0, 30.0, 0.0);
    assert!(!has_height(&BED_51));
    let flattened = project(&placement, &BED_51);
    assert_eq!(flattened.elevation_deg, 0.0);
    assert_eq!(flattened.width_deg, 80.0);
    assert_eq!(
        project(&placement, &FULL),
        placement,
        "a height layout keeps it"
    );
}

#[test]
fn width_widens_the_image_instead_of_moving_it() {
    let narrow = placement_route(&StemPlacement::new(0.0, 0.0, 0.0, 40.0, 0.0), &FULL);
    let wide = placement_route(&StemPlacement::new(0.0, 0.0, 120.0, 40.0, 0.0), &FULL);
    let reach = |route: &[f64]| route.iter().filter(|gain| **gain > 0.0).count();
    assert!(
        reach(&wide) > reach(&narrow),
        "a wide image should touch more speakers"
    );
    assert!(
        gain(&wide, &FULL, "FL") > gain(&narrow, &FULL, "FL"),
        "widening should push energy into the front pair",
    );
}

#[test]
fn linked_object_endpoints_follow_width_and_co_locate_at_zero() {
    let point = StemPlacement::new(20.0, 15.0, 0.0, 30.0, 0.0);
    let [left, right] = object_routes(&point, &FULL);
    assert_eq!(left, right);

    let [left, right] = object_routes(
        &StemPlacement {
            width_deg: 80.0,
            ..point
        },
        &FULL,
    );
    assert_ne!(left, right);
    assert!(gain(&left, &FULL, "FL") > gain(&right, &FULL, "FL"));
}

#[test]
fn lead_vocals_are_stereo_wide_in_every_preset() {
    use upmixer_dsp_core::spatial::presets::{preset_placement, PRESET_NAMES};

    for preset in PRESET_NAMES {
        assert!(
            preset_placement(preset, "Lead Vocals").unwrap().width_deg >= 60.0,
            "{preset} narrows Lead Vocals"
        );
    }
}

#[test]
fn the_lfe_send_passes_through_untouched() {
    let placement = StemPlacement::new(0.0, 0.0, 60.0, 40.0, 0.75);
    let route = placement_route(&placement, &FULL);
    assert_eq!(gain(&route, &FULL, "LFE"), 0.75);
    let no_lfe = placement_route(
        &StemPlacement {
            lfe: 0.0,
            ..placement
        },
        &FULL,
    );
    assert_eq!(gain(&no_lfe, &FULL, "LFE"), 0.0);
    assert!(
        (power(&route, &FULL) - power(&no_lfe, &FULL)).abs() < 1e-12,
        "the LFE send must not disturb the speaker map's power",
    );
}

#[test]
fn the_stereo_fold_keeps_a_pan_as_a_ratio() {
    let route = placement_route(&point(45.0, 0.0), &FULL);
    let (left, right) = fold_route_to_stereo(&route, &FULL);
    assert!(left > right, "a left placement should fold left-dominant");
    let (mirror_left, mirror_right) =
        fold_route_to_stereo(&placement_route(&point(-45.0, 0.0), &FULL), &FULL);
    assert!((left - mirror_right).abs() < 1e-12);
    assert!((right - mirror_left).abs() < 1e-12);
}

#[test]
fn stereo_routing_reaches_only_the_front_pair() {
    let stems = ["Vocals", "Guitar"];
    let channels = ["FL", "FR"];
    let routing = build_stem_routing(&stems, &channels, "balanced");
    assert_eq!(routing.len(), 2);
    for (stem, route) in routing {
        assert_eq!(route.len(), 2, "{stem} should fold to two channels");
        assert!(
            route.iter().all(|gain| *gain > 0.0),
            "{stem} lost a side in the fold"
        );
    }
}

#[test]
fn an_unknown_preset_or_stem_routes_nothing() {
    assert!(build_stem_routing(&["Vocals"], &FULL, "no-such-preset").is_empty());
    assert!(build_stem_routing(&["No Such Stem"], &FULL, "balanced").is_empty());
}

#[test]
fn panning_is_deterministic() {
    let placement = StemPlacement::new(33.0, 12.0, 70.0, 55.0, 0.2);
    let first = placement_route(&placement, &FULL);
    for _ in 0..8 {
        assert_eq!(placement_route(&placement, &FULL), first);
    }
}

#[test]
fn cached_layout_matches_the_one_shot_panner() {
    let placement = StemPlacement::new(33.0, 12.0, 70.0, 55.0, 0.2);
    let cached = PannerLayout::new(&FULL);
    assert_eq!(
        cached.placement_route(&placement),
        placement_route(&placement, &FULL)
    );
    assert_eq!(
        cached.object_routes(&placement),
        object_routes(&placement, &FULL)
    );
}

#[test]
fn preset_ambient_keeps_the_pulse_dry_and_scales_the_room_per_preset() {
    use upmixer_dsp_core::spatial::presets::{
        preset_ambient, preset_ambient_height_crossover, preset_stems, PRESET_NAMES,
    };

    for preset in PRESET_NAMES {
        for (stem, _) in preset_stems(preset) {
            let (rear, height) = preset_ambient(preset, stem).unwrap();
            assert!((0.0..=0.9).contains(&rear), "{preset}/{stem} rear {rear}");
            assert!(
                (0.0..=0.9).contains(&height),
                "{preset}/{stem} height {height}"
            );
            let crossover = preset_ambient_height_crossover(preset, stem).unwrap();
            assert!(
                [500.0, 2000.0, 4000.0].contains(&crossover),
                "{preset}/{stem}"
            );
            if matches!(*stem, "Lead Vocals" | "Kick" | "Snare" | "Bass") {
                assert_eq!((rear, height), (0.0, 0.0), "{preset}/{stem}");
            }
        }
    }
    let intimate = preset_ambient("intimate", "Crowd").unwrap();
    let live = preset_ambient("live", "Crowd").unwrap();
    assert!(live.0 > intimate.0 && live.1 > intimate.1);
    assert!(preset_ambient("immersive", "Crowd").unwrap().1 > live.1);
    assert!(preset_ambient("balanced", "nope").is_none());
    assert!(preset_ambient("nope", "Crowd").is_none());
    assert_eq!(
        preset_ambient_height_crossover("balanced", "Vocals Reverb"),
        Some(500.0)
    );
    assert_eq!(
        preset_ambient_height_crossover("balanced", "Vocals"),
        Some(4000.0)
    );
    assert_eq!(
        preset_ambient_height_crossover("balanced", "Guitar"),
        Some(2000.0)
    );
    assert_eq!(preset_ambient_height_crossover("nope", "Crowd"), None);
}

#[test]
fn preset_object_sizes_are_normalized_and_expand_with_scope() {
    use upmixer_dsp_core::spatial::presets::{preset_placement, preset_stems, PRESET_NAMES};

    for preset in PRESET_NAMES {
        for (_, placement) in preset_stems(preset) {
            assert!((0.0..=1.0).contains(&placement.object_size));
        }
    }

    assert!(
        preset_placement("intimate", "Crowd").unwrap().object_size
            < preset_placement("balanced", "Crowd").unwrap().object_size
    );
    assert!(
        preset_placement("balanced", "Crowd").unwrap().object_size
            < preset_placement("wide", "Crowd").unwrap().object_size
    );
}
