//! Stem placement and ADM object-size panning into speaker gains.
//!
//! Multiple-Direction Amplitude Panning: a placement becomes a set of virtual
//! sources spanning its width, each panned by VBAP onto one speaker simplex;
//! the gain vectors sum and the result is normalized to constant power. VBAP
//! holds a point placement on the simplex that contains it instead of leaking
//! into every speaker within a falloff radius, and the virtual-source set —
//! rather than how densely the layout happens to be populated around the
//! target — is what decides the image's width.
//!
//! # Simplices
//!
//! VBAP needs a triangulation, not just a speaker set: overlapping candidate
//! triplets would let a direction resolve onto a wide triplet that skips the
//! speakers it sits between. The simplices are the facets of the speakers' own
//! convex hull, derived per layout — a candidate pair (flat layout) or triplet
//! (layout with heights) survives only when every other speaker lies on the
//! listener's side of its plane, which for the basis `B` is
//! `q · B⁻¹ · 1 ≤ 1`. No hull library: the layouts are small and fixed, and
//! the test is one expression over the basis inverse the panning already
//! needs. Three floor speakers are coplanar with the listener, so their basis
//! is singular and they drop out; a floor-level direction is carried by the
//! side facets (two floor speakers plus a height) whose height gain solves to
//! exactly zero, which is pairwise panning on the horizontal ring by another
//! route. Flat layouts have no height to lean on and are solved in the
//! horizontal plane directly, where the same test picks out the ring's
//! adjacent pairs.
//!
//! # Coplanar walls
//!
//! A layout's rear wall and its height layer are flat: four speakers in one
//! plane admit both diagonals as hull facets, so "the" triangulation is not
//! unique there and picking one by score makes the gains jump where the choice
//! flips. Every facet holding the direction contributes instead, averaged.
//! Each of them reproduces the direction exactly, so their mean does too, and
//! a mean of continuous solutions stays continuous where the set of holders
//! changes.
//!
//! # Out of hull
//!
//! Elevation is clamped to what the layout spans (nothing below the horizontal
//! plane, nothing above the height layer). A direction no simplex holds
//! resolves to the simplex with the least negative gain, negatives clamped to
//! zero, which projects it onto the nearest hull edge — the rear of a 5.1 bed
//! lands on its side pair this way. If that leaves nothing, which only the
//! two-speaker bed's rear half can do, the layout is weighted by cosine
//! similarity instead so the placement degrades toward the back of the pair
//! rather than vanishing.
//!
//! # Determinism
//!
//! Pure function of (placement, layout): no RNG, fixed iteration order, ties
//! resolved by the first candidate in sorted order. Pinned against the Python
//! original by the `panner_*` golden fixtures.

use super::adm_extent;
use super::downmix::ITU_CENTER_COEFF;

/// Angular spacing of the virtual sources spanning a placement's width. Finer
/// than the tightest speaker spacing in any supported layout, so a width reads
/// as an arc rather than as its two edges.
pub const VIRTUAL_SOURCE_STEP_DEG: f64 = 15.0;

/// Sends below this are dropped: without a floor a wide placement's outermost
/// virtual sources leave dust in channels the image does not reach.
pub const MINIMUM_SEND: f64 = 1e-3;

/// Degrees of image width a layout with no height pair gets back per degree of
/// elevation it cannot reproduce.
pub const HEIGHT_FLATTEN_WIDTH_FACTOR: f64 = 2.0;

const SINGULAR_BASIS: f64 = 1e-6;
const FACET_EPS: f64 = 1e-9;

/// Two-channel output resolves against the full layout and is folded
/// afterwards, so a stereo mix keeps the same relative image the immersive
/// layouts get. Channel order matches the 7.1.4 output format.
pub const STEREO_PLACEMENT_CHANNELS: [&str; 12] = [
    "FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR",
];

