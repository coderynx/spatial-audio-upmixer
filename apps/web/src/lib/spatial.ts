export type Vec3 = { x: number; y: number; z: number };

// Unit-sphere anchor points per channel, listener at the origin facing -Z.
// x = left(-)/right(+), y = floor(0)/height(+), z = front(-)/back(+). Shared
// by the WebAudio PannerNode positions (useStemPreview.ts) and the Haze view
// (HazeView.tsx) so both agree on where a stem "is".
export const speakerCoordinates: Record<string, Vec3> = {
  FL: { x: -0.5, y: 0, z: -0.87 }, FR: { x: 0.5, y: 0, z: -0.87 }, C: { x: 0, y: 0, z: -1 },
  SL: { x: -0.94, y: 0, z: 0.34 }, SR: { x: 0.94, y: 0, z: 0.34 }, BL: { x: -0.7, y: 0, z: 0.7 }, BR: { x: 0.7, y: 0, z: 0.7 },
  TFL: { x: -0.5, y: 0.6, z: -0.7 }, TFR: { x: 0.5, y: 0.6, z: -0.7 }, TBL: { x: -0.6, y: 0.6, z: 0.6 }, TBR: { x: 0.6, y: 0.6, z: 0.6 },
};

export const speakerLabels: Record<string, string> = {
  FL: "L", FR: "R", C: "C", SL: "Ls", SR: "Rs", BL: "Lrs", BR: "Rrs",
  TFL: "Ltf", TFR: "Rtf", TBL: "Ltb", TBR: "Rtb",
};

/** ITU-R BS.2051-3 SP-label lookup, layout-aware for SL/SR: with no rear
 * pair (5.1/5.1.2/5.1.4 — Systems B/C/D), ±110° is the single side surround
 * "Ls"/"Rs"; once BL/BR are also present (7.1/7.1.4 — Systems I/J), that same
 * position is the *side* surround "Lss"/"Rss" and BL/BR (already labeled
 * Lrs/Rrs) take the rear-surround role instead. */
export function speakerDisplayLabel(channel: string, channels: string[]): string {
  if ((channel === "SL" || channel === "SR") && (channels.includes("BL") || channels.includes("BR"))) {
    return channel === "SL" ? "Lss" : "Rss";
  }
  return speakerLabels[channel] || channel;
}

const TOP_CHANNELS = new Set(["TFL", "TFR", "TBL", "TBR"]);

function weightedCentroid(entries: [string, number][]): Vec3 {
  let total = 0;
  let x = 0;
  let y = 0;
  let z = 0;
  for (const [channel, weight] of entries) {
    if (weight <= 0) continue;
    const speaker = speakerCoordinates[channel];
    if (!speaker) continue;
    total += weight;
    x += speaker.x * weight;
    y += speaker.y * weight;
    z += speaker.z * weight;
  }
  if (total <= 0) return { x: 0, y: 0, z: 0 };
  return { x: x / total, y: y / total, z: z / total };
}

/** Weighted centroid of a stem's routed speaker positions (skips LFE, which
 * has no position in `speakerCoordinates`, and non-positive weights).
 * Returns the origin when the stem has no routing yet. Mirrors the position
 * math `useStemPreview.ts`'s `apply()` feeds into each stem's PannerNode. */
export function stemPosition(route: Record<string, number>): Vec3 {
  return weightedCentroid(Object.entries(route));
}

const LEFT_CHANNELS = new Set(["FL", "SL", "BL", "TFL", "TBL"]);
const RIGHT_CHANNELS = new Set(["FR", "SR", "BR", "TFR", "TBR"]);

/** Splits a stereo stem's routing into independent left/right centroids
 * instead of one collapsed mono point — a stem panned symmetrically (e.g.
 * equal FL/FR sends) is a wide stereo image, not a single dead-center
 * source, and this is what makes that visible. Center-channel weight (`C`)
 * has no left/right side of its own, so it counts toward both centroids
 * (mirrors how a center send carries the same signal to both ears). LFE is
 * excluded, same as `stemPosition`. */
