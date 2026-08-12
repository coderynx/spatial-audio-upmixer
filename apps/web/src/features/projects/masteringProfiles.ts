// Preview mastering + stem-router graph helpers. The tunable DSP *values* the
// backend engine uses (compressor/bass profiles, gains, cutoffs, haas,
// loudness ceilings, voicing params) are NOT defined here — they are fetched
// once at bootstrap from GET /api/v1/configuration's `constants` block and
// threaded in as `EngineConstants` (see resolveEngineConstants below and
// docs/contracts/preview_export_parity.md). This module keeps only the pure
// graph-building functions and the structural/asset constants the web owns.

// FIR asset basenames (EngineConstants.eqFirAssets / .stemEqFirAssets) map each
// profile to its precomputed `/eq_fir/<name>.wav`. These are backend-owned and
// fetched, not hardcoded here — see resolveEngineConstants and
// docs/contracts/preview_export_parity.md §4.
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
};

export type BassProfileName = "boost" | "cut" | "mono" | "enhance";

export type BassProfile = {
  sub_gain_db: number;
  mid_gain_db: number;
  mono_cutoff_hz: number | null;
  excite: boolean;
  lfe_gain_db: number;
};

/** Connect `start -> nodes[0] -> nodes[1] -> ... -> nodes[n-1]` in series and
 * return the last node in the chain (or `start` when `nodes` is empty). */
export function connectSeries(start: AudioNode, nodes: AudioNode[]): AudioNode {
  let previous = start;
  for (const node of nodes) {
    previous.connect(node);
    previous = node;
  }
  return previous;
}

// --- Channel-bed router (ported from upmixer/separation/stem_router.py) —
// see docs/web_architecture.md "Preview audio graph" for why (not HRTF panning). --

/** Per-channel-group gains — upmixer/config.py `center_gain`/`surround_gain`/
 * `back_gain`/`height_gain`. Fetched, not hardcoded (see EngineConstants). */
export type ChannelGroupGains = { center: number; surround: number; back: number; height: number };

/** upmixer/separation/stem_router.py height-send shaping (`_height_send`, same
 * formula as `upmixer/utils.py` `elevation_eq`): attenuate below `lowRolloffHz`
 * to `lowRolloffGain`, then boost above `crossoverHz` by `highShelfGain`. */