/// Unit-sphere anchor points per channel, listener at the origin facing -Z.
/// Mirrors `binaural/geometry.py`'s `SPEAKER_COORDINATES` and
/// `apps/web/src/lib/spatial.ts`'s `speakerCoordinates`.
const SPEAKER_COORDINATES: [(&str, [f64; 3]); 11] = [
    ("FL", [-0.5, 0.0, -0.87]),
    ("FR", [0.5, 0.0, -0.87]),
    ("C", [0.0, 0.0, -1.0]),
    ("SL", [-0.94, 0.0, 0.34]),
    ("SR", [0.94, 0.0, 0.34]),
    ("BL", [-0.7, 0.0, 0.7]),
    ("BR", [0.7, 0.0, 0.7]),
    ("TFL", [-0.5, 0.6, -0.7]),
    ("TFR", [0.5, 0.6, -0.7]),
    ("TBL", [-0.6, 0.6, 0.6]),
    ("TBR", [0.6, 0.6, 0.6]),
];

/// Where one stem sits, before any layout is applied.
///
/// `azimuth_deg`/`elevation_deg` is the image centre — 0 = front, positive
/// azimuth = left, positive elevation = up. `width_deg` is the image's
/// left/right extent: the stem renders as an arc of virtual sources spanning
/// `azimuth ± width/2`. `object_size` is the normalized ADM Cartesian extent.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct StemPlacement {
    pub azimuth_deg: f64,
    pub elevation_deg: f64,
    pub width_deg: f64,
    pub object_size: f64,
    pub lfe: f64,
}

impl StemPlacement {
    pub const fn new(
        azimuth_deg: f64,
        elevation_deg: f64,
        width_deg: f64,
        object_size: f64,
        lfe: f64,
    ) -> Self {
        Self {
            azimuth_deg,
            elevation_deg,
            width_deg,
            object_size,
            lfe,
        }
    }
}

fn allocentric_position(name: &str, has_rear: bool) -> Option<[f64; 3]> {
    match name {
        "FL" => Some([-1.0, 1.0, 0.0]),
        "FR" => Some([1.0, 1.0, 0.0]),
        "C" => Some([0.0, 1.0, 0.0]),
        "SL" => Some([-1.0, if has_rear { 0.0 } else { -1.0 }, 0.0]),
        "SR" => Some([1.0, if has_rear { 0.0 } else { -1.0 }, 0.0]),
        "BL" => Some([-1.0, -1.0, 0.0]),
        "BR" => Some([1.0, -1.0, 0.0]),
        "TFL" => Some([-1.0, 1.0, 1.0]),
        "TFR" => Some([1.0, 1.0, 1.0]),
        "TBL" => Some([-1.0, -1.0, 1.0]),
        "TBR" => Some([1.0, -1.0, 1.0]),
        _ => None,
    }
}

/// MDAP routes for a linked stereo object's left and right feeds.
///
/// The feeds are independent mono objects at the two ends of the placement's
/// width. Their object size remains linked, while width zero deliberately puts
/// both at the same point.
pub fn object_routes(placement: &StemPlacement, speakers: &[&str]) -> [Vec<f64>; 2] {
    let half_width = placement.width_deg * 0.5;
    [
        object_route(
            placement.azimuth_deg + half_width,
            placement.elevation_deg,
            placement.object_size,
            speakers,
        ),
        object_route(
            placement.azimuth_deg - half_width,
            placement.elevation_deg,
            placement.object_size,
            speakers,
        ),
    ]
}

fn object_route(
    azimuth_deg: f64,
    elevation_deg: f64,
    object_size: f64,
    speakers: &[&str],
) -> Vec<f64> {
    let has_rear = speakers.iter().any(|name| matches!(*name, "BL" | "BR"));
    let positions: Vec<[f64; 3]> = speakers
        .iter()
        .filter_map(|name| allocentric_position(name, has_rear))
        .collect();
    let [x, y, z] = direction(azimuth_deg, elevation_deg);
    let gains = adm_extent::gains(&positions, [x, -z, y], object_size.clamp(0.0, 1.0));
    let mut next = 0;
    speakers
        .iter()
        .map(|name| {
            if is_positional(name) {
                let value = gains.get(next).copied().unwrap_or(0.0);
                next += 1;
                value
            } else {
                0.0
            }
        })
        .collect()
}

