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

/// Preset names, in the order they are offered.
pub const PRESET_NAMES: [&str; 6] = [
    "balanced", "intimate", "stage", "wide", "immersive", "live",
];

const BALANCED_PLACEMENTS: [(&str, StemPlacement); 17] = [
    ("Lead Vocals", StemPlacement::new(0.0, 0.0, 0.0, 46.0, 0.0)),
    ("Vocals", StemPlacement::new(0.0, 3.0, 22.0, 54.0, 0.0)),
    ("Backing Vocals", StemPlacement::new(0.0, 14.0, 84.0, 54.0, 0.0)),
    ("Bass", StemPlacement::new(0.0, 0.0, 66.0, 40.0, 0.75)),
    ("Kick", StemPlacement::new(0.0, 0.0, 60.0, 40.0, 0.85)),
    ("Snare", StemPlacement::new(0.0, 0.0, 58.0, 44.0, 0.0)),
    ("Toms", StemPlacement::new(0.0, 0.0, 104.0, 60.0, 0.2)),
    ("Drums", StemPlacement::new(0.0, 0.0, 96.0, 58.0, 0.3)),
    ("Hi-Hat", StemPlacement::new(0.0, 16.0, 76.0, 48.0, 0.0)),
    ("Ride", StemPlacement::new(0.0, 18.0, 80.0, 48.0, 0.0)),
    ("Crash", StemPlacement::new(0.0, 20.0, 90.0, 52.0, 0.0)),
    ("Guitar", StemPlacement::new(0.0, 0.0, 128.0, 70.0, 0.0)),
    ("Piano", StemPlacement::new(0.0, 4.0, 110.0, 62.0, 0.0)),
    ("Other", StemPlacement::new(0.0, 8.0, 116.0, 62.0, 0.15)),
    ("Instrumental", StemPlacement::new(0.0, 6.0, 104.0, 58.0, 0.4)),
    ("Crowd", StemPlacement::new(180.0, 12.0, 120.0, 80.0, 0.0)),
    ("Vocals Reverb", StemPlacement::new(180.0, 22.0, 150.0, 84.0, 0.0)),
];

const INTIMATE_PLACEMENTS: [(&str, StemPlacement); 17] = [
    ("Lead Vocals", StemPlacement::new(0.0, 0.0, 0.0, 38.0, 0.0)),
    ("Vocals", StemPlacement::new(0.0, 0.0, 16.0, 42.0, 0.0)),
    ("Backing Vocals", StemPlacement::new(0.0, 6.0, 52.0, 42.0, 0.0)),
    ("Bass", StemPlacement::new(0.0, 0.0, 52.0, 38.0, 0.7)),
    ("Kick", StemPlacement::new(0.0, 0.0, 32.0, 36.0, 0.8)),
    ("Snare", StemPlacement::new(0.0, 0.0, 30.0, 38.0, 0.0)),
    ("Toms", StemPlacement::new(0.0, 0.0, 48.0, 38.0, 0.18)),
    ("Drums", StemPlacement::new(0.0, 0.0, 44.0, 42.0, 0.28)),
    ("Hi-Hat", StemPlacement::new(0.0, 8.0, 50.0, 38.0, 0.0)),
    ("Ride", StemPlacement::new(0.0, 10.0, 52.0, 38.0, 0.0)),
    ("Crash", StemPlacement::new(0.0, 10.0, 58.0, 42.0, 0.0)),
    ("Guitar", StemPlacement::new(0.0, 0.0, 60.0, 42.0, 0.0)),
    ("Piano", StemPlacement::new(0.0, 0.0, 56.0, 42.0, 0.0)),
    ("Other", StemPlacement::new(0.0, 6.0, 72.0, 50.0, 0.12)),
    ("Instrumental", StemPlacement::new(0.0, 4.0, 64.0, 46.0, 0.35)),
    ("Crowd", StemPlacement::new(180.0, 6.0, 88.0, 58.0, 0.0)),
    ("Vocals Reverb", StemPlacement::new(180.0, 10.0, 104.0, 62.0, 0.0)),
];

