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

// scipy.signal.butter's default Q for a 2nd-order Butterworth section — Web
// Audio's default Q=1 has a small resonant peak a true Butterworth lacks.
// Set explicitly on any biquad standing in for a butter(2, ..., "sos")
// backend filter. Found via golden-diff — see Ledger D9.
export const BUTTERWORTH_Q = 1 / Math.sqrt(2);

// upmixer/mastering/bass.py STEREO_PAIRS — bass mono-maker operates on these
// L/R channel pairs (see `useStemPreview.ts`'s `buildMasteringTopology`).
export const MONO_MAKER_STEREO_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ["FL", "FR"],
  ["SL", "SR"],
  ["BL", "BR"],
  ["TFL", "TFR"],
  ["TBL", "TBR"],
];

/** WaveShaper curve for the backend's tanh soft-limit: identity below
 * `threshold`, tanh saturation above it. Mirrors upmixer/utils.py soft_limit. */
export function buildSoftLimitCurve(threshold: number, samples = 4096): Float32Array {
  const curve = new Float32Array(samples);
  const margin = 1.0 - threshold;
  for (let i = 0; i < samples; i++) {
    const x = (i / (samples - 1)) * 2 - 1;
    const ax = Math.abs(x);
    curve[i] = ax <= threshold
      ? x
      : Math.sign(x) * (threshold + margin * Math.tanh((ax - threshold) / margin));
  }
  return curve;
}

/** WaveShaper curve for the bass exciter: tanh(x * drive). Mirrors the
 * harmonic-exciter stage in upmixer/mastering/bass.py. */
export function buildExciteCurve(drive: number, samples = 4096): Float32Array {
  const curve = new Float32Array(samples);
  for (let i = 0; i < samples; i++) {
    const x = (i / (samples - 1)) * 2 - 1;
    curve[i] = Math.tanh(x * drive);
  }
  return curve;
}

// 4x-oversampled true-peak estimate: a windowed-sinc upsample, not upmixer/
// loudness.py's exact 48-tap kernel — Tier-3, bounded by §5's 1.0 dBTP tolerance.
const _TRUE_PEAK_UPSAMPLE_TAPS = 32;

// Exported so limiterWorklet.test.ts can pin this against limiter.worklet.js's
// hand-duplicated copy (worklet modules can't import this file).
export function buildTruePeakKernel(): Float64Array {
  const taps = _TRUE_PEAK_UPSAMPLE_TAPS;
  const kernel = new Float64Array(taps);
  const center = (taps - 1) / 2;
  for (let i = 0; i < taps; i++) {
    const t = i - center;
    const sinc = t === 0 ? 1 : Math.sin((Math.PI * t) / 4) / ((Math.PI * t) / 4);
    const window = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (taps - 1)); // Hann
    kernel[i] = sinc * window;
  }
  return kernel;
}

function _upsampleTruePeak4x(x: Float32Array | Float64Array): Float64Array {
  const taps = _TRUE_PEAK_UPSAMPLE_TAPS;
  const kernel = buildTruePeakKernel();
  const upsampled = new Float64Array(x.length * 4);
  for (let i = 0; i < x.length; i++) upsampled[i * 4] = x[i];
  const out = new Float64Array(upsampled.length);
  const half = Math.floor(taps / 2);
  for (let i = 0; i < upsampled.length; i++) {
    let sum = 0;
    for (let k = 0; k < taps; k++) {
      const idx = i - half + k;
      if (idx >= 0 && idx < upsampled.length) sum += upsampled[idx] * kernel[k];
    }
    out[i] = sum;
  }
  return out;
}

/** True-peak estimate (dBTP) for one channel's samples — see the
 * `_upsampleTruePeak4x` comment above for provenance/tolerance. */
export function measureBufferTruePeakDbtp(x: Float32Array | Float64Array): number {
  const up = _upsampleTruePeak4x(x);
  let maxPeak = 1e-12;
  for (let i = 0; i < up.length; i++) {
    const a = Math.abs(up[i]);
    if (a > maxPeak) maxPeak = a;
  }
  return 20 * Math.log10(maxPeak);
}

/** Fetches and decodes an EQ FIR asset (see EngineConstants.eqFirAssets/stemEqFirAssets)
 * from `/eq_fir/<assetName>.wav`. Callers should cache the returned promise
 * per `assetName` (see `useStemPreview.ts`'s buffer cache) — the same
 * profile is commonly reused across many stems or across a rebuild. */
