// Test-only fixture: a full `constants` payload matching what the backend
// `engine_constants()` serves, using the same values the engine formerly
// hardcoded. Imported by web tests that build a `Configuration` or drive the
// preview engine — NOT by app code (the real values arrive from
// GET /api/v1/configuration at runtime). This is a fixture, not a parity
// source: the authoritative values live in packages/core.
import { resolveEngineConstants, type ServedEngineConstants, type ServedVoicingParams } from "./masteringProfiles";

const neutral: ServedVoicingParams = {
  crossfeed_amount: 0,
  crossfeed_cutoff_hz: 700,
  bass_shelf_hz: 120,
  bass_shelf_gain_db: 0,
  air_shelf_hz: 9000,
  air_shelf_gain_db: 0,
  presence_hz: 3000,
  presence_gain_db: 0,
  presence_q: 0.9,
  stereo_widen: 0,
  loudness_target_lkfs: null,
};

export const TEST_SERVED_CONSTANTS: ServedEngineConstants = {
  channel_group_gains: { center: 0.85, surround: 0.6, back: 0.55, height: 0.55 },
  lfe_gain: 0.31622776601683794,
  lfe_lowpass_hz: 120,
  surround_bass_cutoff_hz: 250,
  height_low_rolloff_hz: 150,
  height_low_rolloff_gain: 0.15,
  height_crossover_hz: 3000,
  height_high_shelf_gain: 1.5,
  soft_limit_threshold: 0.95,
  limiter_lookahead_ms: 5.0,
  limiter_release_ms: 50.0,
  loudness_max_gain_db: 30.0,
  surround_downmix_coeff: 0.7071,
  itu_center_coeff: 1 / Math.sqrt(2),
  diffuse_send_blend: 0.55,
  surround_haas_ms: { left: 31, right: 37 },
  height_haas_ms: { left: 23, right: 29 },
  comp_profiles: {
    transparent: { threshold_db: -22.0, ratio: 1.5, attack_ms: 30.0, release_ms: 300.0, knee_db: 9.0, makeup_db: 0.0 },
    glue: { threshold_db: -18.0, ratio: 2.0, attack_ms: 20.0, release_ms: 200.0, knee_db: 6.0, makeup_db: 0.0 },
    warm: { threshold_db: -15.0, ratio: 2.0, attack_ms: 40.0, release_ms: 400.0, knee_db: 12.0, makeup_db: 0.0 },
  },
  bass_profiles: {
    boost: { sub_gain_db: 2.0, mid_gain_db: 1.0, mono_cutoff_hz: null, excite: false, lfe_gain_db: 1.5 },
    cut: { sub_gain_db: -2.5, mid_gain_db: -1.5, mono_cutoff_hz: null, excite: false, lfe_gain_db: -1.0 },
    mono: { sub_gain_db: 0.0, mid_gain_db: 0.0, mono_cutoff_hz: 100.0, excite: false, lfe_gain_db: 0.0 },
    enhance: { sub_gain_db: 1.5, mid_gain_db: 0.5, mono_cutoff_hz: 80.0, excite: true, lfe_gain_db: 1.0 },
  },
  bass_sub_cutoff_hz: 80.0,
  bass_mid_cutoff_hz: 200.0,
  bass_excite_blend: 0.15,
  bass_excite_drive: 3.0,
  binaural_loudness_max_gain_db: 6.0,
  crosstalk_loudness_max_gain_db: 6.0,
  voicing_params: {
    flat: { ...neutral },
    studio: { ...neutral },
    listening: {
      crossfeed_amount: 0.1,
      crossfeed_cutoff_hz: 700,
      bass_shelf_hz: 100,
      bass_shelf_gain_db: 1.0,
      air_shelf_hz: 10000,
      air_shelf_gain_db: 4.0,
      presence_hz: 3000,
      presence_gain_db: 2.0,
      presence_q: 0.9,
      stereo_widen: 0.15,
      loudness_target_lkfs: null,
    },
  },
  transaural_voicing_params: {
    stereo: { ...neutral },
    smart_speaker: { ...neutral, bass_shelf_hz: 150, bass_shelf_gain_db: 1.5, stereo_widen: 0.2 },
    car: { ...neutral, bass_shelf_hz: 120, bass_shelf_gain_db: 2.5, presence_hz: 2500, presence_gain_db: 1.0, stereo_widen: 0.1 },
    laptop: { ...neutral, bass_shelf_hz: 160, bass_shelf_gain_db: 2.0, presence_hz: 3000, presence_gain_db: 1.0, stereo_widen: 0.25 },
    phone: { ...neutral, bass_shelf_hz: 180, bass_shelf_gain_db: 3.0, presence_hz: 3000, presence_gain_db: 1.5, stereo_widen: 0.3 },
  },
  eq_fir_assets: {
    "spatial-transparent": "master_spatial-transparent",
    "spatial-air": "master_spatial-air",
    "spatial-warm": "master_spatial-warm",
    "spatial-present": "master_spatial-present",
    "atmos-streaming": "master_atmos-streaming",
  },
  stem_eq_fir_assets: {
    "vocal-presence": "stem_vocal-presence",
    "vocal-warmth": "stem_vocal-warmth",
    "bass-warmth": "stem_bass-warmth",
    "bass-cut": "stem_bass-cut",
    "drums-punch": "stem_drums-punch",
    "other-air": "stem_other-air",
    flat: "stem_flat",
  },
  decode_filter_set: {
    flat: "flat_o3_decode",
    studio: "studio_o3_decode",
    listening: "listening_o3_decode",
  },
  xtc_filter_set: {
    stereo: "stereo_xtc",
    smart_speaker: "smart_speaker_xtc",
    car: "car_xtc",
    laptop: "laptop_xtc",
    phone: "phone_xtc",
  },
};

export const TEST_ENGINE_CONSTANTS = resolveEngineConstants(TEST_SERVED_CONSTANTS);