/// Azimuth/elevation of one speaker label, in the geometry convention above.
///
/// Derived from the Cartesian table rather than tabulated directly, so this
/// stays the same round trip `binaural/geometry.py` performs: the anchor
/// points are not unit length, and the conversion is what normalizes them.
fn speaker_azimuth_elevation(name: &str) -> Option<(f64, f64)> {
    let position = SPEAKER_COORDINATES
        .iter()
        .find(|(label, _)| *label == name)?
        .1;
    let [x, y, z] = position;
    let radius = (x * x + y * y + z * z).sqrt();
    if radius == 0.0 {
        return Some((0.0, 0.0));
    }
    let elevation = (y / radius).clamp(-1.0, 1.0).asin().to_degrees();
    let azimuth = (-x).atan2(-z).to_degrees();
    Some((azimuth, elevation))
}

/// Unit vector for a direction, listener facing -Z, positive azimuth = left.
pub fn direction(azimuth_deg: f64, elevation_deg: f64) -> [f64; 3] {
    let azimuth = azimuth_deg.to_radians();
    let elevation = elevation_deg.to_radians();
    [
        -elevation.cos() * azimuth.sin(),
        elevation.sin(),
        -elevation.cos() * azimuth.cos(),
    ]
}

/// A speaker set with its hull triangulation, ready to pan directions onto.
struct Layout {
    /// Axis-projected speaker coordinates, one row per speaker.
    coordinates: Vec<Vec<f64>>,
    axes: Vec<usize>,
    /// Highest elevation the layout can reproduce.
    max_elevation_deg: f64,
    /// Speaker indices per simplex, and the inverse of each simplex's basis
    /// stored row-major.
    members: Vec<Vec<usize>>,
    inverses: Vec<Vec<f64>>,
}

/// Determinant of a row-major `dim × dim` matrix, `dim` ∈ {2, 3}.
fn determinant(basis: &[f64], dim: usize) -> f64 {
    match dim {
        2 => basis[0] * basis[3] - basis[1] * basis[2],
        _ => {
            basis[0] * (basis[4] * basis[8] - basis[5] * basis[7])
                - basis[1] * (basis[3] * basis[8] - basis[5] * basis[6])
                + basis[2] * (basis[3] * basis[7] - basis[4] * basis[6])
        }
    }
}

/// Inverse of a row-major `dim × dim` matrix by the adjugate, `dim` ∈ {2, 3}.
fn invert(basis: &[f64], dim: usize, det: f64) -> Vec<f64> {
    match dim {
        2 => vec![
            basis[3] / det,
            -basis[1] / det,
            -basis[2] / det,
            basis[0] / det,
        ],
        _ => {
            let cofactor = [
                basis[4] * basis[8] - basis[5] * basis[7],
                basis[2] * basis[7] - basis[1] * basis[8],
                basis[1] * basis[5] - basis[2] * basis[4],
                basis[5] * basis[6] - basis[3] * basis[8],
                basis[0] * basis[8] - basis[2] * basis[6],
                basis[2] * basis[3] - basis[0] * basis[5],
                basis[3] * basis[7] - basis[4] * basis[6],
                basis[1] * basis[6] - basis[0] * basis[7],
                basis[0] * basis[4] - basis[1] * basis[3],
            ];
            cofactor.iter().map(|value| value / det).collect()
        }
    }
}