export async function fetchEqFirBuffer(ctx: BaseAudioContext, assetName: string): Promise<AudioBuffer> {
  const response = await fetch(`/eq_fir/${assetName}.wav`);
  if (!response.ok) throw new Error(`EQ FIR asset missing: ${assetName}.wav`);
  const data = await response.arrayBuffer();
  return ctx.decodeAudioData(data);
}

export type FirEqNode = {
  input: AudioNode;
  output: AudioNode;
  convolver: ConvolverNode;
  dryGain: GainNode;
  wetGain: GainNode;
  nodes: AudioNode[];
};

/** Builds a wet/dry FIR EQ insert: `input` splits into a dry passthrough and
 * a `ConvolverNode` (bank-summed the same way the backend's `_apply_fir`
 * blends `(1-strength)*dry + strength*filtered`), recombining at `output`.
 * The convolver's `buffer` starts unset (silent wet path, per the Web Audio
 * spec) — non-blocking, matching the binaural decode bank's loading
 * pattern: callers wire this synchronously into the graph, then assign
 * `convolver.buffer` once `fetchEqFirBuffer` resolves, so playback can start
 * immediately while the FIR asset is still fetching/decoding. Callers that
 * rebuild their whole topology on any mastering-config change (as
 * `buildMasteringTopology`/`buildStemEqChains` do) just build a fresh node
 * at the new `strength` rather than retuning an existing one. */
export function buildFirEqNode(ctx: BaseAudioContext, strength: number): FirEqNode {
  const input = ctx.createGain();
  const convolver = ctx.createConvolver();
  convolver.normalize = false;
  const dryGain = ctx.createGain();
  const wetGain = ctx.createGain();
  const output = ctx.createGain();
  input.connect(dryGain).connect(output);
  input.connect(convolver).connect(wetGain).connect(output);
  dryGain.gain.value = 1 - strength;
  wetGain.gain.value = strength;
  return { input, output, convolver, dryGain, wetGain, nodes: [input, convolver, dryGain, wetGain, output] };
}

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

/** Resolve a channel to its group gain. FL/FR always 1.0 (no group). */
export function channelGroupGain(channel: string, gains: ChannelGroupGains): number {
  if (channel === "C") return gains.center;
  if (channel === "BL" || channel === "BR") return gains.back;
  if (channel === "SL" || channel === "SR") return gains.surround;
  if (channel === "TFL" || channel === "TFR" || channel === "TBL" || channel === "TBR") return gains.height;
  return 1.0;
}

/** upmixer/separation/stem_router.py height-send shaping (`_height_send`, same
 * formula as `upmixer/utils.py` `elevation_eq`): attenuate below `lowRolloffHz`
 * to `lowRolloffGain`, then boost above `crossoverHz` by `highShelfGain`. */
export type HeightShaping = {
  lowRolloffHz: number;
  lowRolloffGain: number;
  crossoverHz: number;
  highShelfGain: number;
};

/** Web Audio version of `upmixer/utils.py` `diffuse_send`: blends a signal
 * with a delayed copy of itself for early-reflection decorrelation. */
export function buildDiffuseSend(
  ctx: BaseAudioContext,
  input: AudioNode,
  delayMs: number,
  blend: number,
): { output: AudioNode; nodes: AudioNode[] } {
  const delay = ctx.createDelay(1);
  delay.delayTime.value = delayMs / 1000;
  const dry = ctx.createGain();
  dry.gain.value = 1 - blend;
  const wet = ctx.createGain();
  wet.gain.value = blend;
  const output = ctx.createGain();
  input.connect(dry).connect(output);
  input.connect(delay).connect(wet).connect(output);
  return { output, nodes: [delay, dry, wet, output] };
}

/** Web Audio version of `stem_router.py` `_height_send` /
 * `upmixer/utils.py` `elevation_eq`: sub-bass rolloff (kept at
 * `shaping.lowRolloffGain`, not fully removed) plus a top-end shelf boost
 * above the crossover. Implemented as the additive identity the Python
 * `sosfilt` version reduces to: `shaped = x - low·(1-g); out = shaped +
 * high(shaped)·(shelfGain-1)`. */
