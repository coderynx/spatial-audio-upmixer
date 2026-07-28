import { canvasTheme } from "@/lib/canvasTheme";

/** The Logic Level Meter scale, shared by every meter in the app so a bar in
 * the mixer strip and a bar in the ChannelMeters display read the same dB at
 * the same height and change colour at the same thresholds. */

// Non-linear scale (equal pixel spacing per tick regardless of dB gap) —
// compresses the quiet end so a meter's day-to-day range (roughly -20..0dB)
// gets most of the vertical resolution, matching common DAW meter scales.
export const DB_TICKS = [0, -5, -10, -15, -20, -30, -45, -60];

/** Logic's channel-strip scale: the same equal-pixel-per-tick construction as
 * `DB_TICKS`, at the finer resolution a mixer strip prints beside its meter
 * (3 dB steps down to -18, then coarsening). Only the tick set differs — the
 * mapping algorithm and every zone threshold stay shared, so a strip bar and
 * a ChannelMeters bar still agree about which dB is "hot". */
export const STRIP_DB_TICKS = [0, -3, -6, -9, -12, -15, -18, -24, -30, -35, -40, -50, -60];

export const RED_ZONE_DB = -5;
export const YELLOW_ZONE_DB = -20;
export const CLIP_DB = -1;
export const PEAK_DECAY_DB_PER_SEC = 14;
// Same exponential rate HazeView/ElevationView smooth their per-stem level
// toward — keeps every meter's play/stop ramp visually in sync with the haze
// blobs' and elevation dots' dissolve in/out.
export const LEVEL_SMOOTHING_RATE = 8;
export const SETTLE_EPSILON_DB = 0.05;

/** Per-bar display state shared by every meter: an eased level and a
 * decay-held peak, both keyed by bar.
 *
 * Peak-hold deliberately tracks the **smoothed RMS bar**, not the raw
 * instantaneous sample peak: real music's crest factor puts the true peak far
 * enough above RMS that the tick reads as detached, floating well off the top
 * of its own fill. The instantaneous peak still drives the separate 0dBFS
 * clip latch, which is a different, fixed indicator. Any meter that re-derives
 * this itself will disagree with the others — use this. */
export function createMeterState() {
  const levels = new Map<string, number>();
  const peaks = new Map<string, number>();
  return {
    smoothLevel(key: string, target: number, deltaSec: number) {
      const previous = levels.get(key) ?? 0;
      const next = previous + (target - previous) * Math.min(1, deltaSec * LEVEL_SMOOTHING_RATE);
      levels.set(key, next);
      return next;
    },
    updatePeak(key: string, currentDb: number, deltaSec: number) {
      const previous = peaks.get(key) ?? -60;
      const next = Math.max(currentDb, previous - PEAK_DECAY_DB_PER_SEC * deltaSec);
      peaks.set(key, next);
      return next;
    },
    forget(key: string) {
      levels.delete(key);
      peaks.delete(key);
    },
  };
}

// Only floors at -60 — deliberately not clamped at 0dB on top, so a true
// over (peak amplitude > 1.0) is still distinguishable from a peak that
// merely touched 0dBFS exactly. `dbToY` (screen position) and `zoneColor`
// (both already >= checks) handle values above 0 correctly on their own;
// only the clip latch (`clipped`, computed in useStemPreview.ts from the
// raw un-clamped sample amplitude) is the authoritative "did this clip"
// signal — this function is for display placement, not detection.
export function levelToDb(level: number): number {
  return level > 0.0001 ? Math.max(-60, 20 * Math.log10(level)) : -60;
}

export function dbToY(db: number, top: number, bottom: number, ticks: readonly number[] = DB_TICKS): number {
  const clamped = Math.max(-60, Math.min(0, db));
  for (let i = 0; i < ticks.length - 1; i++) {
    const hi = ticks[i];
    const lo = ticks[i + 1];
    if (clamped <= hi && clamped >= lo) {
      const t = (hi - clamped) / (hi - lo);
      const segmentFraction = (i + t) / (ticks.length - 1);
      return top + segmentFraction * (bottom - top);
    }
  }
  return bottom;
}

/** The three zone colours a lit bar cycles through, bottom to top.
 *
 * Logic itself uses two palettes: its Level Meter plugin runs blue-to-yellow
 * (what `canvasTheme` carries, and what `ChannelMeters` draws), while a mixer
 * channel strip runs green-to-yellow. Both are real Logic; the thresholds and
 * the dB mapping stay shared, only the hue differs by host. */