/// Every ascending index combination of `dim` speakers out of `count`.
fn combinations(count: usize, dim: usize) -> Vec<Vec<usize>> {
    let mut out = Vec::new();
    let mut index = (0..dim).collect::<Vec<usize>>();
    if dim > count {
        return out;
    }
    loop {
        out.push(index.clone());
        let mut position = dim;
        while position > 0 {
            position -= 1;
            if index[position] != position + count - dim {
                index[position] += 1;
                for next in position + 1..dim {
                    index[next] = index[next - 1] + 1;
                }
                break;
            }
            if position == 0 {
                return out;
            }
        }
    }
}

impl Layout {
    fn new(names: &[&str]) -> Self {
        let positions: Vec<(f64, f64)> = names
            .iter()
            .map(|name| speaker_azimuth_elevation(name).unwrap_or((0.0, 0.0)))
            .collect();
        let vectors: Vec<[f64; 3]> = positions
            .iter()
            .map(|(azimuth, elevation)| direction(*azimuth, *elevation))
            .collect();
        let max_elevation_deg = positions
            .iter()
            .map(|(_, elevation)| *elevation)
            .fold(f64::NEG_INFINITY, f64::max);
        let axes: Vec<usize> = if positions.iter().any(|(_, elevation)| *elevation > 0.0) {
            vec![0, 1, 2]
        } else {
            vec![0, 2]
        };
        let coordinates: Vec<Vec<f64>> = vectors
            .iter()
            .map(|vector| axes.iter().map(|axis| vector[*axis]).collect())
            .collect();

        let dim = axes.len();
        let mut members = Vec::new();
        let mut inverses = Vec::new();
        for combination in combinations(names.len(), dim) {
            let basis: Vec<f64> = combination
                .iter()
                .flat_map(|speaker| coordinates[*speaker].iter().copied())
                .collect();
            let det = determinant(&basis, dim);
            if det.abs() < SINGULAR_BASIS {
                continue;
            }
            let inverse = invert(&basis, dim, det);
            // `q · B⁻¹ · 1 ≤ 1` for every speaker: the facet has all the
            // others on the listener's side of its plane.
            let outside = coordinates.iter().any(|point| {
                let sum: f64 = (0..dim)
                    .map(|column| {
                        (0..dim)
                            .map(|row| point[row] * inverse[row * dim + column])
                            .sum::<f64>()
                    })
                    .sum();
                sum > 1.0 + FACET_EPS
            });
            if outside {
                continue;
            }
            members.push(combination);
            inverses.push(inverse);
        }
        Self {
            coordinates,
            axes,
            max_elevation_deg,
            members,
            inverses,
        }
    }

    /// VBAP gains for one direction, one entry per speaker.
    fn pan(&self, source: [f64; 3]) -> Vec<f64> {
        let dim = self.axes.len();
        let speakers = self.coordinates.len();
        let mut point: Vec<f64> = self.axes.iter().map(|axis| source[*axis]).collect();
        let norm = point.iter().map(|value| value * value).sum::<f64>().sqrt();
        let divisor = if norm > 0.0 { norm } else { 1.0 };
        for value in point.iter_mut() {
            *value /= divisor;
        }

        let mut simplex_gains: Vec<Vec<f64>> = Vec::with_capacity(self.members.len());
        let mut minimum = Vec::with_capacity(self.members.len());
        for inverse in &self.inverses {
            let gains: Vec<f64> = (0..dim)
                .map(|column| {
                    (0..dim)
                        .map(|row| point[row] * inverse[row * dim + column])
                        .sum::<f64>()
                })
                .collect();
            minimum.push(gains.iter().copied().fold(f64::INFINITY, f64::min));
            simplex_gains.push(gains);
        }

        let mut holding: Vec<bool> = minimum.iter().map(|value| *value >= -FACET_EPS).collect();
        if !holding.iter().any(|held| *held) {
            // Out of hull: fall back to the least negative simplex, which
            // clamping then projects onto the nearest hull edge. Ties go to
            // the first candidate, matching `numpy.argmax`.
            let mut best = 0;
            for (index, value) in minimum.iter().enumerate() {
                if *value > minimum[best] {
                    best = index;
                }
            }
            holding[best] = true;
        }

        let mut panned = vec![0.0; speakers];
        let mut held = 0.0;
        for (index, gains) in simplex_gains.iter().enumerate() {
            if !holding[index] {
                continue;
            }
            held += 1.0;
            for (position, speaker) in self.members[index].iter().enumerate() {
                panned[*speaker] += gains[position].max(0.0);
            }
        }
        for value in panned.iter_mut() {
            *value /= held;
        }

        if panned.iter().sum::<f64>() <= 0.0 {
            for (speaker, value) in panned.iter_mut().enumerate() {
                let similarity: f64 = (0..dim)
                    .map(|axis| point[axis] * self.coordinates[speaker][axis])
                    .sum();
                *value = (0.5 * (1.0 + similarity)).max(0.0);
            }
        }
        panned
    }

