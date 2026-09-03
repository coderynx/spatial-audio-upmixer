//! Stem-routing presets as canonical placements.
//!
//! A preset holds one placement per stem — where the stem sits in the
//! listener's sphere, how wide its image is, and how much of it goes to
//! LFE. The realization is layout-dependent: `panner::resolve_placement`
//! projects a placement onto what a speaker layout can actually
//! reproduce, and `panner::placement_route` pans it into speaker gains.
//!
//! Placement guidance (Dolby Atmos music practice, matching the routing
//! philosophy in `separation/stem_router.py`): lead vocal, kick, snare
//! and bass stay on the front wall in every preset; backing vocals,
//! cymbals, pads and room content are what moves to the sides and
//! heights; sub weight goes to LFE rather than into the spatial field.

use super::panner::StemPlacement;

const fn placement(
    azimuth_deg: f64,
    elevation_deg: f64,
    width_deg: f64,
    object_size: f64,
    lfe: f64,
    diversity: f64,
    center_level_db: f64,
) -> StemPlacement {
    StemPlacement::new(azimuth_deg, elevation_deg, width_deg, object_size, lfe)
        .with_bed_controls(diversity, center_level_db)
}

/// Preset names, in the order they are offered.
pub const PRESET_NAMES: [&str; 6] = ["balanced", "intimate", "stage", "wide", "immersive", "live"];

/// Everything a preset decides for one stem before layout realization.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PresetTreatment {
    pub placement: StemPlacement,
    pub ambient_rear: f64,
    pub ambient_height: f64,
    pub ambient_height_crossover_hz: f64,
}

const BALANCED_PLACEMENTS: [(&str, StemPlacement); 16] = [
    (
        "Lead Vocals",
        placement(0.0, 0.0, 60.0, 0.10, 0.0, 0.00, 1.5),
    ),
    ("Vocals", placement(0.0, 2.0, 26.0, 0.12, 0.0, 0.00, 0.5)),
    (
        "Backing Vocals",
        placement(0.0, 14.0, 88.0, 0.22, 0.0, 0.16, -0.5),
    ),
    ("Bass", placement(0.0, 0.0, 60.0, 0.06, 0.72, 0.00, 0.5)),
    ("Kick", placement(0.0, 0.0, 52.0, 0.05, 0.82, 0.00, 1.0)),
    ("Snare", placement(0.0, 0.0, 54.0, 0.08, 0.0, 0.00, 0.8)),
    ("Toms", placement(0.0, 0.0, 100.0, 0.16, 0.18, 0.12, -0.5)),
    ("Drums", placement(0.0, 0.0, 92.0, 0.18, 0.28, 0.14, -0.8)),
    ("Hi-Hat", placement(0.0, 16.0, 76.0, 0.12, 0.0, 0.00, 0.0)),
    ("Ride", placement(0.0, 20.0, 84.0, 0.14, 0.0, 0.14, -1.5)),
    ("Crash", placement(0.0, 22.0, 96.0, 0.18, 0.0, 0.18, -2.0)),
    ("Guitar", placement(0.0, 2.0, 124.0, 0.20, 0.0, 0.14, -0.5)),
    ("Piano", placement(0.0, 4.0, 106.0, 0.18, 0.0, 0.10, 0.0)),
    ("Other", placement(0.0, 8.0, 116.0, 0.22, 0.15, 0.05, 0.0)),
    (
        "Instrumental",
        placement(0.0, 8.0, 108.0, 0.20, 0.38, 0.16, -0.5),
    ),
    (
        "Crowd",
        placement(180.0, 14.0, 128.0, 0.40, 0.0, 0.50, -3.0),
    ),
];

