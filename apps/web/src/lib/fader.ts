/** Monitor-volume fader taper: maps a 0..1 slider position onto a dB curve
 * instead of linear amplitude, so a given amount of slider travel produces
 * roughly the same perceived loudness change anywhere on the slider (an
 * audio/IEC-60268-17-style taper) instead of concentrating nearly all of the
 * audible range in the top third of the travel, as a raw linear gain does.
 *
 * Web-only monitor-UI concern, deliberately kept out of the preview engine's
 * DSP path — the Tier-1 DSP constants are single-sourced from core (served via
 * GET /api/v1/configuration, see docs/contracts/preview_export_parity.md §2),
 * and monitor gain never reaches the exported render (see useStemPreview.ts's
 * PROGRAM/MONITOR gain split), so it has no parity contract to keep. */

/** Position 0 is true silence; position 1 is unity (0 dB). The program
 * signal reaching the monitor stage is already true-peak-limited and
 * soft-limited (see useStemPreview.ts), so there is no useful headroom
 * above unity to expose here — anything louder belongs on system volume. */
export const FADER_MIN_DB = -60;

const ZERO_SHAPE = 10 ** (FADER_MIN_DB / 20);
const UNITY_FIX = 1 / (1 - ZERO_SHAPE);

/** The un-tapered dB value a slider position maps to (0 at position 1,
 * -Infinity at position 0) — for a numeric readout beside the slider, not
 * for gain math (see `faderPositionToGain`, which folds in the small
 * zero-shape correction this omits). */
export function faderPositionToDb(position: number): number {
  const clamped = Math.max(0, Math.min(1, position));
  if (clamped <= 0) return -Infinity;
  return -FADER_MIN_DB * clamped + FADER_MIN_DB;
}

/** Linear gain for a 0..1 slider position: exactly 0 at position 0, exactly
 * 1 (unity) at position 1, tapered in between so the curve reaches true
 * silence at the bottom of the travel instead of asymptoting toward it. */
export function faderPositionToGain(position: number): number {
  const clamped = Math.max(0, Math.min(1, position));
  if (clamped <= 0) return 0;
  const db = -FADER_MIN_DB * clamped + FADER_MIN_DB;
  return (10 ** (db / 20) - ZERO_SHAPE) * UNITY_FIX;
}

/** Inverse of `faderPositionToDb` — seeds a slider position from a known dB
 * value (e.g. resetting the control to unity). */
export function dbToFaderPosition(db: number): number {
  if (!Number.isFinite(db) || db <= FADER_MIN_DB) return 0;
  const clamped = Math.min(0, db);
  return (clamped - FADER_MIN_DB) / -FADER_MIN_DB;
}

/** Formats a fader position as a readout string, e.g. "0.0 dB" / "-15.0 dB"
 * / "-∞". */
export function formatFaderDb(position: number): string {
  const db = faderPositionToDb(position);
  return Number.isFinite(db) ? `${db.toFixed(1)} dB` : "-∞";
}