export function buildHeightSend(
  ctx: BaseAudioContext,
  input: AudioNode,
  shaping: HeightShaping,
): { output: AudioNode; nodes: AudioNode[] } {
  const lowpass = ctx.createBiquadFilter();
  lowpass.type = "lowpass";
  lowpass.frequency.value = shaping.lowRolloffHz;
  const lowComp = ctx.createGain();
  lowComp.gain.value = -(1 - shaping.lowRolloffGain);
  const shaped = ctx.createGain();
  input.connect(shaped);
  input.connect(lowpass).connect(lowComp).connect(shaped);

  const highpass = ctx.createBiquadFilter();
  highpass.type = "highpass";
  highpass.frequency.value = shaping.crossoverHz;
  highpass.Q.value = BUTTERWORTH_Q;
  const highGain = ctx.createGain();
  highGain.gain.value = shaping.highShelfGain - 1;
  const output = ctx.createGain();
  shaped.connect(output);
  shaped.connect(highpass).connect(highGain).connect(output);

  return { output, nodes: [lowpass, lowComp, shaped, highpass, highGain, output] };
}

/** Web Audio version of `stem_router.py`'s surround send: a highpass at
 * `bassCutoffHz` (keeps rhythmic low end out of the diffuse surround/back
 * layer) followed by the Haas diffuse send. */
export function buildSurroundSend(
  ctx: BaseAudioContext,
  input: AudioNode,
  delayMs: number,
  bassCutoffHz: number,
  diffuseBlend: number,
): { output: AudioNode; nodes: AudioNode[] } {
  const highpass = ctx.createBiquadFilter();
  highpass.type = "highpass";
  highpass.frequency.value = bassCutoffHz;
  highpass.Q.value = BUTTERWORTH_Q;
  input.connect(highpass);
  const diffuse = buildDiffuseSend(ctx, highpass, delayMs, diffuseBlend);
  return { output: diffuse.output, nodes: [highpass, ...diffuse.nodes] };
}

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
export const AMBISONIC_ORDER = 3;
export const N_ACN_CHANNELS = (AMBISONIC_ORDER + 1) * (AMBISONIC_ORDER + 1);

// upmixer/binaural/ambisonics.py::encode_gains's ACN 12 (Y3^0, the order-3
// vertical harmonic) omits the standard N3D sqrt(7) normalization factor. The
// decode filter bank (docs/standards/spatial_audio_engine.md §4) was fit as
// the pseudo-inverse of that unscaled encoder, so this preview's
// standard-N3D real-SH encoder output for ACN 12 must be scaled down to
// match what the filters were designed against. See that doc's §3.
export const ACN12_INDEX = 12;
export const ACN12_N3D_CORRECTION = 1 / Math.sqrt(7);

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

/** Web Audio voicing chain: crossfeed -> bass/air shelves -> presence peak
 * -> M/S widen. Mirrors upmixer/binaural/voicing.py::apply_voicing exactly
 * (same order, same parameters). Builds a fixed topology regardless of
 * profile — a zero-gain shelf/peak or zero-amount crossfeed/widen stage is
 * already numerically an identity, so switching profiles only needs
 * `applyVoicingParams` to retune existing AudioParams, never a graph
 * rebuild. Returns the two output nodes to connect onward (left, right),
 * every node created (for teardown), and the tunable nodes themselves. */
