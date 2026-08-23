//! MDAP stem panning parity against the Python original.

mod common;

use common::Case;
use upmixer_dsp_core::spatial::panner::{
    build_stem_routing, placement_route, resolve_placement, StemPlacement,
};

/// `params.channels` maps a layout name to its channel order.
fn layout_channels(case: &Case, layout: &str) -> Vec<String> {
    case.meta["params"]["channels"][layout]
        .as_array()
        .unwrap_or_else(|| panic!("no channel list for layout {layout}"))
        .iter()
        .map(|name| name.as_str().expect("channel name").to_string())
        .collect()
}

fn as_str_list(value: &serde_json::Value) -> Vec<String> {
    value
        .as_array()
        .expect("string list")
        .iter()
        .map(|item| item.as_str().expect("string").to_string())
        .collect()
}

#[test]
fn preset_routing_matches_python() {
    let case = Case::load("panner_routing");
    let stems = as_str_list(&case.meta["params"]["stems"]);
    let stem_refs: Vec<&str> = stems.iter().map(String::as_str).collect();

    let mut got = Vec::new();
    let mut previous = (String::new(), String::new());
    let mut routing: Vec<(String, Vec<f64>)> = Vec::new();
    for entry in case.meta["params"]["cases"].as_array().expect("case list") {
        let fields = as_str_list(entry);
        let (preset, layout, stem) = (&fields[0], &fields[1], &fields[2]);
        if previous != (preset.clone(), layout.clone()) {
            let channels = layout_channels(&case, layout);
            let refs: Vec<&str> = channels.iter().map(String::as_str).collect();
            routing = build_stem_routing(&stem_refs, &refs, preset);
            previous = (preset.clone(), layout.clone());
        }
        let route = routing
            .iter()
            .find(|(name, _)| name == stem)
            .map(|(_, gains)| gains.clone())
            .unwrap_or_else(|| vec![0.0; layout_channels(&case, layout).len()]);
        got.extend(route);
    }
    case.assert_close(&got, &case.array("gains"), "preset routing gains");
}

#[test]
fn placement_projection_matches_python() {
    let case = Case::load("panner_projection");
    let routing = Case::load("panner_routing");

    let mut got = Vec::new();
    for entry in case.meta["params"]["cases"].as_array().expect("case list") {
        let fields = as_str_list(entry);
        let (preset, layout, stem) = (&fields[0], &fields[1], &fields[2]);
        // Stereo resolves against the full layout, same as the panner does.
        let channels = layout_channels(&routing, if layout == "stereo" { "7.1.4" } else { layout });
        let refs: Vec<&str> = channels.iter().map(String::as_str).collect();
        let placement = resolve_placement(preset, stem, &refs)
            .unwrap_or_else(|| panic!("{preset}/{stem} missing from the preset table"));
        got.extend([
            placement.azimuth_deg,
            placement.elevation_deg,
            placement.width_deg,
            placement.spread_deg,
            placement.lfe,
        ]);
    }
    case.assert_close(&got, &case.array("placements"), "projected placements");
}

#[test]
fn hull_edges_and_clamps_match_python() {
    let case = Case::load("panner_edges");

    let mut got = Vec::new();
    for entry in case.meta["params"]["cases"].as_array().expect("case list") {
        let fields = entry.as_array().expect("case entry");
        let layout = fields[0].as_str().expect("layout name");
        let value = |index: usize| fields[index].as_f64().expect("placement field");
        let placement =
            StemPlacement::new(value(1), value(2), value(3), value(4), value(5));
        let channels = layout_channels(&case, layout);
        let refs: Vec<&str> = channels.iter().map(String::as_str).collect();
        got.extend(placement_route(&placement, &refs));
    }
    case.assert_close(&got, &case.array("gains"), "edge placement gains");
}