const INTIMATE_PLACEMENTS: [(&str, StemPlacement); 16] = [
    (
        "Lead Vocals",
        placement(0.0, 0.0, 60.0, 0.05, 0.0, 0.00, 2.0),
    ),
    ("Vocals", placement(0.0, 0.0, 16.0, 0.08, 0.0, 0.00, 1.0)),
    (
        "Backing Vocals",
        placement(0.0, 4.0, 48.0, 0.10, 0.0, 0.06, 0.0),
    ),
    ("Bass", placement(0.0, 0.0, 46.0, 0.04, 0.68, 0.00, 1.0)),
    ("Kick", placement(0.0, 0.0, 28.0, 0.03, 0.78, 0.00, 1.5)),
    ("Snare", placement(0.0, 0.0, 30.0, 0.06, 0.0, 0.00, 1.0)),
    ("Toms", placement(0.0, 0.0, 42.0, 0.07, 0.16, 0.04, 0.0)),
    ("Drums", placement(0.0, 0.0, 40.0, 0.09, 0.25, 0.05, -0.2)),
    ("Hi-Hat", placement(0.0, 6.0, 46.0, 0.07, 0.0, 0.06, -1.0)),
    ("Ride", placement(0.0, 8.0, 48.0, 0.07, 0.0, 0.07, -1.0)),
    ("Crash", placement(0.0, 8.0, 54.0, 0.09, 0.0, 0.08, -1.5)),
    ("Guitar", placement(0.0, 0.0, 56.0, 0.10, 0.0, 0.06, 0.0)),
    ("Piano", placement(0.0, 0.0, 52.0, 0.09, 0.0, 0.05, 0.2)),
    ("Other", placement(0.0, 4.0, 68.0, 0.12, 0.10, 0.10, -0.5)),
    (
        "Instrumental",
        placement(0.0, 4.0, 60.0, 0.14, 0.32, 0.08, 0.0),
    ),
    ("Crowd", placement(180.0, 8.0, 92.0, 0.26, 0.0, 0.30, -2.0)),
];

const STAGE_PLACEMENTS: [(&str, StemPlacement); 16] = [
    (
        "Lead Vocals",
        placement(0.0, 0.0, 60.0, 0.08, 0.0, 0.00, 1.5),
    ),
    ("Vocals", placement(0.0, 2.0, 24.0, 0.11, 0.0, 0.00, 0.7)),
    (
        "Backing Vocals",
        placement(0.0, 14.0, 92.0, 0.20, 0.0, 0.14, -0.5),
    ),
    ("Bass", placement(0.0, 0.0, 60.0, 0.06, 0.72, 0.00, 0.8)),
    ("Kick", placement(0.0, 0.0, 54.0, 0.05, 0.82, 0.00, 1.2)),
    ("Snare", placement(0.0, 0.0, 52.0, 0.07, 0.0, 0.00, 1.0)),
    ("Toms", placement(-20.0, 0.0, 50.0, 0.13, 0.18, 0.08, -0.5)),
    ("Drums", placement(0.0, 0.0, 90.0, 0.16, 0.28, 0.12, -0.6)),
    ("Hi-Hat", placement(34.0, 16.0, 34.0, 0.09, 0.0, 0.00, -1.5)),
    ("Ride", placement(-38.0, 18.0, 34.0, 0.09, 0.0, 0.11, -1.5)),
    ("Crash", placement(0.0, 26.0, 92.0, 0.16, 0.0, 0.16, -2.0)),
    ("Guitar", placement(50.0, 2.0, 50.0, 0.16, 0.0, 0.10, -0.5)),
    ("Piano", placement(-50.0, 4.0, 50.0, 0.16, 0.0, 0.08, 0.0)),
    ("Other", placement(0.0, 12.0, 108.0, 0.20, 0.15, 0.16, -1.0)),
    (
        "Instrumental",
        placement(0.0, 8.0, 108.0, 0.18, 0.38, 0.14, -0.5),
    ),
    (
        "Crowd",
        placement(180.0, 14.0, 128.0, 0.36, 0.0, 0.46, -3.0),
    ),
];

const WIDE_PLACEMENTS: [(&str, StemPlacement); 16] = [
    (
        "Lead Vocals",
        placement(0.0, 0.0, 62.0, 0.12, 0.0, 0.00, 1.2),
    ),
    ("Vocals", placement(0.0, 2.0, 44.0, 0.20, 0.0, 0.00, 0.3)),
    (
        "Backing Vocals",
        placement(0.0, 22.0, 120.0, 0.28, 0.0, 0.26, -0.8),
    ),
    ("Bass", placement(0.0, 0.0, 76.0, 0.08, 0.72, 0.00, 0.2)),
    ("Kick", placement(0.0, 0.0, 56.0, 0.06, 0.82, 0.00, 0.8)),
    ("Snare", placement(0.0, 0.0, 50.0, 0.09, 0.0, 0.00, 0.5)),
    ("Toms", placement(0.0, 2.0, 92.0, 0.18, 0.18, 0.20, -0.8)),
    ("Drums", placement(0.0, 2.0, 82.0, 0.20, 0.28, 0.22, -1.0)),
    ("Hi-Hat", placement(0.0, 22.0, 100.0, 0.18, 0.0, 0.00, -1.8)),
    ("Ride", placement(0.0, 26.0, 104.0, 0.18, 0.0, 0.26, -1.8)),
    ("Crash", placement(0.0, 30.0, 120.0, 0.22, 0.0, 0.30, -2.2)),
    ("Guitar", placement(0.0, 6.0, 120.0, 0.26, 0.0, 0.24, -0.8)),
    ("Piano", placement(0.0, 10.0, 110.0, 0.24, 0.0, 0.20, -0.3)),
    ("Other", placement(0.0, 20.0, 136.0, 0.30, 0.15, 0.32, -1.2)),
    (
        "Instrumental",
        placement(0.0, 12.0, 122.0, 0.26, 0.38, 0.26, -0.8),
    ),
    (
        "Crowd",
        placement(180.0, 18.0, 140.0, 0.46, 0.0, 0.62, -3.5),
    ),
];

