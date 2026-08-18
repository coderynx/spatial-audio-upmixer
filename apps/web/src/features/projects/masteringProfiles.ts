// Preview mastering + stem-router graph helpers. The tunable DSP *values* the
// backend engine uses (compressor/bass profiles, gains, cutoffs,
// loudness ceilings, voicing params) are NOT defined here — they are fetched
// once at bootstrap from GET /api/v1/configuration's `constants` block and
// threaded in as `EngineConstants` (see resolveEngineConstants below and
// docs/contracts/preview_export_parity.md). This module keeps only the pure
// graph-building functions and the structural/asset constants the web owns.

// FIR asset basenames (EngineConstants.eqFirAssets / .stemEqFirAssets) map each
// profile to its precomputed `/eq_fir/<name>.wav`. These are backend-owned and
// fetched, not hardcoded here — see resolveEngineConstants and
// docs/contracts/preview_export_parity.md §2.
export type EqProfileName =
  | "spatial-transparent"
  | "spatial-air"
  | "spatial-warm"
  | "spatial-present"
  | "atmos-streaming";

export type StemEqProfileName =
  | "vocal-presence"
  | "vocal-warmth"
  | "bass-warmth"
  | "bass-cut"
  | "drums-punch"
  | "other-air"
  | "flat";

export type CompProfileName = "transparent" | "glue" | "warm";

export type CompProfile = {
  threshold_db: number;
  ratio: number;
  attack_ms: number;
  release_ms: number;
  knee_db: number;
  makeup_db: number;
  sidechain_hpf_hz: number | null;
};

export type BassProfileName = "boost" | "cut" | "mono" | "enhance" | "deep" | "cinema";

export type LfSpreadName = "front" | "bed" | "all";
export type BassLfeMode = "off" | "add" | "split";

export type BassProfile = {
  sub_gain_db: number;
  mid_gain_db: number;
  unify_hz: number | null;
  spread: LfSpreadName;
  punch: number;
  excite: boolean;
  lfe_mode: BassLfeMode;
  lfe_send: number;
  lfe_gain_db: number;
  decorrelate: number;
};

/** One named delivery specification. `tolerance_lu` is null where the spec
 * publishes a target without one. */
export type DeliveryTarget = {
  target_lkfs: number;
  max_tp_dbtp: number;
  tolerance_lu: number | null;
};

/** A mastering block as the project stores it: a profile name plus per-field
 * overrides, any of which may be null meaning "use the profile's value".
 *
 * Values are loosely typed because they arrive from the stored manifest,
 * whose enum-valued fields the API validates; only the key set is pinned. */
type Overrides<T> = { profile?: string | null } & { [K in keyof T]?: unknown };

/** Resolve a profile name plus overrides into concrete values.
 *
 * Both the export chain (`mastering/chain.py`) and the preview have to do
 * this; doing it here rather than inside the engine-parameter builder is what
 * keeps a moved pot from being silently dropped on the way to the worklet
 * (see docs/contracts/preview_export_parity.md). */
function resolveProfile<T extends object>(
  block: Overrides<T> | null | undefined,
  presets: Record<string, T>,
): T | null {
  if (!block) return null;
  const preset = block.profile ? presets[block.profile] : undefined;
  if (!preset) return null;
  const resolved = { ...preset };
  for (const key of Object.keys(preset) as (keyof T)[]) {
    const override = block[key];
    if (override !== undefined && override !== null) resolved[key] = override as T[keyof T];
  }
  return resolved;
}

export const resolveBassParams = (
  block: Overrides<BassProfile> | null | undefined,
  presets: Record<string, BassProfile>,
) => resolveProfile(block, presets);

export const resolveCompParams = (
  block: Overrides<CompProfile> | null | undefined,
  presets: Record<string, CompProfile>,
) => resolveProfile(block, presets);

/** Mirror of `bass.py`'s `resolve_lf_targets`: `(speaker index, weight)` pairs
 * for the LF redistribution. Non-LFE weights sum to 1 in `off`/`add` and to
 * `1 - lfe_send` in `split`; the LFE entry carries the BS.775 authoring gain,
 * which playback's +10 dB replay gain undoes. */