    /// The virtual sources spanning a placement's width.
    fn virtual_sources(&self, placement: &StemPlacement) -> Vec<[f64; 3]> {
        let elevation = placement.elevation_deg.max(0.0).min(self.max_elevation_deg);
        let width = placement.width_deg.max(0.0);
        let count = if width <= 0.0 {
            1
        } else {
            (2.0_f64).max((width / VIRTUAL_SOURCE_STEP_DEG).ceil() + 1.0) as usize
        };
        let mut sources = Vec::with_capacity(count);
        for index in 0..count {
            let azimuth = if count == 1 {
                placement.azimuth_deg
            } else {
                placement.azimuth_deg - width / 2.0 + width * index as f64 / (count - 1) as f64
            };
            sources.push(direction(azimuth, elevation));
        }
        sources
    }
}

/// A speaker layout whose hull is built once and reused for static object
/// placements during streaming playback.
pub struct PannerLayout {
    layout: Layout,
    positional_channels: Vec<usize>,
    positional_names: Vec<String>,
    channels: usize,
    lfe_index: Option<usize>,
}

impl PannerLayout {
    pub fn new(channels: &[&str]) -> Self {
        let positional_channels: Vec<usize> = channels
            .iter()
            .enumerate()
            .filter_map(|(index, name)| is_positional(name).then_some(index))
            .collect();
        let positional_names: Vec<&str> = positional_channels
            .iter()
            .map(|&index| channels[index])
            .collect();
        Self {
            layout: Layout::new(&positional_names),
            positional_channels,
            positional_names: positional_names.into_iter().map(str::to_owned).collect(),
            channels: channels.len(),
            lfe_index: channels.iter().position(|name| *name == "LFE"),
        }
    }

    pub fn placement_route(&self, placement: &StemPlacement) -> Vec<f64> {
        let gains = self.layout_gains(placement);
        let norm = gains
            .iter()
            .filter(|gain| **gain > MINIMUM_SEND)
            .map(|gain| gain * gain)
            .sum::<f64>()
            .sqrt();
        let mut route = vec![0.0; self.channels];
        if norm <= 0.0 {
            return route;
        }
        for (&channel, gain) in self.positional_channels.iter().zip(gains) {
            if gain > MINIMUM_SEND {
                route[channel] = gain / norm;
            }
        }
        if placement.lfe > 0.0 {
            if let Some(index) = self.lfe_index {
                route[index] = placement.lfe;
            }
        }
        route
    }

    pub fn object_routes(&self, placement: &StemPlacement) -> [Vec<f64>; 2] {
        let half_width = placement.width_deg * 0.5;
        [
            self.exact_object_route(
                placement.azimuth_deg + half_width,
                placement.elevation_deg,
                placement.object_size,
            ),
            self.exact_object_route(
                placement.azimuth_deg - half_width,
                placement.elevation_deg,
                placement.object_size,
            ),
        ]
    }