const IMMERSIVE_PLACEMENTS: [(&str, StemPlacement); 16] = [
    (
        "Lead Vocals",
        placement(0.0, 0.0, 60.0, 0.10, 0.0, 0.00, 1.0),
    ),
    ("Vocals", placement(0.0, 4.0, 28.0, 0.14, 0.0, 0.00, 0.3)),
    (
        "Backing Vocals",
        placement(0.0, 30.0, 112.0, 0.28, 0.0, 0.30, -1.0),
    ),
    ("Bass", placement(0.0, 0.0, 62.0, 0.07, 0.72, 0.00, 0.4)),
    ("Kick", placement(0.0, 0.0, 54.0, 0.05, 0.82, 0.00, 0.9)),
    ("Snare", placement(0.0, 0.0, 54.0, 0.08, 0.0, 0.00, 0.7)),
    ("Toms", placement(0.0, 10.0, 78.0, 0.16, 0.18, 0.18, -0.7)),
    ("Drums", placement(0.0, 8.0, 72.0, 0.18, 0.28, 0.20, -0.9)),
    ("Hi-Hat", placement(0.0, 32.0, 96.0, 0.22, 0.0, 0.00, -2.0)),
    ("Ride", placement(0.0, 34.0, 100.0, 0.22, 0.0, 0.32, -2.0)),
    ("Crash", placement(0.0, 36.0, 116.0, 0.26, 0.0, 0.36, -2.5)),
    ("Guitar", placement(0.0, 16.0, 100.0, 0.20, 0.0, 0.22, -0.8)),
    ("Piano", placement(0.0, 20.0, 94.0, 0.20, 0.0, 0.22, -0.5)),
    ("Other", placement(0.0, 30.0, 130.0, 0.30, 0.15, 0.38, -1.5)),
    (
        "Instrumental",
        placement(0.0, 20.0, 108.0, 0.22, 0.38, 0.28, -1.0),
    ),
    (
        "Crowd",
        placement(180.0, 30.0, 142.0, 0.48, 0.0, 0.68, -4.0),
    ),
];

const LIVE_PLACEMENTS: [(&str, StemPlacement); 16] = [
    (
        "Lead Vocals",
        placement(0.0, 0.0, 60.0, 0.12, 0.0, 0.00, 1.0),
    ),
    ("Vocals", placement(0.0, 2.0, 34.0, 0.18, 0.0, 0.00, 0.2)),
    (
        "Backing Vocals",
        placement(0.0, 20.0, 110.0, 0.24, 0.0, 0.24, -0.8),
    ),
    ("Bass", placement(0.0, 0.0, 64.0, 0.08, 0.72, 0.00, 0.5)),
    ("Kick", placement(0.0, 0.0, 56.0, 0.05, 0.82, 0.00, 1.0)),
    ("Snare", placement(0.0, 0.0, 54.0, 0.08, 0.0, 0.00, 0.8)),
    ("Toms", placement(0.0, 2.0, 90.0, 0.18, 0.18, 0.18, -0.6)),
    ("Drums", placement(0.0, 6.0, 96.0, 0.20, 0.28, 0.22, -1.0)),
    ("Hi-Hat", placement(0.0, 20.0, 92.0, 0.18, 0.0, 0.00, -1.8)),
    ("Ride", placement(0.0, 24.0, 96.0, 0.18, 0.0, 0.24, -1.8)),
    ("Crash", placement(0.0, 28.0, 110.0, 0.22, 0.0, 0.28, -2.3)),
    ("Guitar", placement(0.0, 4.0, 112.0, 0.22, 0.0, 0.22, -0.8)),
    ("Piano", placement(0.0, 8.0, 102.0, 0.20, 0.0, 0.18, -0.3)),
    ("Other", placement(0.0, 16.0, 142.0, 0.32, 0.15, 0.34, -1.5)),
    (
        "Instrumental",
        placement(0.0, 10.0, 124.0, 0.24, 0.38, 0.26, -1.0),
    ),
    (
        "Crowd",
        placement(180.0, 24.0, 158.0, 0.54, 0.0, 0.72, -4.5),
    ),
];

