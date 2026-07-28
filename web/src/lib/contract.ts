// Signed preview/export parity contract — canonical Tier-1/Tier-2 constants.
//
// See docs/contracts/preview_export_parity.md for what this covers and why.
// This module is the TypeScript half of the "signing" mechanism: it mirrors
// upmixer/contract.py::canonical_constants() field-for-field, importing the
// real constants from masteringProfiles.ts (never re-typed literals), and
// hashes them the same way. Both signatures are asserted against the value
// pinned in the contract doc — one by tests/test_contract_parity.py, one by
// contract.test.ts.
//
// Node-only (uses node:crypto): nothing in the browser bundle imports this
// module, only contract.test.ts does, so it never ships to the browser.
//
// Changing any constant this module reads changes contractSignature(). Per
// the contract's change protocol (docs/contracts/README.md), that signature
// change must be intentional and mirrored on both sides — see that document
// before editing any value referenced here.
import { createHash } from "node:crypto";
import {
  BASS_PROFILES,
  BINAURAL_LOUDNESS_MAX_GAIN_DB,
  CENTER_GAIN,
  BACK_GAIN,
  COMP_PROFILES,
  DIFFUSE_SEND_BLEND,
  EXCITE_BLEND,
  EXCITE_DRIVE,
  HEIGHT_CROSSOVER_HZ,
  HEIGHT_GAIN,
  HEIGHT_HAAS_MS,
  HEIGHT_HIGH_SHELF_GAIN,
  HEIGHT_LOW_ROLLOFF_GAIN,
  HEIGHT_LOW_ROLLOFF_HZ,
  ITU_CENTER_COEFF,
  LFE_GAIN,
  LFE_LOWPASS_HZ,
  LIMITER_LOOKAHEAD_MS,
  LIMITER_RELEASE_MS,
  LOUDNESS_MAX_GAIN_DB,
  MID_CUTOFF_HZ,
  SOFT_LIMIT_THRESHOLD,
  SUB_CUTOFF_HZ,
  SURROUND_BASS_CUTOFF_HZ,
  SURROUND_DOWNMIX_COEFF,
  SURROUND_GAIN,
  SURROUND_HAAS_MS,
} from "@/features/projects/masteringProfiles";

/** Mirrors upmixer/contract.py::canonical_constants() field-for-field —
 * same key names, same nesting — so the two produce identical canonical
 * JSON (and therefore the same hash) whenever the underlying values agree. */
export function canonicalConstants(): Record<string, unknown> {
  return {
    channel_group_gains: {
      center: CENTER_GAIN,
      surround: SURROUND_GAIN,
      back: BACK_GAIN,
      height: HEIGHT_GAIN,
    },
    lfe_gain: LFE_GAIN,
    lfe_lowpass_hz: LFE_LOWPASS_HZ,
    surround_bass_cutoff_hz: SURROUND_BASS_CUTOFF_HZ,
    height_low_rolloff_hz: HEIGHT_LOW_ROLLOFF_HZ,
    height_low_rolloff_gain: HEIGHT_LOW_ROLLOFF_GAIN,
    height_crossover_hz: HEIGHT_CROSSOVER_HZ,
    height_high_shelf_gain: HEIGHT_HIGH_SHELF_GAIN,
    soft_limit_threshold: SOFT_LIMIT_THRESHOLD,
    limiter_lookahead_ms: LIMITER_LOOKAHEAD_MS,
    limiter_release_ms: LIMITER_RELEASE_MS,
    loudness_max_gain_db: LOUDNESS_MAX_GAIN_DB,
    surround_downmix_coeff: SURROUND_DOWNMIX_COEFF,
    itu_center_coeff: ITU_CENTER_COEFF,
    diffuse_send_blend: DIFFUSE_SEND_BLEND,
    surround_haas_ms: { left: SURROUND_HAAS_MS.left, right: SURROUND_HAAS_MS.right },
    height_haas_ms: { left: HEIGHT_HAAS_MS.left, right: HEIGHT_HAAS_MS.right },
    comp_profiles: COMP_PROFILES,
    bass_profiles: BASS_PROFILES,
    bass_sub_cutoff_hz: SUB_CUTOFF_HZ,
    bass_mid_cutoff_hz: MID_CUTOFF_HZ,
    bass_excite_blend: EXCITE_BLEND,
    bass_excite_drive: EXCITE_DRIVE,
    binaural_loudness_max_gain_db: BINAURAL_LOUDNESS_MAX_GAIN_DB,
  };
}

/** Format a number so Python and TypeScript hash the same payload.
 *
 * Native JSON.stringify/json.dumps float formatting differs across the two
 * languages (e.g. Python prints `30.0`, JS prints `30`), which would make
 * the two sides' signatures diverge over formatting, not real value drift.
 * Both this function and its Python mirror
 * (upmixer/contract.py::_canonical_number) instead: print integer-valued
 * numbers with no decimal point, and otherwise round to 12 fractional
 * digits and strip trailing zeros (keeping at least one). 12 digits is well
 * inside a float64's ~15-17 significant-digit precision, so both languages'
 * correctly-rounded fixed-decimal conversions agree byte-for-byte on the
 * same underlying double. */
function canonicalNumber(value: number): string {
  if (!Number.isFinite(value)) {
    throw new Error(`Non-finite number not supported in canonical serialization: ${value}`);
  }
  if (Number.isInteger(value)) return String(value);
  const text = value.toFixed(12).replace(/0+$/, "");
  return text.endsWith(".") ? `${text}0` : text;
}

/** Recursively render `value` as deterministic, cross-language JSON text —
 * mirrors upmixer/contract.py::_canonical_value. */
function canonicalValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return canonicalNumber(value);
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalValue).join(",")}]`;
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalValue(obj[key])}`).join(",")}}`;
  }
  throw new TypeError(`Unsupported type for canonical serialization: ${typeof value}`);
}

/** Stable sha256 hex digest of canonicalConstants() — must equal
 * upmixer/contract.py::contract_signature() and the value pinned in
 * docs/contracts/preview_export_parity.md. */
export function contractSignature(): string {
  const payload = canonicalValue(canonicalConstants());
  return createHash("sha256").update(payload, "utf8").digest("hex");
}