    pub fn exact_object_route(
        &self,
        azimuth_deg: f64,
        elevation_deg: f64,
        object_size: f64,
    ) -> Vec<f64> {
        let has_rear = self
            .positional_names
            .iter()
            .any(|name| matches!(name.as_str(), "BL" | "BR"));
        let positions: Vec<[f64; 3]> = self
            .positional_names
            .iter()
            .filter_map(|name| allocentric_position(name, has_rear))
            .collect();
        let [x, y, z] = direction(azimuth_deg, elevation_deg);
        let gains = adm_extent::gains(
            &positions,
            [x, -z, y],
            object_size.clamp(0.0, 1.0),
        );
        let mut route = vec![0.0; self.channels];
        for (&channel, gain) in self.positional_channels.iter().zip(gains) {
            route[channel] = gain;
        }
        route
    }

    fn layout_gains(&self, placement: &StemPlacement) -> Vec<f64> {
        if self.positional_channels.is_empty() {
            return Vec::new();
        }
        let mut summed = vec![0.0; self.positional_channels.len()];
        for source in self.layout.virtual_sources(placement) {
            for (speaker, gain) in self.layout.pan(source).iter().enumerate() {
                summed[speaker] += gain;
            }
        }
        let norm = summed.iter().map(|value| value * value).sum::<f64>().sqrt();
        if norm <= 0.0 {
            vec![0.0; self.positional_channels.len()]
        } else {
            summed.into_iter().map(|value| value / norm).collect()
        }
    }
}

/// Constant-power speaker gains for one placement, one entry per name in
/// `speakers`. Names with no known position contribute nothing.
pub fn panning_gains(placement: &StemPlacement, speakers: &[&str]) -> Vec<f64> {
    if speakers.is_empty() {
        return Vec::new();
    }
    let layout = Layout::new(speakers);
    let mut summed = vec![0.0; speakers.len()];
    for source in layout.virtual_sources(placement) {
        for (speaker, gain) in layout.pan(source).iter().enumerate() {
            summed[speaker] += gain;
        }
    }
    let norm = summed.iter().map(|value| value * value).sum::<f64>().sqrt();
    if norm <= 0.0 {
        return vec![0.0; speakers.len()];
    }
    summed.iter().map(|value| value / norm).collect()
}

/// True when a channel name carries a virtual-loudspeaker position — i.e.
/// everything except LFE.
pub fn is_positional(channel: &str) -> bool {
    SPEAKER_COORDINATES
        .iter()
        .any(|(label, _)| *label == channel)
}

/// The highest elevation a channel set can reproduce, in degrees. Placements
/// above this are clamped to it by the panner.
pub fn max_elevation_deg(channels: &[&str]) -> f64 {
    channels
        .iter()
        .filter_map(|name| speaker_azimuth_elevation(name))
        .map(|(_, elevation)| elevation)
        .fold(0.0, f64::max)
}

/// True when the channel set has a height pair.
pub fn has_height(channels: &[&str]) -> bool {
    channels
        .iter()
        .any(|name| matches!(*name, "TFL" | "TFR" | "TBL" | "TBR"))
}

/// Pan one placement into `channels`, constant power, one gain per channel.
///
/// Adds the send floor and the LFE passthrough on top of [`panning_gains`],
/// and renormalizes so dropping the floored sends does not cost the map its
/// constant power. Channels the image does not reach read back as zero.
pub fn placement_route(placement: &StemPlacement, channels: &[&str]) -> Vec<f64> {
    let speakers: Vec<&str> = channels
        .iter()
        .copied()
        .filter(|name| is_positional(name))
        .collect();
    let gains = panning_gains(placement, &speakers);
    let norm = gains
        .iter()
        .filter(|gain| **gain > MINIMUM_SEND)
        .map(|gain| gain * gain)
        .sum::<f64>()
        .sqrt();

    let mut route = vec![0.0; channels.len()];
    if norm <= 0.0 {
        return route;
    }
    let mut next = 0;
    for (index, channel) in channels.iter().enumerate() {
        if !is_positional(channel) {
            continue;
        }
        let gain = gains[next];
        next += 1;
        if gain > MINIMUM_SEND {
            route[index] = gain / norm;
        }
    }
    if placement.lfe > 0.0 {
        if let Some(index) = channels.iter().position(|name| *name == "LFE") {
            route[index] = placement.lfe;
        }
    }
    route
}