fn preset_table(preset: &str) -> Option<&'static [(&'static str, StemPlacement)]> {
    match preset {
        "balanced" => Some(&BALANCED_PLACEMENTS),
        "intimate" => Some(&INTIMATE_PLACEMENTS),
        "stage" => Some(&STAGE_PLACEMENTS),
        "wide" => Some(&WIDE_PLACEMENTS),
        "immersive" => Some(&IMMERSIVE_PLACEMENTS),
        "live" => Some(&LIVE_PLACEMENTS),
        _ => None,
    }
}

/// The complete treatment a preset gives one stem. `None` when the preset or
/// stem is unknown.
pub fn preset_treatment(preset: &str, stem: &str) -> Option<PresetTreatment> {
    let table = preset_table(preset)?;
    let placement = table
        .iter()
        .find(|(name, _)| *name == stem)
        .map(|(_, placement)| *placement)?;
    let (_, rear_scale, height_scale) =
        *AMBIENT_SCALE.iter().find(|(name, _, _)| *name == preset)?;
    let (_, rear, height, crossover) = *AMBIENT_DEFAULTS
        .iter()
        .find(|(name, _, _, _)| *name == stem)?;
    Some(PresetTreatment {
        placement,
        ambient_rear: (rear * rear_scale).min(AMBIENT_MAX),
        ambient_height: (height * height_scale).min(AMBIENT_MAX),
        ambient_height_crossover_hz: crossover,
    })
}

/// The canonical placement a preset gives one stem, before any layout is
/// applied. `None` when the preset or the stem is unknown.
pub fn preset_placement(preset: &str, stem: &str) -> Option<StemPlacement> {
    preset_treatment(preset, stem).map(|treatment| treatment.placement)
}

/// Every stem a preset names, in table order.
pub fn preset_stems(preset: &str) -> &'static [(&'static str, StemPlacement)] {
    preset_table(preset).unwrap_or(&[])
}

/// How readily a stem's ambient half leaves the front wall at `balanced`'s
/// scale, followed by its height crossover in Hz.
const AMBIENT_DEFAULTS: [(&str, f64, f64, f64); 16] = [
    ("Lead Vocals", 0.0, 0.0, 4000.0),
    ("Vocals", 0.06, 0.04, 4000.0),
    ("Backing Vocals", 0.20, 0.16, 4000.0),
    ("Bass", 0.0, 0.0, 4000.0),
    ("Kick", 0.0, 0.0, 4000.0),
    ("Snare", 0.0, 0.0, 4000.0),
    ("Toms", 0.08, 0.05, 4000.0),
    ("Drums", 0.10, 0.06, 4000.0),
    ("Hi-Hat", 0.10, 0.10, 2000.0),
    ("Ride", 0.12, 0.12, 2000.0),
    ("Crash", 0.16, 0.16, 2000.0),
    ("Guitar", 0.14, 0.08, 2000.0),
    ("Piano", 0.14, 0.10, 2000.0),
    ("Other", 0.22, 0.18, 2000.0),
    ("Instrumental", 0.16, 0.12, 2000.0),
    ("Crowd", 0.55, 0.35, 2000.0),
];

/// How much each preset leans on the room, against the ambient defaults.
const AMBIENT_SCALE: [(&str, f64, f64); 6] = [
    ("balanced", 1.0, 1.0),
    ("intimate", 0.45, 0.35),
    ("stage", 0.85, 0.70),
    ("wide", 1.25, 1.10),
    ("immersive", 1.35, 1.75),
    ("live", 1.55, 1.30),
];

/// The ambient half is a move, not a copy, so a send this high already leaves
/// the stem's dry image thin.
const AMBIENT_MAX: f64 = 0.9;

/// A preset's default surround/height sends for one stem's ambient half.
/// `None` when the preset or the stem is unknown.
pub fn preset_ambient(preset: &str, stem: &str) -> Option<(f64, f64)> {
    let treatment = preset_treatment(preset, stem)?;
    Some((treatment.ambient_rear, treatment.ambient_height))
}

/// A preset's default height crossover for one stem, in Hz.
/// `None` when the preset or the stem is unknown.
pub fn preset_ambient_height_crossover(preset: &str, stem: &str) -> Option<f64> {
    preset_treatment(preset, stem).map(|treatment| treatment.ambient_height_crossover_hz)
}
