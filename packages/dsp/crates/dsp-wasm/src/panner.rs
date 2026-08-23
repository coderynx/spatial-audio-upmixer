//! MDAP stem-placement panning, for the preview's mix editor.
//!
//! Control-rate only: the host calls this when a placement changes, not per
//! render quantum. Channels cross as indices into [`CHANNELS`] so the C ABI
//! never marshals strings inbound, and the host reads that table back out
//! rather than keeping a second copy of it.

use upmixer_dsp_core::spatial::panner::{self, StemPlacement};
use upmixer_dsp_core::spatial::presets;

/// Channel names by index, the vocabulary the host addresses channels with.
const CHANNELS: [&str; 12] = [
    "FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR",
];

/// # Safety
/// `channels` must address `n_channels` readable u32 values.
unsafe fn channel_names(channels: *const u32, n_channels: usize) -> Option<Vec<&'static str>> {
    if channels.is_null() {
        return None;
    }
    std::slice::from_raw_parts(channels, n_channels)
        .iter()
        .map(|index| CHANNELS.get(*index as usize).copied())
        .collect()
}

#[no_mangle]
pub extern "C" fn dsp_panner_channel_count() -> usize {
    CHANNELS.len()
}

#[no_mangle]
pub extern "C" fn dsp_panner_channel_len(index: usize) -> usize {
    CHANNELS.get(index).map_or(0, |name| name.len())
}

#[no_mangle]
pub extern "C" fn dsp_panner_channel_ptr(index: usize) -> *const u8 {
    CHANNELS.get(index).map_or(std::ptr::null(), |name| name.as_ptr())
}

#[no_mangle]
pub extern "C" fn dsp_preset_count() -> usize {
    presets::PRESET_NAMES.len()
}

#[no_mangle]
pub extern "C" fn dsp_preset_name_len(preset: usize) -> usize {
    presets::PRESET_NAMES.get(preset).map_or(0, |name| name.len())
}

#[no_mangle]
pub extern "C" fn dsp_preset_name_ptr(preset: usize) -> *const u8 {
    presets::PRESET_NAMES.get(preset).map_or(std::ptr::null(), |name| name.as_ptr())
}

#[no_mangle]
pub extern "C" fn dsp_preset_stem_count(preset: usize) -> usize {
    presets::PRESET_NAMES.get(preset).map_or(0, |name| presets::preset_stems(name).len())
}

fn preset_stem(preset: usize, stem: usize) -> Option<&'static (&'static str, StemPlacement)> {
    let name = presets::PRESET_NAMES.get(preset)?;
    presets::preset_stems(name).get(stem)
}

#[no_mangle]
pub extern "C" fn dsp_preset_stem_name_len(preset: usize, stem: usize) -> usize {
    preset_stem(preset, stem).map_or(0, |(name, _)| name.len())
}

#[no_mangle]
pub extern "C" fn dsp_preset_stem_name_ptr(preset: usize, stem: usize) -> *const u8 {
    preset_stem(preset, stem).map_or(std::ptr::null(), |(name, _)| name.as_ptr())
}

/// Write one preset placement as `[azimuth, elevation, width, spread, lfe]`.
/// Returns 0 on success, -1 when the preset or stem is unknown.
///
/// # Safety
/// `out` must address 5 writable f64 values.
#[no_mangle]
pub unsafe extern "C" fn dsp_preset_placement(
    preset: usize,
    stem: usize,
    out: *mut f64,
) -> i32 {
    let Some((_, placement)) = preset_stem(preset, stem) else { return -1 };
    if out.is_null() {
        return -1;
    }
    let fields = [
        placement.azimuth_deg,
        placement.elevation_deg,
        placement.width_deg,
        placement.spread_deg,
        placement.lfe,
    ];
    std::slice::from_raw_parts_mut(out, fields.len()).copy_from_slice(&fields);
    0
}

/// Pan a placement into `channels`, writing one gain per channel into `out`.
/// Returns 0 on success, -1 on a bad channel index or null pointer.
///
/// # Safety
/// `channels` must address `n_channels` readable u32 values and `out` the same
/// number of writable f64 values.
#[no_mangle]
pub unsafe extern "C" fn dsp_placement_route(
    azimuth_deg: f64,
    elevation_deg: f64,
    width_deg: f64,
    spread_deg: f64,
    lfe: f64,
    channels: *const u32,
    n_channels: usize,
    out: *mut f64,
) -> i32 {
    let Some(names) = channel_names(channels, n_channels) else { return -1 };
    if out.is_null() {
        return -1;
    }
    let placement =
        StemPlacement::new(azimuth_deg, elevation_deg, width_deg, spread_deg, lfe);
    let route = panner::placement_route(&placement, &names);
    std::slice::from_raw_parts_mut(out, n_channels).copy_from_slice(&route);
    0
}

/// Restate a placement as what `channels` can reproduce, writing the five
/// fields into `out`. Returns 0 on success, -1 on a bad channel index.
///
/// # Safety
/// `channels` must address `n_channels` readable u32 values; `out` must
/// address 5 writable f64 values.
#[no_mangle]
pub unsafe extern "C" fn dsp_project_placement(
    azimuth_deg: f64,
    elevation_deg: f64,
    width_deg: f64,
    spread_deg: f64,
    lfe: f64,
    channels: *const u32,
    n_channels: usize,
    out: *mut f64,
) -> i32 {
    let Some(names) = channel_names(channels, n_channels) else { return -1 };
    if out.is_null() {
        return -1;
    }
    let placement =
        StemPlacement::new(azimuth_deg, elevation_deg, width_deg, spread_deg, lfe);
    let projected = panner::project(&placement, &names);
    let fields = [
        projected.azimuth_deg,
        projected.elevation_deg,
        projected.width_deg,
        projected.spread_deg,
        projected.lfe,
    ];
    std::slice::from_raw_parts_mut(out, fields.len()).copy_from_slice(&fields);
    0
}

/// Collapse a speaker map onto FL/FR, writing `[left, right]` into `out`.
/// Returns 0 on success, -1 on a bad channel index or null pointer.
///
/// # Safety
/// `route` and `channels` must each address `n_channels` readable values;
/// `out` must address 2 writable f64 values.
#[no_mangle]
pub unsafe extern "C" fn dsp_fold_route_to_stereo(
    route: *const f64,
    channels: *const u32,
    n_channels: usize,
    out: *mut f64,
) -> i32 {
    let Some(names) = channel_names(channels, n_channels) else { return -1 };
    if route.is_null() || out.is_null() {
        return -1;
    }
    let gains = std::slice::from_raw_parts(route, n_channels);
    let (left, right) = panner::fold_route_to_stereo(gains, &names);
    std::slice::from_raw_parts_mut(out, 2).copy_from_slice(&[left, right]);
    0
}

/// The highest elevation `channels` can reproduce, or -1 on a bad index.
///
/// # Safety
/// `channels` must address `n_channels` readable u32 values.
#[no_mangle]
pub unsafe extern "C" fn dsp_panner_max_elevation(
    channels: *const u32,
    n_channels: usize,
) -> f64 {
    match channel_names(channels, n_channels) {
        Some(names) => panner::max_elevation_deg(&names),
        None => -1.0,
    }
}