export type HeightShaping = {
  lowRolloffHz: number;
  lowRolloffGain: number;
  crossoverHz: number;
  highShelfGain: number;
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

// Ambisonic order for the virtual-loudspeaker renderer shared by the live
// preview (useStemPreview.ts) and the golden-diff harness's extracted
// buildBinauralGraph below — see docs/standards/spatial_audio_engine.md.
// Higher order = tighter localization, more encoder channels ((order+1)^2).

// Decode filter set contract (docs/standards/spatial_audio_engine.md §4):
// 16 ACN channels x {L, R} FIR filters, shipped as four 8-channel WAVs so
// the browser's per-file multichannel decode stays under its 8ch cap.
export const DECODE_FILTER_SPLITS = ["01-08ch", "09-16ch", "17-24ch", "25-32ch"] as const;

// Stereo / Smart-speaker / Car / Laptop / Phone crosstalk-cancellation (transaural) profiles.
// Filter geometry/regularization contract lives in
// docs/standards/transaural_speakers.md; this section carries the XTC asset
// name only. The voicing *values* are fetched
// (EngineConstants.transauralVoicingParams), reusing the same VoicingParams
// shape and Web Audio chain the binaural profiles use.

export type TransauralProfile = "stereo" | "smart_speaker" | "car" | "laptop" | "phone";

// XTC filter set basenames are backend-owned (upmixer/crosstalk/profiles.py
// XTC_FILTER_SET) and fetched as EngineConstants.xtcFilterSet — see
// resolveEngineConstants.

// XTC filter set contract (docs/standards/transaural_speakers.md §4): 4 FIR
// filters (H_LL, H_LR, H_RL, H_RR) in one 4-channel WAV — unlike the 32ch
// binaural decode bank, 4 channels fits well inside the browser's 8ch cap,
// so no multi-file split is needed.
export const XTC_FILTER_CHANNELS = 4;

export type VoicingChain = {
  left: AudioNode;
  right: AudioNode;
  nodes: AudioNode[];
  lowL: BiquadFilterNode;
  lowR: BiquadFilterNode;
  dryL: GainNode;
  dryR: GainNode;
  bleedToL: GainNode;
  bleedToR: GainNode;
  bassL: BiquadFilterNode;
  bassR: BiquadFilterNode;
  airL: BiquadFilterNode;
  airR: BiquadFilterNode;
  presenceL: BiquadFilterNode;
  presenceR: BiquadFilterNode;
  sideL: GainNode;
  sideR: GainNode;
};

/** Approximates `stem_router.py`'s per-stem constant-power `route_scale`
 * (`sqrt(input_energy/routed_energy)`) from the route table alone, treating
 * every contributing send as comparable energy — good enough to keep a
 * widely-routed stem from reading louder than a narrowly-routed one, not an
 * exact energy match (the real value needs the decoded buffers' energy). */
export function estimateRouteScale(route: Record<string, number>, gains: ChannelGroupGains): number {
  const groupGain = (channel: string): number => {
    if (channel === "C") return gains.center;
    if (channel === "BL" || channel === "BR") return gains.back;
    if (channel === "SL" || channel === "SR") return gains.surround;
    if (channel.startsWith("T")) return gains.height;
    return 1;
  };
  let sumSquares = 0;
  for (const [channel, weight] of Object.entries(route)) {
    if (channel === "LFE" || weight <= 0) continue;
    const scaled = weight * groupGain(channel);
    sumSquares += scaled * scaled;
  }
  return sumSquares > 1e-10 ? 1 / Math.sqrt(sumSquares) : 1;
}

// --- Backend-served engine constants ------------------------------------
//
// The web preview engine holds no hardcoded copy of the tunable DSP values;
// it fetches them from GET /api/v1/configuration's `constants` block (see
// apps/api system slice `engine_constants()`). `ServedEngineConstants` is the
// wire shape (snake_case, matching the backend); `EngineConstants` is the
// normalized shape the graph builders consume. resolveEngineConstants maps
// between them — the only place voicing params get their snake->camel rename.

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
  soft_limit_threshold: number;
  limiter_lookahead_ms: number;
  limiter_release_ms: number;
  safety_margin_db: number;
  loudness_max_gain_db: number;
  surround_downmix_coeff: number;
  itu_center_coeff: number;
  diffuse_send_blend: number;
  speaker_directions: Record<string, { azimuth_rad: number; elevation_rad: number }>;
  surround_haas_ms: { left: number; right: number };
  height_haas_ms: { left: number; right: number };
  comp_profiles: Record<string, CompProfile>;
  bass_profiles: Record<string, BassProfile>;
  bass_sub_cutoff_hz: number;
  bass_mid_cutoff_hz: number;
  bass_excite_blend: number;
  bass_excite_drive: number;
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
  softLimitThreshold: number;
  limiterLookaheadMs: number;
  limiterReleaseMs: number;
  safetyMarginDb: number;
  loudnessMaxGainDb: number;
  surroundDownmixCoeff: number;
  ituCenterCoeff: number;
  diffuseSendBlend: number;
  /** Ambisonic encode angles, served so the browser never re-derives them. */
  speakerDirections: Record<string, { azimuth_rad: number; elevation_rad: number }>;
  surroundHaasMs: { left: number; right: number };
  heightHaasMs: { left: number; right: number };
  compProfiles: Record<CompProfileName, CompProfile>;
  bassProfiles: Record<BassProfileName, BassProfile>;
  subCutoffHz: number;
  midCutoffHz: number;
  exciteBlend: number;
  exciteDrive: number;
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
    },
    softLimitThreshold: s.soft_limit_threshold,
    limiterLookaheadMs: s.limiter_lookahead_ms,
    limiterReleaseMs: s.limiter_release_ms,
    safetyMarginDb: s.safety_margin_db,
    loudnessMaxGainDb: s.loudness_max_gain_db,
    surroundDownmixCoeff: s.surround_downmix_coeff,
    ituCenterCoeff: s.itu_center_coeff,
    diffuseSendBlend: s.diffuse_send_blend,
    speakerDirections: s.speaker_directions,
    surroundHaasMs: s.surround_haas_ms,
    heightHaasMs: s.height_haas_ms,
    compProfiles: s.comp_profiles as Record<CompProfileName, CompProfile>,
    bassProfiles: s.bass_profiles as Record<BassProfileName, BassProfile>,
    subCutoffHz: s.bass_sub_cutoff_hz,
    midCutoffHz: s.bass_mid_cutoff_hz,
    exciteBlend: s.bass_excite_blend,
    exciteDrive: s.bass_excite_drive,
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