export function resolveLfTargets(
  speakers: string[],
  bass: BassProfile,
  spreads: Record<string, string[]>,
  lfeAuthoringGain: number,
): [number, number][] {
  const members = spreads[bass.spread];
  if (!members) return [];
  const index = new Map(speakers.map((name, i) => [name, i]));
  const present = members.filter((name) => index.has(name)).map((name) => index.get(name)!);
  if (present.length === 0) return [];

  const lfe = index.get("LFE");
  const send =
    lfe === undefined || bass.lfe_mode === "off"
      ? 0
      : Math.min(1, Math.max(0, bass.lfe_send));
  const bedShare = bass.lfe_mode === "split" ? 1 - send : 1;

  const targets: [number, number][] = present.map((i) => [i, bedShare / present.length]);
  if (send > 0) targets.push([lfe!, send * lfeAuthoringGain]);
  return targets;
}

// --- Channel-bed router (ported from upmixer/separation/stem_router.py) —
// see docs/web_architecture.md "Preview audio graph" for why (not HRTF panning). --

/** Per-channel-group gains — upmixer/config.py `center_gain`/`surround_gain`/
 * `back_gain`/`height_gain`. Fetched, not hardcoded (see EngineConstants). */
export type ChannelGroupGains = { center: number; surround: number; back: number; height: number };

/** upmixer/separation/stem_router.py height-send shaping (`_height_send`, same
 * formula as `upmixer/utils.py` `elevation_eq`): attenuate below `lowRolloffHz`
 * to `lowRolloffGain`, boost above `crossoverHz` by `highShelfGain`, then lift
 * the directional band at `directionalBandHz` by `directionalBandGain`. */
export type HeightShaping = {
  lowRolloffHz: number;
  lowRolloffGain: number;
  crossoverHz: number;
  highShelfGain: number;
  directionalBandHz: number;
  directionalBandGain: number;
};

// --- Spatial Audio Engine voicing chain (ported from upmixer/binaural/) --
//
// Studio/Listening/Flat binaural profiles. Filter geometry/SH/decode-filter
// contract lives in docs/standards/spatial_audio_engine.md; this section
// only carries the post-decode voicing chain topology. The per-profile
// voicing *values* are fetched (EngineConstants.voicingParams).

export type SpatialProfile = "studio" | "listening" | "flat";

// Decode-filter set basenames are backend-owned (upmixer/binaural/profiles.py
// DECODE_FILTER_SET) and fetched as EngineConstants.decodeFilterSet — see
// resolveEngineConstants.

export type VoicingParams = {
  crossfeedAmount: number;
  crossfeedCutoffHz: number;
  bassShelfHz: number;
  bassShelfGainDb: number;
  airShelfHz: number;
  airShelfGainDb: number;
  presenceHz: number;
  presenceGainDb: number;
  presenceQ: number;
  stereoWiden: number;
  loudnessTargetLkfs: number | null;
};

// Decode filter set contract (docs/standards/spatial_audio_engine.md §4):
// 16 ACN channels x {L, R} FIR filters, shipped as four 8-channel WAVs so
// the browser's per-file multichannel decode stays under its 8ch cap.

// Stereo / Smart-speaker / Car / Laptop / Phone crosstalk-cancellation (transaural) profiles.
// Filter geometry/regularization contract lives in
// docs/standards/transaural_speakers.md; this section carries the XTC asset
// name only. The voicing *values* are fetched
// (EngineConstants.transauralVoicingParams), reusing the same VoicingParams
// shape as the binaural profiles.

export type TransauralProfile = "stereo" | "smart_speaker" | "car" | "laptop" | "phone";

// XTC filter set basenames are backend-owned (upmixer/crosstalk/profiles.py
// XTC_FILTER_SET) and fetched as EngineConstants.xtcFilterSet — see
// resolveEngineConstants.

// XTC filter set contract (docs/standards/transaural_speakers.md §4): 4 FIR
// filters (H_LL, H_LR, H_RL, H_RR) in one 4-channel WAV — unlike the 32ch
// binaural decode bank, 4 channels fits well inside the browser's 8ch cap,
// so no multi-file split is needed.

/** Approximates `stem_router.py`'s per-stem `route_scale` from the route table
 * alone, treating every contributing send as comparable energy — good enough to
 * keep a widely-routed stem from reading louder than a narrowly-routed one, not
 * an exact match (the real value measures the decoded buffers, K-weighted per
 * BS.1770 — ledger D3). The channel weights below are that measurement's, so
 * the estimate at least carries the surround channels' +1.5 dB. */
