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

/** The stem's position between hard left (0) and hard right (1), 0.5 when
 * the pair is silent. Read-only: the panorama view reports a pan, the mix
 * editor sets one through the placement's azimuth. */
export function stemPan(route: Record<string, number>): number {
  const left = route.FL || 0;
  const right = route.FR || 0;
  if (left <= 0 && right <= 0) return 0.5;
  return Math.atan2(right, left) / (Math.PI / 2);
}

/** Top-down compass angle in radians: 0 = front (screen-up), increasing
 * clockwise (PI/2 = right, PI = back, -PI/2 = left). Ignores `y` (height) —
 * the Haze view's radar is a floor-plan projection; height is shown as a
 * separate ring, see `heightFraction`. */
export function vecAngle(vector: Vec3): number {
  if (vector.x === 0 && vector.z === 0) return 0;
  return Math.atan2(vector.x, -vector.z);
}

function weightFraction(route: Record<string, number>, of: Set<string>): number {
  let part = 0;
  let total = 0;
  for (const [channel, weight] of Object.entries(route)) {
    if (weight <= 0 || !speakerCoordinates[channel]) continue;
    total += weight;
    if (of.has(channel)) part += weight;
  }
  return total > 0 ? part / total : 0;
}

/** Fraction (0..1) of a stem's total routed weight sent to the four height
 * speakers — the Haze view's height-ring intensity per stem/angle. */
export function heightFraction(route: Record<string, number>): number {
  return weightFraction(route, TOP_CHANNELS);
}