export function buildVoicingChain(ctx: BaseAudioContext, left: AudioNode, right: AudioNode): VoicingChain {
  const lowL = ctx.createBiquadFilter();
  lowL.type = "lowpass";
  const lowR = ctx.createBiquadFilter();
  lowR.type = "lowpass";
  left.connect(lowL);
  right.connect(lowR);

  const dryL = ctx.createGain();
  const dryR = ctx.createGain();
  const bleedToL = ctx.createGain();
  const bleedToR = ctx.createGain();
  const crossfedL = ctx.createGain();
  const crossfedR = ctx.createGain();
  left.connect(dryL).connect(crossfedL);
  lowR.connect(bleedToL).connect(crossfedL);
  right.connect(dryR).connect(crossfedR);
  lowL.connect(bleedToR).connect(crossfedR);

  const bassL = ctx.createBiquadFilter();
  bassL.type = "lowshelf";
  const bassR = ctx.createBiquadFilter();
  bassR.type = "lowshelf";
  const airL = ctx.createBiquadFilter();
  airL.type = "highshelf";
  const airR = ctx.createBiquadFilter();
  airR.type = "highshelf";
  const presenceL = ctx.createBiquadFilter();
  presenceL.type = "peaking";
  const presenceR = ctx.createBiquadFilter();
  presenceR.type = "peaking";
  crossfedL.connect(bassL).connect(airL).connect(presenceL);
  crossfedR.connect(bassR).connect(airR).connect(presenceR);

  // M/S widen: mid = (L+R)/2, side = (L-R) * (1+w)/2; out = mid +- side.
  // `side` carries the true L-R difference (presenceR negated via
  // `sideDiff`) so both sideL and sideR scale the *same* difference signal —
  // tapping presenceL/presenceR directly here would make sideL/sideR each
  // pass a single raw channel instead of a true side signal, so even
  // `stereoWiden = 0` would fail to reduce to identity.
  const mid = ctx.createGain();
  mid.gain.value = 0.5;
  const side = ctx.createGain();
  const sideDiff = ctx.createGain();
  sideDiff.gain.value = -1;
  const sideL = ctx.createGain();
  const sideR = ctx.createGain();
  presenceL.connect(mid);
  presenceR.connect(mid);
  presenceL.connect(side);
  presenceR.connect(sideDiff).connect(side);
  side.connect(sideL);
  side.connect(sideR);

  const outL = ctx.createGain();
  const outR = ctx.createGain();
  mid.connect(outL);
  sideL.connect(outL);
  mid.connect(outR);
  sideR.connect(outR);

  return {
    left: outL,
    right: outR,
    nodes: [
      lowL, lowR, dryL, dryR, bleedToL, bleedToR, crossfedL, crossfedR,
      bassL, bassR, airL, airR, presenceL, presenceR,
      mid, side, sideDiff, sideL, sideR, outL, outR,
    ],
    lowL, lowR, dryL, dryR, bleedToL, bleedToR,
    bassL, bassR, airL, airR, presenceL, presenceR, sideL, sideR,
  };
}

/** Retunes an existing `VoicingChain`'s AudioParams for a new profile — no
 * node creation, so it's safe to call on every profile switch or animate. */
export function applyVoicingParams(chain: VoicingChain, params: VoicingParams): void {
  chain.lowL.frequency.value = params.crossfeedCutoffHz;
  chain.lowR.frequency.value = params.crossfeedCutoffHz;
  chain.dryL.gain.value = 1 - params.crossfeedAmount;
  chain.dryR.gain.value = 1 - params.crossfeedAmount;
  chain.bleedToL.gain.value = params.crossfeedAmount;
  chain.bleedToR.gain.value = params.crossfeedAmount;
  chain.bassL.frequency.value = params.bassShelfHz;
  chain.bassR.frequency.value = params.bassShelfHz;
  chain.bassL.gain.value = params.bassShelfGainDb;
  chain.bassR.gain.value = params.bassShelfGainDb;
  chain.airL.frequency.value = params.airShelfHz;
  chain.airR.frequency.value = params.airShelfHz;
  chain.airL.gain.value = params.airShelfGainDb;
  chain.airR.gain.value = params.airShelfGainDb;
  chain.presenceL.frequency.value = params.presenceHz;
  chain.presenceR.frequency.value = params.presenceHz;
  chain.presenceL.Q.value = params.presenceQ;
  chain.presenceR.Q.value = params.presenceQ;
  chain.presenceL.gain.value = params.presenceGainDb;
  chain.presenceR.gain.value = params.presenceGainDb;
  chain.sideL.gain.value = 0.5 * (1 + params.stereoWiden);
  chain.sideR.gain.value = -0.5 * (1 + params.stereoWiden);
}

/** Approximates `stem_router.py`'s per-stem constant-power `route_scale`
 * (`sqrt(input_energy/routed_energy)`) from the route table alone, treating
 * every contributing send as comparable energy — good enough to keep a
 * widely-routed stem from reading louder than a narrowly-routed one, not an
 * exact energy match (the real value needs the decoded buffers' energy). */
export function estimateRouteScale(route: Record<string, number>, gains: ChannelGroupGains): number {
  let sumSquares = 0;
  for (const [channel, weight] of Object.entries(route)) {
    if (channel === "LFE" || weight <= 0) continue;
    const scaled = weight * channelGroupGain(channel, gains);
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