const STAGE_PLACEMENTS: [(&str, StemPlacement); 17] = [
    ("Lead Vocals", StemPlacement::new(0.0, 0.0, 0.0, 46.0, 0.0)),
    ("Vocals", StemPlacement::new(0.0, 3.0, 22.0, 54.0, 0.0)),
    ("Backing Vocals", StemPlacement::new(0.0, 16.0, 96.0, 52.0, 0.0)),
    ("Bass", StemPlacement::new(0.0, 0.0, 66.0, 40.0, 0.75)),
    ("Kick", StemPlacement::new(0.0, 0.0, 60.0, 40.0, 0.85)),
    ("Snare", StemPlacement::new(0.0, 0.0, 58.0, 44.0, 0.0)),
    ("Toms", StemPlacement::new(-18.0, 0.0, 52.0, 42.0, 0.2)),
    ("Drums", StemPlacement::new(0.0, 0.0, 96.0, 58.0, 0.3)),
    ("Hi-Hat", StemPlacement::new(32.0, 14.0, 36.0, 48.0, 0.0)),
    ("Ride", StemPlacement::new(-36.0, 16.0, 36.0, 48.0, 0.0)),
    ("Crash", StemPlacement::new(0.0, 24.0, 88.0, 50.0, 0.0)),
    ("Guitar", StemPlacement::new(48.0, 0.0, 52.0, 54.0, 0.0)),
    ("Piano", StemPlacement::new(-48.0, 4.0, 52.0, 54.0, 0.0)),
    ("Other", StemPlacement::new(0.0, 10.0, 104.0, 60.0, 0.15)),
    ("Instrumental", StemPlacement::new(0.0, 6.0, 104.0, 58.0, 0.4)),
    ("Crowd", StemPlacement::new(180.0, 12.0, 120.0, 80.0, 0.0)),
    ("Vocals Reverb", StemPlacement::new(180.0, 22.0, 150.0, 84.0, 0.0)),
];

const WIDE_PLACEMENTS: [(&str, StemPlacement); 17] = [
    ("Lead Vocals", StemPlacement::new(0.0, 0.0, 24.0, 52.0, 0.0)),
    ("Vocals", StemPlacement::new(0.0, 0.0, 40.0, 58.0, 0.0)),
    ("Backing Vocals", StemPlacement::new(0.0, 20.0, 116.0, 62.0, 0.0)),
    ("Bass", StemPlacement::new(0.0, 0.0, 84.0, 50.0, 0.75)),
    ("Kick", StemPlacement::new(0.0, 0.0, 60.0, 40.0, 0.85)),
    ("Snare", StemPlacement::new(0.0, 0.0, 52.0, 50.0, 0.0)),
    ("Toms", StemPlacement::new(0.0, 0.0, 88.0, 52.0, 0.2)),
    ("Drums", StemPlacement::new(0.0, 0.0, 78.0, 56.0, 0.3)),
    ("Hi-Hat", StemPlacement::new(0.0, 20.0, 96.0, 52.0, 0.0)),
    ("Ride", StemPlacement::new(0.0, 24.0, 100.0, 52.0, 0.0)),
    ("Crash", StemPlacement::new(0.0, 28.0, 116.0, 58.0, 0.0)),
    ("Guitar", StemPlacement::new(0.0, 4.0, 116.0, 60.0, 0.0)),
    ("Piano", StemPlacement::new(0.0, 8.0, 106.0, 58.0, 0.0)),
    ("Other", StemPlacement::new(0.0, 18.0, 130.0, 68.0, 0.15)),
    ("Instrumental", StemPlacement::new(0.0, 10.0, 116.0, 62.0, 0.4)),
    ("Crowd", StemPlacement::new(180.0, 16.0, 132.0, 74.0, 0.0)),
    ("Vocals Reverb", StemPlacement::new(180.0, 26.0, 160.0, 88.0, 0.0)),
];