export function stemPositionStereo(route: Record<string, number>): { left: Vec3; right: Vec3 } {
  const entries = Object.entries(route);
  const centerWeight = route.C || 0;
  const left = entries.filter(([channel]) => LEFT_CHANNELS.has(channel));
  const right = entries.filter(([channel]) => RIGHT_CHANNELS.has(channel));
  if (centerWeight > 0) {
    left.push(["C", centerWeight]);
    right.push(["C", centerWeight]);
  }
  const leftTotal = left.reduce((sum, [, weight]) => sum + Math.max(0, weight), 0);
  const rightTotal = right.reduce((sum, [, weight]) => sum + Math.max(0, weight), 0);
  // One side has no routed channels (e.g. purely mono-center or fully
  // panned to the other side) — fall back to the overall centroid so the
  // silent side doesn't collapse to the origin.
  const fallback = stemPosition(route);
  return {
    left: leftTotal > 0 ? weightedCentroid(left) : fallback,
    right: rightTotal > 0 ? weightedCentroid(right) : fallback,
  };
}

/** Fixed azimuth/elevation (degrees) of the 11 positional speakers, in the
 * same JSAmbisonics/ambisonic convention used by `positionToAzimuthElevation`
 * — matches `apps/api/src/features/projects/routing.py` `_POSITIONS` (positive
 * azimuth = left). Computed once from `speakerCoordinates` rather than
 * duplicated, so the two stay in sync automatically. */
export const speakerAzimuthElevation: Record<string, { azim: number; elev: number }> =
  Object.fromEntries(
    Object.entries(speakerCoordinates).map(([channel, position]) => [channel, positionToAzimuthElevation(position)]),
  );

/** Inverse of `useStemPreview.ts`'s `coordinates()`: azimuth (positive =
 * left, matching the ambisonic/SH convention `monoEncoder.azim` expects and
 * the backend's `_POSITIONS` table) and elevation, in degrees, for a
 * Web Audio position vector. */
export function positionToAzimuthElevation(position: Vec3): { azim: number; elev: number } {
  const radius = Math.sqrt(position.x * position.x + position.y * position.y + position.z * position.z);
  if (radius === 0) return { azim: 0, elev: 0 };
  const elev = (Math.asin(Math.min(1, Math.max(-1, position.y / radius))) * 180) / Math.PI;
  const azim = (Math.atan2(-position.x, -position.z) * 180) / Math.PI;
  return { azim, elev };
}

/** Degree-space distance with azimuth wrapped to ±180° — BL/TBL sit at +135°
 * and BR/TBR at −135°, so an unwrapped difference puts the far rear pair
 * ~315° away and drops one whole side from the nearest-3 selection. */
function angularDistance(azimuth: number, elevation: number, position: { azim: number; elev: number }): number {
  const deltaAzimuth = ((((position.azim - azimuth + 180) % 360) + 360) % 360) - 180;
  return Math.hypot(deltaAzimuth, position.elev - elevation);
}

/** Port of `apps/api/src/features/projects/routing.py` `routing_for_scene`'s
 * per-stem mapping: nearest 3 positional speakers to (azimuth, elevation) by
 * Euclidean distance in degree-space (azimuth wrapped), inverse-distance
 * weighted, constant-power normalized. Used as a fallback when a stem has a
 * scene position but no resolved `stem_routing` entry yet. */
export function routingFromAzimuthElevation(azimuth: number, elevation: number): Record<string, number> {
  const available = Object.entries(speakerAzimuthElevation);
  const ranked = available
    .map(([channel, position]) => ({
      channel,
      distance: angularDistance(azimuth, elevation, position),
    }))
    .sort((a, b) => a.distance - b.distance)
    .slice(0, Math.min(3, available.length));
  const weights = ranked.map((entry) => 1 / Math.max(1, entry.distance));
  const norm = Math.sqrt(weights.reduce((sum, weight) => sum + weight * weight, 0)) || 1;
  const mapping: Record<string, number> = {};
  ranked.forEach((entry, index) => {
    mapping[entry.channel] = weights[index] / norm;
  });
  return mapping;
}

/** Top-down compass angle in radians: 0 = front (screen-up), increasing
 * clockwise (PI/2 = right, PI = back, -PI/2 = left). Ignores `y` (height) —
 * the Haze view's radar is a floor-plan projection; height is shown as a
 * separate ring, see `heightFraction`. */
export function vecAngle(vector: Vec3): number {
  if (vector.x === 0 && vector.z === 0) return 0;
  return Math.atan2(vector.x, -vector.z);
}

/** Fraction (0..1) of a stem's total routed weight sent to the four height
 * speakers — the Haze view's height-ring intensity per stem/angle. */
export function heightFraction(route: Record<string, number>): number {
  let top = 0;
  let total = 0;
  for (const [channel, weight] of Object.entries(route)) {
    if (weight <= 0 || !speakerCoordinates[channel]) continue;
    total += weight;
    if (TOP_CHANNELS.has(channel)) top += weight;
  }
  return total > 0 ? top / total : 0;
}