/// Restate a canonical placement as what a channel set can reproduce.
///
/// A layout with no height pair cannot carry an elevated placement, and simply
/// zeroing the elevation would pull the stem *inward* onto the front wall —
/// the opposite of what it was placed overhead for. The elevation is spent on
/// width instead, so overhead content wraps to the sides and rear.
///
/// Azimuth needs no clamp: the panner's span already widens to the rearmost
/// pair a layout does have.
pub fn project(placement: &StemPlacement, channels: &[&str]) -> StemPlacement {
    if has_height(channels) || placement.elevation_deg <= 0.0 {
        return *placement;
    }
    StemPlacement {
        elevation_deg: 0.0,
        width_deg: placement.width_deg + HEIGHT_FLATTEN_WIDTH_FACTOR * placement.elevation_deg,
        ..*placement
    }
}

/// Collapse a speaker map onto FL/FR for a two-channel output format.
///
/// Only the resulting left/right *ratio* is meaningful: the router
/// renormalizes each stem to its own loudness afterwards, so the side weights
/// are a pan law, not the BS.775-4 level law. Idempotent.
pub fn fold_route_to_stereo(route: &[f64], channels: &[&str]) -> (f64, f64) {
    let mut left = 0.0;
    let mut right = 0.0;
    for (index, channel) in channels.iter().enumerate() {
        let gain = route[index];
        if gain <= 0.0 || *channel == "LFE" {
            continue;
        }
        match *channel {
            "C" => {
                left += gain * ITU_CENTER_COEFF;
                right += gain * ITU_CENTER_COEFF;
            }
            "FL" | "SL" | "BL" | "TFL" | "TBL" => left += gain,
            "FR" | "SR" | "BR" | "TFR" | "TBR" => right += gain,
            _ => {}
        }
    }
    (left, right)
}

/// The placement a preset gives one stem, realized on `channels`.
pub fn resolve_placement(preset: &str, stem: &str, channels: &[&str]) -> Option<StemPlacement> {
    super::presets::preset_placement(preset, stem).map(|placement| project(&placement, channels))
}

/// Explicit speaker maps for a stem-routing preset, one gain per channel.
///
/// The preset's placements are resolved for `channels`' layout and panned into
/// its speakers; two-channel output is folded here, having been panned across
/// the full layout first. Stems the preset does not name are skipped.
pub fn build_stem_routing(
    stems: &[&str],
    channels: &[&str],
    preset: &str,
) -> Vec<(String, Vec<f64>)> {
    let to_stereo = channels.len() == 2;
    let panning_channels: Vec<&str> = if to_stereo {
        STEREO_PLACEMENT_CHANNELS.to_vec()
    } else {
        channels.to_vec()
    };

    let mut routing = Vec::new();
    for stem in stems {
        let Some(placement) = resolve_placement(preset, stem, &panning_channels) else {
            continue;
        };
        let route = placement_route(&placement, &panning_channels);
        if to_stereo {
            let (left, right) = fold_route_to_stereo(&route, &panning_channels);
            let mut folded = vec![0.0; channels.len()];
            for (index, channel) in channels.iter().enumerate() {
                folded[index] = match *channel {
                    "FL" => left,
                    "FR" => right,
                    _ => 0.0,
                };
            }
            routing.push((stem.to_string(), folded));
        } else {
            routing.push((stem.to_string(), route));
        }
    }
    routing
}
