//! MDAP stem-placement panning exports.
//!
//! Speaker maps cross the boundary as ordered gain lists aligned with the
//! channel names the caller passed in; `packages/core` turns them back into
//! the sparse channel dictionaries its manifests use.

use pyo3::prelude::*;

use upmixer_dsp_core::spatial::panner::{self, StemPlacement};
use upmixer_dsp_core::spatial::presets;

type PlacementTuple = (f64, f64, f64, f64, f64);
type PresetPlacementTuple = (f64, f64, f64, f64, f64, f64, f64);
type PresetTreatmentTuple = (f64, f64, f64, f64, f64, f64, f64, f64, f64, f64);

fn placement(values: PlacementTuple) -> StemPlacement {
    StemPlacement::new(values.0, values.1, values.2, values.3, values.4)
}

fn unpack(value: &StemPlacement) -> PlacementTuple {
    (
        value.azimuth_deg,
        value.elevation_deg,
        value.width_deg,
        value.object_size,
        value.lfe,
    )
}

fn unpack_preset(value: &StemPlacement) -> PresetPlacementTuple {
    (
        value.azimuth_deg,
        value.elevation_deg,
        value.width_deg,
        value.object_size,
        value.lfe,
        value.diversity,
        value.center_level_db,
    )
}

fn as_refs(names: &[String]) -> Vec<&str> {
    names.iter().map(String::as_str).collect()
}

#[pyfunction]
fn direction(azimuth_deg: f64, elevation_deg: f64) -> (f64, f64, f64) {
    let vector = panner::direction(azimuth_deg, elevation_deg);
    (vector[0], vector[1], vector[2])
}

#[pyfunction]
fn panning_gains(
    azimuth_deg: f64,
    elevation_deg: f64,
    width_deg: f64,
    object_size: f64,
    speakers: Vec<String>,
) -> Vec<f64> {
    let value = placement((azimuth_deg, elevation_deg, width_deg, object_size, 0.0));
    panner::panning_gains(&value, &as_refs(&speakers))
}

#[pyfunction(signature = (
    azimuth_deg, elevation_deg, width_deg, object_size, lfe, channels,
    diversity = 0.0, center_level_db = 0.0
))]
fn placement_route(
    azimuth_deg: f64,
    elevation_deg: f64,
    width_deg: f64,
    object_size: f64,
    lfe: f64,
    channels: Vec<String>,
    diversity: f64,
    center_level_db: f64,
) -> Vec<f64> {
    let value = StemPlacement::new(azimuth_deg, elevation_deg, width_deg, object_size, lfe)
        .with_bed_controls(diversity, center_level_db);
    panner::placement_route(&value, &as_refs(&channels))
}

#[pyfunction]
fn object_routes(
    azimuth_deg: f64,
    elevation_deg: f64,
    width_deg: f64,
    object_size: f64,
    channels: Vec<String>,
) -> (Vec<f64>, Vec<f64>) {
    let value = placement((azimuth_deg, elevation_deg, width_deg, object_size, 0.0));
    let [left, right] = panner::object_routes(&value, &as_refs(&channels));
    (left, right)
}

#[pyfunction]
fn adm_object_routes(
    azimuth_deg: f64,
    elevation_deg: f64,
    width_deg: f64,
    object_size: f64,
    channel_lock: bool,
    zone_exclusion: Vec<String>,
    channels: Vec<String>,
) -> (Vec<f64>, Vec<f64>) {
    let value = placement((azimuth_deg, elevation_deg, width_deg, object_size, 0.0));
    let zones = as_refs(&zone_exclusion);
    let [left, right] =
        panner::object_routes_with_metadata(&value, &as_refs(&channels), channel_lock, &zones);
    (left, right)
}

#[pyfunction]
fn project_placement(
    azimuth_deg: f64,
    elevation_deg: f64,
    width_deg: f64,
    object_size: f64,
    lfe: f64,
    channels: Vec<String>,
) -> PlacementTuple {
    let value = placement((azimuth_deg, elevation_deg, width_deg, object_size, lfe));
    unpack(&panner::project(&value, &as_refs(&channels)))
}

#[pyfunction]
fn build_stem_routing(
    stems: Vec<String>,
    channels: Vec<String>,
    preset: &str,
) -> Vec<(String, Vec<f64>)> {
    panner::build_stem_routing(&as_refs(&stems), &as_refs(&channels), preset)
}

#[pyfunction]
fn fold_route_to_stereo(route: Vec<f64>, channels: Vec<String>) -> (f64, f64) {
    panner::fold_route_to_stereo(&route, &as_refs(&channels))
}

#[pyfunction]
fn preset_names() -> Vec<&'static str> {
    presets::PRESET_NAMES.to_vec()
}

#[pyfunction]
fn preset_treatments(preset: &str) -> Vec<(String, PresetTreatmentTuple)> {
    presets::preset_stems(preset)
        .iter()
        .filter_map(|(stem, _)| {
            let treatment = presets::preset_treatment(preset, stem)?;
            let placement = unpack_preset(&treatment.placement);
            Some((
                stem.to_string(),
                (
                    placement.0,
                    placement.1,
                    placement.2,
                    placement.3,
                    placement.4,
                    placement.5,
                    placement.6,
                    treatment.ambient_rear,
                    treatment.ambient_height,
                    treatment.ambient_height_crossover_hz,
                ),
            ))
        })
        .collect()
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(direction, m)?)?;
    m.add_function(wrap_pyfunction!(panning_gains, m)?)?;
    m.add_function(wrap_pyfunction!(placement_route, m)?)?;
    m.add_function(wrap_pyfunction!(object_routes, m)?)?;
    m.add_function(wrap_pyfunction!(adm_object_routes, m)?)?;
    m.add_function(wrap_pyfunction!(project_placement, m)?)?;
    m.add_function(wrap_pyfunction!(build_stem_routing, m)?)?;
    m.add_function(wrap_pyfunction!(fold_route_to_stereo, m)?)?;
    m.add_function(wrap_pyfunction!(preset_names, m)?)?;
    m.add_function(wrap_pyfunction!(preset_treatments, m)?)?;
    m.add("VIRTUAL_SOURCE_STEP_DEG", panner::VIRTUAL_SOURCE_STEP_DEG)?;
    m.add("MINIMUM_SEND", panner::MINIMUM_SEND)?;
    m.add(
        "HEIGHT_FLATTEN_WIDTH_FACTOR",
        panner::HEIGHT_FLATTEN_WIDTH_FACTOR,
    )?;
    m.add(
        "STEREO_PLACEMENT_CHANNELS",
        panner::STEREO_PLACEMENT_CHANNELS,
    )?;
    Ok(())
}