export function estimateRouteScale(route: Record<string, number>, gains: ChannelGroupGains): number {
  const groupGain = (channel: string): number => {
    if (channel === "C") return gains.center;
    if (channel === "BL" || channel === "BR") return gains.back;
    if (channel === "SL" || channel === "SR") return gains.surround;
    if (channel.startsWith("T")) return gains.height;
    return 1;
  };
  // BS.1770-5: side surrounds +1.5 dB, every other non-LFE channel unity.
  const loudnessWeight = (channel: string): number =>
    channel === "SL" || channel === "SR" ? 1.41 : 1;
  let sumSquares = 0;
  for (const [channel, weight] of Object.entries(route)) {
    if (channel === "LFE" || weight <= 0) continue;
    const scaled = weight * groupGain(channel);
    sumSquares += loudnessWeight(channel) * scaled * scaled;
  }
  return sumSquares > 1e-10 ? 1 / Math.sqrt(sumSquares) : 1;
}

/** Wire shape of one voicing profile (backend snake_case). */
export type ServedVoicingParams = {
  crossfeed_amount: number;
  crossfeed_cutoff_hz: number;
  bass_shelf_hz: number;
  bass_shelf_gain_db: number;
  air_shelf_hz: number;
  air_shelf_gain_db: number;
  presence_hz: number;
  presence_gain_db: number;
  presence_q: number;
  stereo_widen: number;
  loudness_target_lkfs: number | null;
};

/** Wire shape of the `constants` block from GET /api/v1/configuration. */
export type ServedEngineConstants = {
  channel_group_gains: ChannelGroupGains;
  lfe_gain: number;
  lfe_lowpass_hz: number;
  surround_bass_cutoff_hz: number;
  height_low_rolloff_hz: number;
  height_low_rolloff_gain: number;
  height_crossover_hz: number;
  height_high_shelf_gain: number;
  height_directional_band_hz: number;
  height_directional_band_gain: number;
  stem_transient_duck: number;
  soft_limit_threshold: number;
  limiter_lookahead_ms: number;
  limiter_release_ms: number;
  safety_margin_db: number;
  loudness_max_gain_db: number;
  surround_downmix_coeff: number;
  height_downmix_coeff: number;
  itu_center_coeff: number;
  speaker_directions: Record<string, { azimuth_rad: number; elevation_rad: number }>;
  comp_profiles: Record<string, CompProfile>;
  bass_profiles: Record<string, BassProfile>;
  delivery_targets: Record<string, DeliveryTarget>;
  bass_sub_cutoff_hz: number;
  bass_mid_cutoff_hz: number;
  bass_excite_blend: number;
  bass_excite_drive: number;
  bass_lf_spreads: Record<string, string[]>;
  bass_punch_fast_ms: number;
  bass_punch_slow_ms: number;
  bass_punch_max_db: number;
  bass_decorr_low_hz: number;
  bass_decorr_high_hz: number;
  bass_decorr_sections: number;
  bass_decorr_max_delay_ms: number;
  bass_decorr_fast_ms: number;
  bass_decorr_slow_ms: number;
  binaural_loudness_max_gain_db: number;
  crosstalk_loudness_max_gain_db: number;
  voicing_params: Record<string, ServedVoicingParams>;
  transaural_voicing_params: Record<string, ServedVoicingParams>;
  eq_fir_assets: Record<string, string>;
  stem_eq_fir_assets: Record<string, string>;
  decode_filter_set: Record<string, string>;
  xtc_filter_set: Record<string, string>;
};

/** Normalized engine constants the preview graph builders consume. */
export type EngineConstants = {
  channelGains: ChannelGroupGains;
  lfeGain: number;
  lfeLowpassHz: number;
  surroundBassCutoffHz: number;
  heightShaping: HeightShaping;
  stemTransientDuck: number;
  softLimitThreshold: number;
  limiterLookaheadMs: number;
  limiterReleaseMs: number;
  safetyMarginDb: number;
  loudnessMaxGainDb: number;
  surroundDownmixCoeff: number;
  heightDownmixCoeff: number;
  ituCenterCoeff: number;
  /** Ambisonic encode angles, served so the browser never re-derives them. */
  speakerDirections: Record<string, { azimuth_rad: number; elevation_rad: number }>;
  compProfiles: Record<CompProfileName, CompProfile>;
  bassProfiles: Record<BassProfileName, BassProfile>;
  subCutoffHz: number;
  midCutoffHz: number;
  exciteBlend: number;
  exciteDrive: number;
  lfSpreads: Record<LfSpreadName, string[]>;
  punchFastMs: number;
  punchSlowMs: number;
  punchMaxDb: number;
  decorrLowHz: number;
  decorrHighHz: number;
  decorrSections: number;
  decorrMaxDelayMs: number;
  decorrFastMs: number;
  decorrSlowMs: number;
  binauralLoudnessMaxGainDb: number;
  crosstalkLoudnessMaxGainDb: number;
  voicingParams: Record<SpatialProfile, VoicingParams>;
  transauralVoicingParams: Record<TransauralProfile, VoicingParams>;
  eqFirAssets: Record<EqProfileName, string>;
  stemEqFirAssets: Record<StemEqProfileName, string>;
  decodeFilterSet: Record<SpatialProfile, string>;
  xtcFilterSet: Record<TransauralProfile, string>;
};