export type MeterPalette = { safe: string; warn: string; hot: string };

export const FIELD_METER_PALETTE: MeterPalette = {
  safe: canvasTheme.meterSafe,
  warn: canvasTheme.meterWarn,
  hot: canvasTheme.meterHot,
};

export const STRIP_METER_PALETTE: MeterPalette = {
  safe: canvasTheme.stripMeterSafe,
  warn: canvasTheme.meterWarn,
  hot: canvasTheme.meterHot,
};

/** Colour a lit bar takes at a given dB — `safe` below the yellow zone,
 * `warn` through it, `hot` above the red zone. */
export function zoneColor(db: number, palette: MeterPalette = FIELD_METER_PALETTE) {
  if (db >= RED_ZONE_DB) return palette.hot;
  if (db >= YELLOW_ZONE_DB) return palette.warn;
  return palette.safe;
}

/** Logic Level Meter bar: a flat square-ended column painted straight onto
 * the field, changing colour as it crosses each zone, plus a held peak tick.
 * An active channel has no track — an unlit meter is simply background, and
 * the dB gridlines behind carry the structure. A muted channel keeps a slot
 * so it reads as switched off rather than merely silent. */
export function drawMeterBar(
  ctx: CanvasRenderingContext2D,
  barX: number,
  barWidth: number,
  meterTop: number,
  meterBottom: number,
  redBottomY: number,
  yellowBottomY: number,
  currentDb: number,
  peakDb: number,
  muted: boolean,
  clipped: boolean,
  options: {
    // On the always-dark instrument field an unlit meter is simply background,
    // so an active bar has no track (§7). A meter hosted in chrome — the mixer
    // strip — has no such field behind it and would vanish against a light
    // panel, so it passes a recessed well to sit in.
    well?: string;
    palette?: MeterPalette;
    ticks?: readonly number[];
    /** Corner radius, for the rounded slot a channel strip's meter sits in. */
    radius?: number;
  } = {},
) {
  const { well, palette = FIELD_METER_PALETTE, ticks = DB_TICKS, radius = 0 } = options;
  const slot = (fill: string) => {
    ctx.fillStyle = fill;
    ctx.beginPath();
    ctx.roundRect(barX, meterTop, barWidth, meterBottom - meterTop, radius);
    ctx.fill();
  };

  if (muted) {
    slot(well ?? canvasTheme.well);
    ctx.strokeStyle = canvasTheme.mute;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(barX + 0.5, meterTop + 0.5, barWidth - 1, meterBottom - meterTop - 1, radius);
    ctx.stroke();
    return;
  }
  if (well) slot(well);

  ctx.save();
  if (radius > 0) {
    ctx.beginPath();
    ctx.roundRect(barX, meterTop, barWidth, meterBottom - meterTop, radius);
    ctx.clip();
  }
  const fillTopY = dbToY(currentDb, meterTop, meterBottom, ticks);
  const segments: [number, number, string][] = [
    [Math.max(fillTopY, yellowBottomY), meterBottom, palette.safe],
    [Math.max(fillTopY, redBottomY), yellowBottomY, palette.warn],
    [fillTopY, redBottomY, palette.hot],
  ];
  for (const [top, bottom, color] of segments) {
    if (bottom - top <= 0) continue;
    ctx.fillStyle = color;
    ctx.fillRect(barX, top, barWidth, bottom - top);
  }
  ctx.restore();

  // Held peak, drawn as a tick centred on the level it is holding — red once
  // within a hair of clipping, otherwise the colour of the zone it sits in.
  if (peakDb > -60) {
    const peakY = dbToY(peakDb, meterTop, meterBottom, ticks);
    ctx.fillStyle = peakDb >= CLIP_DB ? canvasTheme.mute : zoneColor(peakDb, palette);
    ctx.fillRect(barX, Math.max(meterTop, peakY - 1), barWidth, 2);
  }

  // Latched 0dBFS clip indicator: a fixed cap pinned to the very top of the
  // bar the instant any sample reaches full scale, and held there (see
  // useStemPreview.ts's `clipped` latch) instead of decaying with the peak
  // tick above — a genuine over must stay visible even once playback has
  // moved past it.
  if (clipped) {
    ctx.fillStyle = canvasTheme.mute;
    ctx.fillRect(barX, meterTop, barWidth, 3);
  }
}