const IMMERSIVE_PLACEMENTS: [(&str, StemPlacement); 17] = [
    ("Lead Vocals", StemPlacement::new(0.0, 0.0, 0.0, 46.0, 0.0)),
    ("Vocals", StemPlacement::new(0.0, 3.0, 22.0, 54.0, 0.0)),
    ("Backing Vocals", StemPlacement::new(0.0, 30.0, 108.0, 68.0, 0.0)),
    ("Bass", StemPlacement::new(0.0, 0.0, 66.0, 40.0, 0.75)),
    ("Kick", StemPlacement::new(0.0, 0.0, 60.0, 40.0, 0.85)),
    ("Snare", StemPlacement::new(0.0, 0.0, 58.0, 44.0, 0.0)),
    ("Toms", StemPlacement::new(0.0, 8.0, 74.0, 48.0, 0.2)),
    ("Drums", StemPlacement::new(0.0, 6.0, 66.0, 52.0, 0.3)),
    ("Hi-Hat", StemPlacement::new(0.0, 32.0, 92.0, 66.0, 0.0)),
    ("Ride", StemPlacement::new(0.0, 34.0, 96.0, 68.0, 0.0)),
    ("Crash", StemPlacement::new(0.0, 36.0, 112.0, 72.0, 0.0)),
    ("Guitar", StemPlacement::new(0.0, 14.0, 96.0, 56.0, 0.0)),
    ("Piano", StemPlacement::new(0.0, 18.0, 90.0, 54.0, 0.0)),
    ("Other", StemPlacement::new(0.0, 30.0, 124.0, 70.0, 0.15)),
    ("Instrumental", StemPlacement::new(0.0, 18.0, 102.0, 60.0, 0.4)),
    ("Crowd", StemPlacement::new(180.0, 28.0, 132.0, 76.0, 0.0)),
    ("Vocals Reverb", StemPlacement::new(180.0, 38.0, 160.0, 90.0, 0.0)),
];

const LIVE_PLACEMENTS: [(&str, StemPlacement); 17] = [
    ("Lead Vocals", StemPlacement::new(0.0, 0.0, 0.0, 46.0, 0.0)),
    ("Vocals", StemPlacement::new(0.0, 0.0, 30.0, 56.0, 0.0)),
    ("Backing Vocals", StemPlacement::new(0.0, 18.0, 104.0, 60.0, 0.0)),
    ("Bass", StemPlacement::new(0.0, 0.0, 66.0, 40.0, 0.75)),
    ("Kick", StemPlacement::new(0.0, 0.0, 60.0, 40.0, 0.85)),
    ("Snare", StemPlacement::new(0.0, 0.0, 58.0, 44.0, 0.0)),
    ("Toms", StemPlacement::new(0.0, 0.0, 86.0, 54.0, 0.2)),
    ("Drums", StemPlacement::new(0.0, 4.0, 92.0, 60.0, 0.3)),
    ("Hi-Hat", StemPlacement::new(0.0, 18.0, 88.0, 52.0, 0.0)),
    ("Ride", StemPlacement::new(0.0, 22.0, 92.0, 52.0, 0.0)),
    ("Crash", StemPlacement::new(0.0, 26.0, 104.0, 58.0, 0.0)),
    ("Guitar", StemPlacement::new(0.0, 2.0, 106.0, 58.0, 0.0)),
    ("Piano", StemPlacement::new(0.0, 6.0, 96.0, 56.0, 0.0)),
    ("Other", StemPlacement::new(0.0, 14.0, 136.0, 72.0, 0.15)),
    ("Instrumental", StemPlacement::new(0.0, 8.0, 118.0, 62.0, 0.4)),
    ("Crowd", StemPlacement::new(180.0, 22.0, 150.0, 84.0, 0.0)),
    ("Vocals Reverb", StemPlacement::new(180.0, 30.0, 168.0, 92.0, 0.0)),
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

/// The canonical placement a preset gives one stem, before any layout is
/// applied. `None` when the preset or the stem is unknown.
pub fn preset_placement(preset: &str, stem: &str) -> Option<StemPlacement> {
    let table = preset_table(preset)?;
    table.iter().find(|(name, _)| *name == stem).map(|(_, placement)| *placement)
}

/// Every stem a preset names, in table order.
pub fn preset_stems(preset: &str) -> &'static [(&'static str, StemPlacement)] {
    preset_table(preset).unwrap_or(&[])
}