function voicingFromServed(v: ServedVoicingParams): VoicingParams {
  return {
    crossfeedAmount: v.crossfeed_amount,
    crossfeedCutoffHz: v.crossfeed_cutoff_hz,
    bassShelfHz: v.bass_shelf_hz,
    bassShelfGainDb: v.bass_shelf_gain_db,
    airShelfHz: v.air_shelf_hz,
    airShelfGainDb: v.air_shelf_gain_db,
    presenceHz: v.presence_hz,
    presenceGainDb: v.presence_gain_db,
    presenceQ: v.presence_q,
    stereoWiden: v.stereo_widen,
    loudnessTargetLkfs: v.loudness_target_lkfs,
  };
}

function mapVoicing<K extends string>(served: Record<string, ServedVoicingParams>): Record<K, VoicingParams> {
  const out: Record<string, VoicingParams> = {};
  for (const [key, value] of Object.entries(served)) out[key] = voicingFromServed(value);
  return out as Record<K, VoicingParams>;
}

/** Normalize the wire `constants` block into `EngineConstants`. */
export function resolveEngineConstants(s: ServedEngineConstants): EngineConstants {
  return {
    channelGains: s.channel_group_gains,
    lfeGain: s.lfe_gain,
    lfeLowpassHz: s.lfe_lowpass_hz,
    surroundBassCutoffHz: s.surround_bass_cutoff_hz,
    heightShaping: {
      lowRolloffHz: s.height_low_rolloff_hz,
      lowRolloffGain: s.height_low_rolloff_gain,
      crossoverHz: s.height_crossover_hz,
      highShelfGain: s.height_high_shelf_gain,
      directionalBandHz: s.height_directional_band_hz,
      directionalBandGain: s.height_directional_band_gain,
    },
    stemTransientDuck: s.stem_transient_duck,
    softLimitThreshold: s.soft_limit_threshold,
    limiterLookaheadMs: s.limiter_lookahead_ms,
    limiterReleaseMs: s.limiter_release_ms,
    safetyMarginDb: s.safety_margin_db,
    loudnessMaxGainDb: s.loudness_max_gain_db,
    surroundDownmixCoeff: s.surround_downmix_coeff,
    heightDownmixCoeff: s.height_downmix_coeff,
    ituCenterCoeff: s.itu_center_coeff,
    speakerDirections: s.speaker_directions,
    compProfiles: s.comp_profiles as Record<CompProfileName, CompProfile>,
    bassProfiles: s.bass_profiles as Record<BassProfileName, BassProfile>,
    subCutoffHz: s.bass_sub_cutoff_hz,
    midCutoffHz: s.bass_mid_cutoff_hz,
    exciteBlend: s.bass_excite_blend,
    exciteDrive: s.bass_excite_drive,
    lfSpreads: s.bass_lf_spreads as Record<LfSpreadName, string[]>,
    punchFastMs: s.bass_punch_fast_ms,
    punchSlowMs: s.bass_punch_slow_ms,
    punchMaxDb: s.bass_punch_max_db,
    decorrLowHz: s.bass_decorr_low_hz,
    decorrHighHz: s.bass_decorr_high_hz,
    decorrSections: s.bass_decorr_sections,
    decorrMaxDelayMs: s.bass_decorr_max_delay_ms,
    decorrFastMs: s.bass_decorr_fast_ms,
    decorrSlowMs: s.bass_decorr_slow_ms,
    binauralLoudnessMaxGainDb: s.binaural_loudness_max_gain_db,
    crosstalkLoudnessMaxGainDb: s.crosstalk_loudness_max_gain_db,
    voicingParams: mapVoicing<SpatialProfile>(s.voicing_params),
    transauralVoicingParams: mapVoicing<TransauralProfile>(s.transaural_voicing_params),
    eqFirAssets: s.eq_fir_assets as Record<EqProfileName, string>,
    stemEqFirAssets: s.stem_eq_fir_assets as Record<StemEqProfileName, string>,
    decodeFilterSet: s.decode_filter_set as Record<SpatialProfile, string>,
    xtcFilterSet: s.xtc_filter_set as Record<TransauralProfile, string>,
  };
}
