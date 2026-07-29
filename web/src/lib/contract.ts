// Preview/export parity constants — the live cross-check's TypeScript half.
//
// See docs/contracts/preview_export_parity.md for what this covers and why.
// This module mirrors upmixer/contract.py::canonical_constants()
// field-for-field, importing the real constants from masteringProfiles.ts
// (never re-typed literals). web/scripts/dump-constants.mjs dumps
// canonicalConstants() to tests/fixtures/contract/web_constants.json, which
// tests/test_contract_parity.py compares directly against the live Python
// values — no hash, no pinned literal to regenerate.
//
// Changing any constant this module reads changes what that comparison
// checks against. Per the contract's change protocol
// (docs/contracts/README.md), such a change must be mirrored on both sides
// and the web fixture re-dumped (`npm run constants:dump`) — see that
// document before editing any value referenced here.
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
