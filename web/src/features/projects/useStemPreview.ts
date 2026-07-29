import * as React from "react";
import type { ProjectStem, StemScene } from "@/api";
import { positionToAzimuthElevation, routingFromAzimuthElevation, speakerCoordinates } from "@/lib/spatial";
import {
  BINAURAL_LOUDNESS_MAX_GAIN_DB,
  DECODE_FILTER_SET,
  ITU_CENTER_COEFF,
  LFE_GAIN,
  LFE_LOWPASS_HZ,
  LIMITER_LOOKAHEAD_MS,
  LIMITER_RELEASE_MS,
  LOUDNESS_MAX_GAIN_DB,
  N_ACN_CHANNELS,
  STEM_EQ_FIR_ASSETS,
  SURROUND_DOWNMIX_COEFF,
  SURROUND_HAAS_MS,
  HEIGHT_HAAS_MS,
  VOICING_PARAMS,
  applyVoicingParams,
  buildDiffuseSend,
  buildFirEqNode,
  buildHeightSend,
  buildSoftLimitCurve,
  buildSurroundSend,
  channelGroupGain,
  estimateRouteScale,
  measureBufferTruePeakDbtp,
  type SpatialProfile,
  type StemEqProfileName,
  type VoicingChain,
} from "./masteringProfiles";
import {
  assignDecodeFilterBuffers,
  buildBinauralGraph,
  buildMasteringGraph,
  createPositionalEncoder,
  loadCachedDecodeFilterChannels,
  loadCachedEqBuffer,
  loadCachedRefMatchBuffers,
  type MasterPreview,
} from "./previewGraph";
import { faderPositionToGain } from "@/lib/fader";

export type { SpatialProfile } from "./masteringProfiles";

// Fetches one part of a profile's decode filter set (see
// `DECODE_FILTER_SPLITS` in masteringProfiles.ts) from `/hrir/` — the
// browser-`fetch` partLoader `loadDecodeFilterChannels` (previewGraph.ts)
// needs to stay decoupled from the golden-diff harness's disk-read loader.
async function fetchDecodeFilterPart(ctx: BaseAudioContext, partName: string): Promise<AudioBuffer> {
  const response = await fetch(`/hrir/${partName}.wav`);
  if (!response.ok) throw new Error(`Decode filter part missing: ${partName}.wav`);
  const data = await response.arrayBuffer();
  return ctx.decodeAudioData(data);
}


// Preview monitoring mode: which final render stage the channel bed feeds.
// "binaural" is the existing headphone-virtualized render; "stereo" is a
// BS.775-compliant 2/0 downmix; "native" sends the channel bed's own
// discrete channels straight to the chosen system output device.
export type OutputMode = "binaural" | "stereo" | "native";

// ITU-R BS.775-4 Annex 4 Table 2 2/0 downmix coefficients, mirroring
// upmixer/utils.py::itu_downmix_stereo. Back channels fold into the
// matching side channel attenuated by the centre coefficient, same as the
// backend. Height channels and LFE are excluded per the standard — LFE
// gets its own discrete native send instead.
const STEREO_DOWNMIX_GAINS: Partial<Record<string, { left: number; right: number }>> = {
  FL: { left: 1, right: 0 },
  FR: { left: 0, right: 1 },
  C: { left: ITU_CENTER_COEFF, right: ITU_CENTER_COEFF },
  SL: { left: SURROUND_DOWNMIX_COEFF, right: 0 },
  SR: { left: 0, right: SURROUND_DOWNMIX_COEFF },
  BL: { left: SURROUND_DOWNMIX_COEFF * ITU_CENTER_COEFF, right: 0 },
  BR: { left: 0, right: SURROUND_DOWNMIX_COEFF * ITU_CENTER_COEFF },
};

// Which of a stem's shaped signals (see `createStemSends`) feeds each
// positional speaker — mirrors upmixer/separation/stem_router.py `route()`:
// left/right channels get the raw stem_L/stem_R, C gets the mono downmix,
// surround/back channels get the highpassed+Haas-decorrelated surround
// send, height channels get the elevation-shaped+Haas-decorrelated send.
const CHANNEL_SIGNAL: Record<string, keyof StemSignals> = {
  FL: "left", FR: "right", C: "mono",
  SL: "surroundLeft", SR: "surroundRight", BL: "surroundLeft", BR: "surroundRight",
  TFL: "heightLeft", TFR: "heightRight", TBL: "heightLeft", TBR: "heightRight",
};

const POSITIONAL_CHANNELS = Object.keys(speakerCoordinates);

type StemSignals = {
  left: AudioNode;
  right: AudioNode;
  mono: AudioNode;
  surroundLeft: AudioNode;
  surroundRight: AudioNode;
  heightLeft: AudioNode;
  heightRight: AudioNode;
};

// One fixed virtual loudspeaker: an ambisonic mono encoder pointed at that
// speaker's direction (set once, positions never move) feeding the shared
// HOA bus, gated by a mute gain so a speaker can be silenced independently
// of any stem — the same "render the channel bed, not the objects" model
// Apple's Spatial Audio renderer uses, and it's what makes per-speaker mute
// possible.
type SpeakerBus = {
  muteGain: GainNode;
  // Stable per-channel mastering insert points (never disconnected outside
  // a full graph teardown) — `buildMasteringTopology` wires a fresh
  // EQ -> compressor-gain -> bass chain (or a direct passthrough when
  // mastering is inactive) between them on every rebuild, without touching
  // `encoder`/`stereoSend`/`nativeIndex`'s stable wiring below. Mirrors
  // upmixer/mastering/chain.py running before the binaural/spatial render,
  // not after (see masterIn's connection point vs. `muteGain`).
  masterIn: GainNode;
  masterOut: GainNode;
  encoder: ReturnType<typeof createPositionalEncoder>;
  // Present only for channels the BS.775 stereo downmix uses (excludes
  // height channels) — see STEREO_DOWNMIX_GAINS.
  stereoSend: { gainL: GainNode; gainR: GainNode } | null;
  // This channel's input index on the native discrete ChannelMergerNode, or
  // -1 if the current layout doesn't carry it.
  nativeIndex: number;
};

// One playable source (an ordinary stem, or the dry stereo source anchor).
// `sends` holds one gain node per positional channel this source can reach
// (absent entries send nothing); each feeds straight into that channel's
// `SpeakerBus.muteGain`, so route weights and speaker mute compose for free.
type AudioNodeSet = {
  buffer: AudioBuffer;
  source: AudioBufferSourceNode | null;
  // Stem gain (mute/solo/rebalance/anchor-duck), sits upstream of the
  // splitter so it scales every channel send at once. Anchor has none: its
  // two sends are driven directly by the anchor strength instead.
  stemGain: GainNode | null;
  // Fixed post-EQ insert point `createStemSends` actually reads from — see
  // `buildStemEqChains`, which rebuilds the `stemGain -> [EQ filters] ->
  // postEqGain` chain in between whenever `mix.stem_eq` changes, mirroring
  // upmixer/separation/stem_eq.py's per-stem EQ (applied before routing, on
  // the backend's `all_stems`). Anchor has none: the backend's dry
  // source-anchor blend bypasses stem_eq entirely (it operates on the
  // original zone audio, not `all_stems`).
  postEqGain: GainNode | null;
  sends: Partial<Record<string, GainNode>>;
  // Every node `createStemSends`/anchor setup created, for teardown.
  ownNodes: AudioNode[];
  // LFE send: present for ordinary stems, absent for the dry source anchor
  // (the backend never routes the anchor's dry blend through LFE).
  lfeGain: GainNode | null;
  lfeFilters: [BiquadFilterNode, BiquadFilterNode] | null;
  // Passive level tap for the 3D scene's audio-reactive halos — has no
  // output, so it cannot affect the audible signal. Absent for the dry
  // source anchor (it has no single "stem" to visualize).
  analyser: AnalyserNode | null;
  // One passive analyser per source channel, for the mixer strip's meters: a
  // stereo stem gets two independent bars the way a console shows them, so a
  // one-sided image reads as one-sided instead of being summed away. Fed
  // through `meterSplitter` from the same pre-gain source tap `analyser`
  // uses; also output-less. Absent for the dry source anchor.
  meterSplitter: ChannelSplitterNode | null;
  meterAnalysers: AnalyserNode[];
};

type Timeline = { offset: number; contextTime: number };

// One channel/headphone meter reading: `rms` is the smoothed level the bar
// fill tracks, `peak` is the raw instantaneous sample peak of the most
// recent analyser read (ChannelMeters holds/decays this itself — see its
// `updatePeak`), and `clipped` latches true the first time `peak` reaches
// 0dBFS and stays true until the next `stopSources()` (play/stop/seek).
export type MeterLevel = { rms: number; peak: number; clipped: boolean };

const SILENT_METER_LEVEL: MeterLevel = { rms: 0, peak: 0, clipped: false };

type MixPreview = {
  stem_routing?: Record<string, Record<string, number>>;
  stem_rebalance?: Record<string, number>;
  stem_eq?: Record<string, string>;
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_source_anchor_strength?: number;
};

// Sources share one AudioContext-clock start time so every stem begins on
// the same sample; the lookahead gives the browser time to schedule all
// AudioBufferSourceNode.start() calls before that instant arrives.
const START_LOOKAHEAD_SECONDS = 0.08;

// Short click/startle-free glide for user-facing gain changes (volume, mute,
// output-mode switch, speaker mute) instead of an instant `.gain.value` snap
// straight to headphones. `setTargetAtTime` ramps from whatever the param's
// current value already is, so no cancel/anchor bookkeeping is needed and no
// discontinuity is introduced mid-ramp.
const GAIN_RAMP_TIME_CONSTANT = 0.008;
function rampGainTo(param: AudioParam, target: number, ctx: BaseAudioContext) {
  param.setTargetAtTime(target, ctx.currentTime, GAIN_RAMP_TIME_CONSTANT);
}

// Builds the shaped-signal set (raw L/R, mono downmix, surround send,
// height send) a stem needs to feed the channel bed, and one gain node per
// positional channel wiring the appropriate shaped signal into that
// channel's speaker bus. Mirrors upmixer/separation/stem_router.py
// `route()`'s per-stem signal prep, done once here instead of per output
// channel since several channels share the same shaped signal (e.g. SL and
// BL both consume `surroundLeft`).
function createStemSends(
  ctx: AudioContext,
  input: AudioNode,
  speakerBuses: Map<string, SpeakerBus>,
  channels: string[],
): { sends: Partial<Record<string, GainNode>>; ownNodes: AudioNode[] } {
  const splitter = ctx.createChannelSplitter(2);
  input.connect(splitter);
  const leftTap = ctx.createGain();
  const rightTap = ctx.createGain();
  splitter.connect(leftTap, 0);
  splitter.connect(rightTap, 1);

  const monoSum = ctx.createGain();
  const monoLeftHalf = ctx.createGain();
  monoLeftHalf.gain.value = 0.5;
  const monoRightHalf = ctx.createGain();
  monoRightHalf.gain.value = 0.5;
  leftTap.connect(monoLeftHalf).connect(monoSum);
  rightTap.connect(monoRightHalf).connect(monoSum);

  const surroundLeft = buildSurroundSend(ctx, leftTap, SURROUND_HAAS_MS.left);
  const surroundRight = buildSurroundSend(ctx, rightTap, SURROUND_HAAS_MS.right);
  const heightShapedLeft = buildHeightSend(ctx, leftTap);
  const heightShapedRight = buildHeightSend(ctx, rightTap);
  const heightLeft = buildDiffuseSend(ctx, heightShapedLeft.output, HEIGHT_HAAS_MS.left);
  const heightRight = buildDiffuseSend(ctx, heightShapedRight.output, HEIGHT_HAAS_MS.right);

  const signals: StemSignals = {
    left: leftTap,
    right: rightTap,
    mono: monoSum,
    surroundLeft: surroundLeft.output,
    surroundRight: surroundRight.output,
    heightLeft: heightLeft.output,
    heightRight: heightRight.output,
  };

  const ownNodes: AudioNode[] = [
    splitter, leftTap, rightTap, monoSum, monoLeftHalf, monoRightHalf,
    ...surroundLeft.nodes, ...surroundRight.nodes,
    ...heightShapedLeft.nodes, ...heightShapedRight.nodes,
    ...heightLeft.nodes, ...heightRight.nodes,
  ];

  const sends: Partial<Record<string, GainNode>> = {};
  for (const channel of channels) {
    const bus = speakerBuses.get(channel);
    if (!bus) continue;
    const send = ctx.createGain();
    send.gain.value = 0;
    signals[CHANNEL_SIGNAL[channel]].connect(send);
    send.connect(bus.muteGain);
    sends[channel] = send;
    ownNodes.push(send);
  }

  return { sends, ownNodes };
}

async function loadBuffer(ctx: AudioContext, url: string): Promise<AudioBuffer> {
  const response = await fetch(url);
  if (!response.ok) throw new Error("Preview stem could not be loaded");
  const data = await response.arrayBuffer();
  return ctx.decodeAudioData(data);
}

// How long to let real post-collapse audio accumulate in `mergePointAnalyser`
// before reading it for the one-shot loudness correction below — see
// `measureOutputLoudness`.
const LOUDNESS_MEASURE_DELAY_FRAMES = 55;

// Re-sample cadence (in rAF ticks, ~60fps) for `measureOutputLoudness`'s
// ongoing true-peak tracking once the initial one-shot snapshot has landed.
const PEAK_TRACK_FRAME_STRIDE = 6;

// Safety cap for `runLoudnessWarmup`, in case a silent or very short stem
// never fills `mergePointAnalyser` with signal loud enough for
// `measureOutputLoudness` to settle — bounds the muted warm-up so play can
// never hang, at the cost of falling back to `measuredLkfs`'s unity-gain
// default (today's behavior) for that one play.
const LOUDNESS_WARMUP_MAX_FRAMES = LOUDNESS_MEASURE_DELAY_FRAMES + 30;

// ~60fps, matching the cadence LOUDNESS_MEASURE_DELAY_FRAMES/
// LOUDNESS_WARMUP_MAX_FRAMES were tuned against — see `runLoudnessWarmup`.
const WARMUP_STEP_MS = 16;

function loudnessGainFor(measuredLkfs: number, targetLkfs: number, maxGainDb: number = LOUDNESS_MAX_GAIN_DB): number {
  if (measuredLkfs <= -70) return 1;
  const gainDb = Math.min(targetLkfs - measuredLkfs, maxGainDb);
  return 10 ** (gainDb / 20);
}

// A sample must clear unity by a real margin, not merely reach or nudge
// past it, to count as a clip. `buildSoftLimitCurve`'s threshold+margin
// sums to exactly 1.0 (its designed asymptote — see masteringProfiles.ts),
// so any moderately hot transient saturates the limiter's tanh curve close
// enough to 1.0 that float32 rounds the sample to bit-exact 1.0 (the
// limiter doing its job at its own ceiling, not an over). Separately, the
// live WaveShaperNode's `oversample: "4x"` reconstruction filter measurably
// rings past the curve's own bound at that same knee — observed peaks of
// ~1.005-1.006 (~+0.04-0.05dB) on ordinary loud passages, a well-known
// WaveShaperNode oversampling artifact, not audible content. `CLIP_TOLERANCE`
// gives ~8x headroom over that observed ripple while still catching a
// genuine over (something actually bypassing the limiter, off by much more
// than a hundredth of a dB).
const CLIP_TOLERANCE = 10 ** (0.5 / 20); // +0.5dB
function isClippedPeak(peak: number): boolean {
  return peak > CLIP_TOLERANCE;
}

/** Second-stage gain reduction mirroring `normalize_loudness`'s
 * `max_tp_dbtp` correction (`upmixer/loudness.py`): given the gain
 * `loudnessGainFor` above already computed, reduce it further if applying
 * it would push the measured pre-gain true peak (`preGainTpDbtp`) over
 * `maxTpDbtp`. Returns the final gain to apply (folds `loudnessGain` in,
 * not just the extra reduction) — a no-op (`loudnessGain` unchanged) when
 * already under the ceiling. Exported (pure, no AudioContext) so this
 * exact formula is unit-testable without a live graph. */
export function applyTruePeakCeiling(preGainTpDbtp: number, loudnessGain: number, maxTpDbtp: number): number {
  const postGainTpDbtp = preGainTpDbtp + 20 * Math.log10(loudnessGain);
  if (postGainTpDbtp <= maxTpDbtp) return loudnessGain;
  return loudnessGain * 10 ** ((maxTpDbtp - postGainTpDbtp) / 20);
}

export function useStemPreview(
  stems: ProjectStem[],
  scene: { stems?: StemScene },
  mix?: MixPreview,
  sourcePreviewUrl: string | null = null,
  mastering?: MasterPreview,
  // Full channel set (including LFE) of the project's selected speaker
  // layout — defaults to every positional channel for callers (e.g. tests)
  // that don't care about layout-scoping the preview's speaker bed.
  layoutChannels: string[] = POSITIONAL_CHANNELS,
  // Which final render stage the channel bed feeds. Ephemeral, session-only
  // choice (not part of the project manifest) — switching it re-routes the
  // already-built graph rather than re-decoding stems, see `applyOutputMode`.
  outputMode: OutputMode = "binaural",
  // Spatial Audio Engine profile (Studio/Listening/Flat) — selects the
  // decode filter set and voicing chain, see docs/standards/
  // spatial_audio_engine.md. Session-only, like outputMode.
  spatialProfile: SpatialProfile = "studio",
) {
  const layoutChannelsKey = layoutChannels.join(",");
  const layoutChannelsRef = React.useRef(layoutChannels);
  layoutChannelsRef.current = layoutChannels;
  const outputModeRef = React.useRef(outputMode);
  outputModeRef.current = outputMode;
  const spatialProfileRef = React.useRef(spatialProfile);
  spatialProfileRef.current = spatialProfile;
  // Read-only inside `initialize()` for the native limiter worklet's ceiling
  // (not a live-editable manifest field today, see that construction site) —
  // a ref so reading it doesn't force `initialize` to depend on `mastering`
  // and rebuild the whole graph on every unrelated mastering-panel edit.
  const masteringRef = React.useRef(mastering);
  masteringRef.current = mastering;
  // Stable-identity, layout-scoped speaker list: this is what actually
  // drives the ambisonic speaker-bus construction below, replacing the old
  // hardcoded `POSITIONAL_CHANNELS` so switching e.g. 7.1.4 -> 5.1 tears
  // down and rebuilds only the speakers the chosen layout actually has.
  const positionalChannels = React.useMemo(
    () => layoutChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel]),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on layoutChannelsKey, not `layoutChannels` (fresh array identity every render)
    [layoutChannelsKey],
  );
  const positionalChannelsRef = React.useRef(positionalChannels);
  positionalChannelsRef.current = positionalChannels;
  const context = React.useRef<AudioContext | null>(null);
  // PROGRAM domain: what a bounce of this graph would contain. `master`
  // carries only `tpSafeGain` (the true-peak-safe loudness correction) —
  // never the user's monitor volume. See `monitorGain` below for the split
  // and `apply()` for where each is set.
  const master = React.useRef<GainNode | null>(null);
  const softLimit = React.useRef<WaveShaperNode | null>(null);
  // MONITOR domain: the user's Transport volume/mute, applied strictly
  // after `softLimit`. Placing it after the limiter (not folded into
  // `master` the way it used to be) means raising the slider can never
  // drive the limiter harder or change its engagement — it only changes
  // what reaches the speakers/headphones, exactly like an interface's
  // monitor knob sitting downstream of a DAW's program fader (see the
  // "Master Fader is Not for Monitor Control" rationale linked from the
  // plan this implements). The channel/headphone meter taps sit on
  // `softLimit`'s output, upstream of this node, so the meters never move
  // with the volume slider either.
  const monitorGain = React.useRef<GainNode | null>(null);
  // The ambisonic rendering core: every positional speaker's encoder feeds
  // `hoaBus` (a plain summing gain, explicit/discrete at 16 channels so
  // multiple encoders' 16-channel outputs add channel-for-channel), which
  // passes through `rotator` (identity — yaw/pitch/roll 0, kept for future
  // head-tracking) into `binDecoder`, which renders the whole channel bed to
  // stereo using the loaded HRIR set. This is the "virtual loudspeaker"
  // model: the renderer sees speaker feeds, not per-stem objects, matching
  // what StemUpmixPipeline actually delivers and letting a speaker be muted
  // independently of any stem. `preMasterBus`/`lfeBus`/`mergePoint` sum the
  // binaural render with the LFE bypass ahead of the soft-limiter.
  // `preMasterBus` is a plain passthrough now — mastering (EQ/comp/bass)
  // runs earlier, on the discrete bed (`SpeakerBus.masterIn`/`masterOut`,
  // see `buildMasteringTopology`), matching upmixer/pipeline.py's order:
  // MasteringChain runs on the mixed bed BEFORE render_binaural_delivery,
  // not on the collapsed binaural stereo.
  const hoaBus = React.useRef<GainNode | null>(null);
  // Decode stage: one ConvolverNode pair (L, R) per ACN channel — the bank
  // convolution docs/standards/spatial_audio_engine.md §4 specifies, in
  // place of a third-party ambisonic decoder, so the exact same filter
  // files (fetched from /hrir/) drive both this preview and the core render.
  const decodeConvolvers = React.useRef<{ left: ConvolverNode; right: ConvolverNode; preGain: GainNode | null }[]>([]);
  const voicingChain = React.useRef<VoicingChain | null>(null);
  // Post-voicing stereo output of `buildBinauralGraph` (previewGraph.ts) —
  // feeds `binauralGate`. LFE connects directly into this same node's two
  // inputs (a ChannelMergerNode sums same-index sources), see
  // `applySpeakerMute`'s sibling wiring in `initialize()`.
  const voicingMerger = React.useRef<ChannelMergerNode | null>(null);
  // Every other node `buildBinauralGraph` created (hoaSplitter, decode sum
  // gains, decode merger, voicing splitter/taps, ACN12 pre-gain) — not
  // individually referenced elsewhere, so one array covers their teardown
  // in `reset()` instead of a dedicated ref per node.
  const binauralGraphNodes = React.useRef<AudioNode[]>([]);
  const speakerBuses = React.useRef<Map<string, SpeakerBus>>(new Map());
  const preMasterBus = React.useRef<GainNode | null>(null);
  // Linked bus-compressor detector: every channel's post-EQ signal sums in
  // here (rebuilt each `buildMasteringTopology` pass), feeding one shared
  // `DynamicsCompressorNode` used purely as a sidechain — its native
  // channelCount cannot exceed 2, so it can't process the discrete bed
  // directly, but reading its live `.reduction` (polled in `tick()`) and
  // applying that as a shared gain to every channel's own `compGain` node
  // reproduces the backend's linked-sidechain bus compressor
  // (upmixer/mastering/compressor.py) without an AudioWorklet. `sidechainSink`
  // is a permanent zero-gain tap into `mergePoint` so the detector node stays
  // part of the actively rendered graph (a compressor with no path to the
  // destination may not reliably keep processing/updating `.reduction`).
  const sidechainSum = React.useRef<GainNode | null>(null);
  const sidechainSink = React.useRef<GainNode | null>(null);
  const sidechainCompressor = React.useRef<DynamicsCompressorNode | null>(null);
  const compGains = React.useRef<GainNode[]>([]);
  // Static makeup gain (linear) folded into every per-tick reduction-gain
  // update — see `buildMasteringTopology`'s compressor block and the
  // `sidechainCompressor.current.reduction` poll in `tick()`.
  const compMakeupGain = React.useRef(1);
  const lfeBus = React.useRef<GainNode | null>(null);
  // Stable LFE mastering insert points, same role as `SpeakerBus.masterIn`/
  // `masterOut` for the positional bed — permanently wired `lfeBus ->
  // lfeMasterIn` and `lfeMasterOut -> lfeMuteGain` in `initialize()`;
  // `buildMasteringTopology` rewires only the bridge between them on every
  // mastering-config change (LFE reference-match — see that function's LFE
  // block). upmixer/mastering/match_reference.py does NOT bypass LFE (unlike
  // named-profile EQ), so this is the one mastering stage LFE needs.
  const lfeMasterIn = React.useRef<GainNode | null>(null);
  const lfeMasterOut = React.useRef<GainNode | null>(null);
  // Gates the LFE bus independently of any stem — same per-speaker mute idea
  // as `SpeakerBus.muteGain`, but LFE has no ambisonic encoder (it bypasses
  // the binaural render entirely), so it needs its own gate on the way into
  // `mergePoint`. Keyed into the same `speakerEnabled` map under "LFE".
  const lfeMuteGain = React.useRef<GainNode | null>(null);
  const mergePoint = React.useRef<GainNode | null>(null);
  // Passive per-channel level taps for the UI's vertical meters — one
  // analyser per positional speaker bus plus "LFE", fed from `masterOut`
  // (post-mastering, same point feeding the ambisonic encoders) and the LFE
  // bypass, so a meter reflects the actual signal reaching the spatial
  // engine (including mute and mastering).
  const channelAnalysers = React.useRef<Map<string, AnalyserNode>>(new Map());
  // Headphone L/R tap: a splitter on the final output node, i.e. the actual
  // binaural signal reaching the listener's headphones, post-mastering.
  const headphoneAnalysers = React.useRef<{ splitter: ChannelSplitterNode; left: AnalyserNode; right: AnalyserNode } | null>(null);
  // Stereo-downmix bus (BS.775) and discrete native-channel bus, built
  // alongside the binaural bus so switching `outputMode` only re-routes
  // which one reaches `ctx.destination` (see `applyOutputMode`) instead of
  // tearing down and re-decoding the whole graph.
  const stereoMerger = React.useRef<ChannelMergerNode | null>(null);
  const nativeMerger = React.useRef<ChannelMergerNode | null>(null);
  const binauralGate = React.useRef<GainNode | null>(null);
  const stereoGate = React.useRef<GainNode | null>(null);
  // PROGRAM domain, native path — kept at unity by `apply()` (native has no
  // loudness correction of its own to apply, see that function), same role
  // as `master` above.
  const nativeOutputGain = React.useRef<GainNode | null>(null);
  // Look-ahead true-peak limiter on the native discrete path — mirrors
  // upmixer/mastering/limiter.py::LookAheadLimiter (the bed-level limiter
  // `MasteringChain` now runs), via the "limiter-processor" AudioWorklet
  // (`web/public/limiter.worklet.js`); native otherwise bypasses
  // `master`/`softLimit` entirely and would reach `ctx.destination` with no
  // limiting at all. Falls back to the plain tanh `WaveShaperNode` (same as
  // `softLimit`) if the worklet module fails to load — see `initialize()`.
  const nativeSoftLimit = React.useRef<AudioWorkletNode | WaveShaperNode | null>(null);
  // MONITOR domain, native path — mirrors `monitorGain` above. Explicit
  // channelCount/channelCountMode/channelInterpretation (set at creation,
  // see `initialize()`) because a bare GainNode defaults to "max"/"speakers"
  // and would fold the discrete N-channel native bus down to stereo; this
  // node must pass every channel through untouched.
  const nativeMonitorGain = React.useRef<GainNode | null>(null);
  const nativeChannelCount = React.useRef(0);
  const [maxChannels, setMaxChannels] = React.useState(2);
  const [outputDevices, setOutputDevices] = React.useState<MediaDeviceInfo[]>([]);
  const [outputDeviceId, setOutputDeviceIdState] = React.useState("");
  const masteringNodes = React.useRef<AudioNode[]>([]);
  // Current per-stem EQ filter chain nodes (stem id -> nodes), so
  // `buildStemEqChains` can disconnect exactly its own prior chain on
  // rebuild without touching the fixed `stemGain`/`postEqGain` nodes.
  const stemEqNodes = React.useRef<Map<string, AudioNode[]>>(new Map());
  // Decoded EQ FIR asset cache (asset name -> pending/loaded AudioBuffer),
  // keyed independently of profile scope (master vs stem asset names never
  // collide, see EQ_FIR_ASSETS/STEM_EQ_FIR_ASSETS) so the same profile
  // reused across many stems or across a rebuild fetches/decodes once. Tied
  // to the single AudioContext this hook creates once per mount (see
  // `initialize`) — never needs invalidating within that lifetime.
  const firEqBufferCache = React.useRef<Map<string, Promise<AudioBuffer>>>(new Map());
  // Same per-context cache lifetime as `firEqBufferCache`, keyed by
  // `fir_url` instead of a profile name (see `loadCachedRefMatchBuffers`) —
  // the URL carries the asset's `?v=<signature>` query param (see
  // `_project_view` in upmixer_web/api.py), so a genuine server recompute
  // naturally busts this cache instead of serving a stale FIR.
  const refMatchBufferCache = React.useRef<Map<string, Promise<Map<string, AudioBuffer>>>>(new Map());
  // Same per-context cache lifetime, keyed by decode filter set name — see
  // loadCachedDecodeFilterChannels. Not cleared in reset(): the profile's
  // decoded Float32Arrays stay valid across a graph rebuild within the same
  // AudioContext.
  const decodeFilterCache = React.useRef<Map<string, Promise<Float32Array[]>>>(new Map());
  // Profile currently assigned onto the live convolvers, so loadDecodeFilterSet
  // can skip a redundant assignDecodeFilterBuffers call (32 buffer copies +
  // reassignments) when re-invoked with the profile already in place.
  const assignedDecodeProfile = React.useRef<SpatialProfile | null>(null);
  const resolvedBass = React.useRef<{ active: boolean; lfeGainDb: number }>({ active: false, lfeGainDb: 0 });
  // Lets `measureOutputLoudness` (defined before `apply`, called from `tick`)
  // invoke the always-current `apply` without needing it in a dependency
  // array at a point in the file where `apply` isn't declared yet.
  const applyRef = React.useRef<() => void>(() => {});
  const measuredLkfs = React.useRef(-70);
  // Same one-shot measurement window as `measuredLkfs`, but true peak
  // (dBTP) instead of loudness — feeds `apply()`'s true-peak safety net, the
  // preview-side mirror of `normalize_loudness`'s `max_tp_dbtp` gain
  // reduction (see that function's comment in `apply()`).
  const preGainTpDbtp = React.useRef(-70);
  // Pure meter tap on `mergePointNode` — the actual post-mastering,
  // post-binaural-collapse (or post-stereo-downmix) signal, immediately
  // before `master`'s loudness/volume gain is applied. `measureOutputLoudness`
  // reads this once real audio has had time to fill its ring buffer, so
  // `measuredLkfs` reflects what's actually coming out of the graph instead
  // of the dry, unprocessed stem source material.
  const mergePointAnalyser = React.useRef<AnalyserNode | null>(null);
  const loudnessMeasureBuf = React.useRef<Float32Array | null>(null);
  const loudnessMeasureState = React.useRef({ framesElapsed: 0, done: false });
  // Forces every audible output path to silence during the first-play
  // loudness warm-up (see `runLoudnessWarmup`) regardless of what `apply()`
  // would otherwise compute — covers both `master` (binaural/stereo) and
  // `nativeOutputGain` (native bypasses `master` entirely, see
  // `applyOutputMode`), so no output mode can leak the pre-correction level.
  const warmupMuted = React.useRef(false);
  const nodes = React.useRef<Map<string, AudioNodeSet>>(new Map());
  // Live per-stem spectrum (base stem name -> level/centroid) for the Haze
  // view's glowing per-stem clouds. A ref, not state — updated every
  // animation frame from `tick()`; consumers should read it in their own
  // render loop (e.g. a canvas rAF loop) rather than subscribing.
  // `level`: smoothed 0..1 RMS, already scaled by the stem's
  // currently-applied gain so mute/solo/rebalance are reflected.
  // `centroid`: 0 (bass-weighted spectrum) .. 1 (treble-weighted spectrum),
  // the Haze view's radial "inner = bass, outer = treble" axis.
  const stemSpectrum = React.useRef<Map<string, { level: number; centroid: number }>>(new Map());
  // Live per-channel meter levels (speaker code, incl. "LFE" -> smoothed
  // RMS/peak/clip) and the headphone L/R levels of the final binaural
  // output. Refs, not state — updated every animation frame from `tick()`;
  // the meter component reads these in its own rAF loop, same as
  // `stemSpectrum`.
  const channelLevels = React.useRef<Map<string, MeterLevel>>(new Map());
  // Per-stem meter levels for the mixer strips, keyed by base stem name, one
  // entry per source channel (1 for mono, 2 for stereo) so a strip can show
  // the same number of bars a console would. Distinct from
  // `stemSpectrum.level`, which is a clamped, display-scaled glow value: a
  // fader's meter has to keep reading above unity.
  const stemLevels = React.useRef<Map<string, MeterLevel[]>>(new Map());
  const headphoneLevels = React.useRef<{ left: MeterLevel; right: MeterLevel }>({
    left: SILENT_METER_LEVEL,
    right: SILENT_METER_LEVEL,
  });
  // Float time-domain scratch buffer for `measureAnalyser` (channel/
  // headphone meters) — separate from `timeDomainBuffer` below, which stays
  // 8-bit for the Haze view's per-stem `measureLevels` (cheaper, and its
  // display glow doesn't need real dBFS resolution).
  const channelTimeDomainBuffer = React.useRef<Float32Array | null>(null);
  const appliedGain = React.useRef<Map<string, number>>(new Map());
  const timeDomainBuffer = React.useRef<Uint8Array | null>(null);
  const frequencyBuffer = React.useRef<Uint8Array | null>(null);
  const stemsRef = React.useRef(stems);
  const timeline = React.useRef<Timeline | null>(null);
  const currentTimeRef = React.useRef(0);
  const durationRef = React.useRef(0);
  const playingRef = React.useRef(false);
  const scrub = React.useRef<{ wasPlaying: boolean } | null>(null);
  const animationFrame = React.useRef<number | null>(null);
  const loopRef = React.useRef(false);
  const initPromise = React.useRef<Promise<void> | null>(null);
  const [playing, setPlaying] = React.useState(false);
  const [currentTime, setCurrentTime] = React.useState(0);
  const [duration, setDuration] = React.useState(0);
  // Default to unity (fader position 1.0): with the PROGRAM/MONITOR split
  // above, unity means "hear the render exactly as it would export" — see
  // `faderPositionToGain` in lib/fader.ts. There is no headroom above it to
  // give up by defaulting lower.
  const [volume, setVolume] = React.useState(1);
  // Master mute — independent of `volume` so unmuting restores the exact
  // prior level instead of whatever a slider drag left it at.
  const [muted, setMuted] = React.useState(false);
  const [loop, setLoop] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [ready, setReady] = React.useState(false);
  const [loadProgress, setLoadProgress] = React.useState(0);
  // True only for the brief first-play loudness warm-up (see
  // `runLoudnessWarmup`) — surfaced so the UI can show a "calibrating" status
  // in place of the transport during that window instead of looking stalled.
  const [measuring, setMeasuring] = React.useState(false);
  const [supported] = React.useState(() => Boolean(window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext));
  // Per-speaker mute state — independent of stem mute/solo, since the
  // renderer input is the channel bed, not the stems (see `SpeakerBus`).
  // "LFE" is included even though it has no ambisonic bus (see `lfeMuteGain`).
  const [speakerEnabled, setSpeakerEnabled] = React.useState<Record<string, boolean>>(
    () => Object.fromEntries([...positionalChannels, "LFE"].map((channel) => [channel, true])),
  );
  // Layout changed (not just the initial mount): drop mute state for
  // speakers the new layout no longer has and default any newly-added ones
  // to enabled, rather than carrying stale entries across layouts.
  const previousLayoutKey = React.useRef(layoutChannelsKey);
  React.useEffect(() => {
    if (previousLayoutKey.current === layoutChannelsKey) return;
    previousLayoutKey.current = layoutChannelsKey;
    setSpeakerEnabled(Object.fromEntries([...positionalChannels, "LFE"].map((channel) => [channel, true])));
  }, [layoutChannelsKey, positionalChannels]);
  const key = `${stems.map((stem) => `${stem.id}:${stem.preview_url || stem.audio_url}`).join("|")}|${sourcePreviewUrl || ""}|${layoutChannelsKey}`;
  // Value-stable key: `mastering` is a fresh object every render (the project
  // page rebuilds its manifest on every edit, including unrelated mixing
  // edits), but the mastering audio graph only needs rebuilding when the
  // resolved values actually change.
  const masteringKey = JSON.stringify(mastering ?? null);
  // Same value-stable-key trick as `masteringKey`, for `mix.stem_eq`.
  const stemEqKey = JSON.stringify(mix?.stem_eq ?? null);
  stemsRef.current = stems;

  const expectedTime = React.useCallback(() => {
    const activeTimeline = timeline.current;
    const ctx = context.current;
    if (!activeTimeline || !ctx) return currentTimeRef.current;
    const elapsed = activeTimeline.offset + (ctx.currentTime - activeTimeline.contextTime);
    const durationValue = durationRef.current;
    if (loopRef.current && durationValue > 0) {
      const wrapped = elapsed % durationValue;
      return wrapped < 0 ? 0 : wrapped;
    }
    return Math.max(0, durationValue > 0 ? Math.min(durationValue, elapsed) : elapsed);
  }, []);

  const stopTicker = React.useCallback(() => {
    if (animationFrame.current !== null) window.cancelAnimationFrame(animationFrame.current);
    animationFrame.current = null;
  }, []);

  const stopSources = React.useCallback(() => {
    nodes.current.forEach((node) => {
      if (!node.source) return;
      try {
        node.source.stop();
      } catch {
        // already stopped/ended
      }
      node.source.disconnect();
      node.source = null;
    });
    stemSpectrum.current.clear();
    stemLevels.current.clear();
    // Bus-tap analysers stop receiving signal the instant sources are torn
    // down, but the smoothed level refs they feed (see `measureChannelLevels`)
    // don't decay on their own without a running tick — clear them here so
    // the meters drop to zero on pause/stop instead of freezing at the last
    // sample, and so each play/stop/seek resets the latched clip indicator.
    // Peak-hold markers are a separate ref owned by ChannelMeters and still
    // decay normally.
    channelLevels.current.clear();
    headphoneLevels.current = { left: SILENT_METER_LEVEL, right: SILENT_METER_LEVEL };
  }, []);

  // Schedules every stem/anchor buffer source to start at `target`, shared by
  // the audible start in `playFrom` and the muted warm-up in
  // `runLoudnessWarmup` — identical scheduling either way, so the warm-up's
  // measurement sees the same signal an audible start would produce. Returns
  // the `AudioContext` time sources were scheduled to start at, or `null` if
  // there's no context to schedule against.
  const startSourcesAt = React.useCallback((target: number) => {
    const ctx = context.current;
    if (!ctx) return null;
    const startAt = ctx.currentTime + START_LOOKAHEAD_SECONDS;
    nodes.current.forEach((node) => {
      const source = ctx.createBufferSource();
      source.buffer = node.buffer;
      if (loopRef.current && durationRef.current > 0) {
        source.loop = true;
        source.loopStart = 0;
        source.loopEnd = durationRef.current;
      }
      // `ownNodes[0]` is always the stem/anchor's input gain (`stemGain`
      // for stems, a dedicated dry input for the anchor) — see
      // `createStemSends`'s caller above.
      const input = node.ownNodes[0];
      if (input) source.connect(input);
      if (node.lfeGain) source.connect(node.lfeGain);
      if (node.analyser) source.connect(node.analyser);
      if (node.meterSplitter) source.connect(node.meterSplitter);
      source.start(startAt, target);
      node.source = source;
    });
    return startAt;
  }, []);

  // Bus-tap RMS is unscaled by any applied gain (unlike `measureLevels`, the
  // stem path), so smooth it directly to keep meter bars from flickering.
  // Reads float time-domain data (not the 8-bit `getByteTimeDomainData`
  // `measureLevels` below uses for the Haze view — that quantization floor
  // sits around -48dBFS per sample, fine for a glow, not for a level meter
  // that needs to report a genuine clip), with no display-scale fudge, so
  // both `rms` and the raw instantaneous sample `peak` are true 0..1
  // amplitude fractions. ChannelMeters converts to dB and holds/decays the
  // peak itself (see its `updatePeak`).
  const measureAnalyser = React.useCallback((analyser: AnalyserNode): { rms: number; peak: number } => {
    const size = analyser.fftSize;
    if (!channelTimeDomainBuffer.current || channelTimeDomainBuffer.current.length !== size) {
      channelTimeDomainBuffer.current = new Float32Array(size);
    }
    const buf = channelTimeDomainBuffer.current;
    analyser.getFloatTimeDomainData(buf);
    let sumSquares = 0;
    let peak = 0;
    for (let i = 0; i < size; i++) {
      const sample = buf[i];
      sumSquares += sample * sample;
      const abs = Math.abs(sample);
      if (abs > peak) peak = abs;
    }
    return { rms: Math.sqrt(sumSquares / size), peak };
  }, []);

  const measureLevels = React.useCallback(() => {
    for (const stem of stemsRef.current) {
      const node = nodes.current.get(stem.id);
      if (!node?.analyser) continue;
      const base = stem.stem_key.split("@", 1)[0];
      // Mixer-strip meter: a genuine 0..1 amplitude from the float tap,
      // scaled by the stem's applied gain so the bar tracks the fader. The
      // byte read below stays as-is for the Haze glow — it is display-scaled
      // and clamped, which a level meter cannot be. `clipped` is always false
      // for the same reason `measureChannelLevels` never latches: this tap is
      // pre-mastering, where exceeding 1.0 is ordinary headroom use.
      const gainForMeter = appliedGain.current.get(base) ?? 1;
      const previousLevels = stemLevels.current.get(base);
      stemLevels.current.set(base, node.meterAnalysers.map((channelAnalyser, channel) => {
        const measured = measureAnalyser(channelAnalyser);
        return {
          rms: (previousLevels?.[channel]?.rms ?? 0) * 0.7 + measured.rms * gainForMeter * 0.3,
          peak: measured.peak * gainForMeter,
          clipped: false,
        };
      }));
      const size = node.analyser.fftSize;
      if (!timeDomainBuffer.current || timeDomainBuffer.current.length !== size) {
        timeDomainBuffer.current = new Uint8Array(size);
      }
      node.analyser.getByteTimeDomainData(timeDomainBuffer.current);
      let sumSquares = 0;
      for (let i = 0; i < size; i++) {
        const deviation = (timeDomainBuffer.current[i] - 128) / 128;
        sumSquares += deviation * deviation;
      }
      const rms = Math.sqrt(sumSquares / size);
      const gain = appliedGain.current.get(base) ?? 1;
      const level = Math.min(1, rms * gain * 2.5);

      const binCount = node.analyser.frequencyBinCount;
      if (!frequencyBuffer.current || frequencyBuffer.current.length !== binCount) {
        frequencyBuffer.current = new Uint8Array(binCount);
      }
      node.analyser.getByteFrequencyData(frequencyBuffer.current);
      let weightedBin = 0;
      let totalAmplitude = 0;
      for (let i = 0; i < binCount; i++) {
        const amplitude = frequencyBuffer.current[i];
        weightedBin += amplitude * i;
        totalAmplitude += amplitude;
      }
      // Linear bin index is frequency-linear, which crams almost all musical
      // energy into the first few bins; sqrt spreads the centroid out across
      // the radar's radius instead of pinning everything near the center.
      const centroidBin = totalAmplitude > 0 ? weightedBin / totalAmplitude : 0;
      const centroid = binCount > 1 ? Math.sqrt(centroidBin / (binCount - 1)) : 0;
      stemSpectrum.current.set(base, { level, centroid });
    }
  }, [measureAnalyser]);

  const measureChannelLevels = React.useCallback(() => {
    channelAnalysers.current.forEach((analyser, channel) => {
      const { rms, peak } = measureAnalyser(analyser);
      const previous = channelLevels.current.get(channel);
      channelLevels.current.set(channel, {
        rms: (previous?.rms ?? 0) * 0.7 + rms * 0.3,
        peak,
        // Never latched here: this tap is `masterOut`, pre-binaural-render
        // and upstream of `softLimit` entirely (see `channelAnalysers`'
        // declaration). A post-mastering channel legitimately exceeding
        // 1.0 pre-render is ordinary headroom use — EQ boost, compressor
        // makeup gain, bass shelf — exactly what the downstream limiter
        // exists to catch. "Clipped" is only meaningful at the true final-
        // output tap (`headphoneLevels`, post-`softLimit`) below.
        clipped: false,
      });
    });
    const headphones = headphoneAnalysers.current;
    if (headphones) {
      const left = measureAnalyser(headphones.left);
      const right = measureAnalyser(headphones.right);
      const previous = headphoneLevels.current;
      headphoneLevels.current = {
        left: {
          rms: previous.left.rms * 0.7 + left.rms * 0.3,
          peak: left.peak,
          clipped: previous.left.clipped || isClippedPeak(left.peak),
        },
        right: {
          rms: previous.right.rms * 0.7 + right.rms * 0.3,
          peak: right.peak,
          clipped: previous.right.clipped || isClippedPeak(right.peak),
        },
      };
    }
  }, [measureAnalyser]);

  // Applies the shared bus-compressor's live gain reduction to every
  // channel's `compGain` node — see `buildMasteringTopology`'s comment on
  // why a sidechain-detector + polled `.reduction` stands in for a true
  // linked multichannel compressor. `.reduction` is always <= 0 dB.
  const applyCompressorReduction = React.useCallback(() => {
    const comp = sidechainCompressor.current;
    if (!comp || compGains.current.length === 0) return;
    const gain = 10 ** (comp.reduction / 20) * compMakeupGain.current;
    for (const node of compGains.current) node.gain.value = gain;
  }, []);

  // Loudness correction for the *actual* output signal — reads
  // `mergePointAnalyser` (tapped on `mergePointNode`, the real post-mastering/
  // post-binaural-collapse or post-stereo-downmix signal, immediately before
  // `master`'s gain) once enough playback has elapsed for its ring buffer to
  // hold real audio instead of the graph's initial silence. Mirrors
  // `normalize_loudness` measuring the actual rendered signal on the backend
  // (`upmixer/loudness.py`, `upmixer/binaural/renderer.py`) instead of the
  // dry, unprocessed stem source material.
  //
  // The LKFS target-matching gain is one-shot, same as the offline
  // normalize — re-deriving it continuously from a ~0.7s window would ride
  // program dynamics and audibly pump. But `preGainTpDbtp` (the *safety*
  // ceiling `applyTruePeakCeiling` clamps against) cannot stay one-shot: an
  // early snapshot commonly lands on a quiet intro, under-measuring the
  // track's real peak — the frozen gain then goes uncorrected straight
  // through a later chorus/drop and drives the soft-limit WaveShaper hard
  // enough to audibly distort (both stereo mixdown and binaural — same
  // shared gain stage, see docs/standards/spatial_audio_engine.md's gain-
  // staging note). So after the initial snapshot, keep sampling the true
  // peak and ratchet `preGainTpDbtp` up (gain down) whenever a louder peak
  // is observed — never the other way, so the ceiling only ever gets
  // stricter as more of the track is heard, the same direction a look-ahead
  // limiter's held ceiling would move.
  const measureOutputLoudness = React.useCallback(() => {
    const state = loudnessMeasureState.current;
    state.framesElapsed += 1;
    if (state.framesElapsed < LOUDNESS_MEASURE_DELAY_FRAMES) return;
    const analyser = mergePointAnalyser.current;
    const buf = loudnessMeasureBuf.current;
    if (!analyser || !buf) return;
    if (!state.done) {
      analyser.getFloatTimeDomainData(buf);
      let sumSquares = 0;
      for (let i = 0; i < buf.length; i++) sumSquares += buf[i] * buf[i];
      const meanSquare = sumSquares / buf.length;
      // Same -0.691 dB offset / ungated mean-square approximation the removed
      // measureApproxLkfs used — good enough to steer this correction gain
      // toward the mastering target, not to reproduce the exact delivered LKFS.
      measuredLkfs.current = meanSquare > 0 ? -0.691 + 10 * Math.log10(meanSquare) : -70;
      preGainTpDbtp.current = measureBufferTruePeakDbtp(buf);
      state.done = true;
      applyRef.current();
      return;
    }
    // Throttled re-sampling (~10Hz, not every rAF tick) — the 4x upsample
    // in measureBufferTruePeakDbtp isn't free, and catching a sustained
    // loud section within ~100ms is already far tighter than needed.
    if (state.framesElapsed % PEAK_TRACK_FRAME_STRIDE !== 0) return;
    analyser.getFloatTimeDomainData(buf);
    const peakDbtp = measureBufferTruePeakDbtp(buf);
    if (peakDbtp > preGainTpDbtp.current) {
      preGainTpDbtp.current = peakDbtp;
      applyRef.current();
    }
  }, []);

  const tick = React.useCallback(() => {
    if (!playingRef.current) return;
    const nextTime = expectedTime();
    // Deliberately not `setCurrentTime` here: this runs every animation
    // frame during playback, and a page-wide re-render at 60fps starved
    // everything downstream (canvas draw loops, even CSS :hover repaints).
    // Live playback position is exposed via `currentTimeRef`; `Transport`
    // reads it directly in its own small rAF loop instead of subscribing to
    // state. `currentTime` state still updates on every discrete transition
    // (pause/stop/seek/end-of-track below) so paused/idle consumers stay
    // correct without any live polling.
    currentTimeRef.current = nextTime;
    measureLevels();
    measureChannelLevels();
    applyCompressorReduction();
    measureOutputLoudness();
    if (!loopRef.current && durationRef.current > 0 && nextTime >= durationRef.current) {
      stopSources();
      timeline.current = null;
      playingRef.current = false;
      currentTimeRef.current = durationRef.current;
      setCurrentTime(durationRef.current);
      setPlaying(false);
      return;
    }
    animationFrame.current = window.requestAnimationFrame(tick);
  }, [expectedTime, measureLevels, measureChannelLevels, applyCompressorReduction, measureOutputLoudness, stopSources]);

  const startTicker = React.useCallback(() => {
    stopTicker();
    animationFrame.current = window.requestAnimationFrame(tick);
  }, [stopTicker, tick]);

  const pause = React.useCallback(() => {
    const position = expectedTime();
    stopTicker();
    stopSources();
    timeline.current = null;
    currentTimeRef.current = position;
    playingRef.current = false;
    setCurrentTime(position);
    setPlaying(false);
  }, [expectedTime, stopSources, stopTicker]);

  const reset = React.useCallback(() => {
    stopTicker();
    stopSources();
    nodes.current.forEach((node) => {
      node.stemGain?.disconnect();
      node.ownNodes.forEach((audioNode) => audioNode.disconnect());
      node.lfeGain?.disconnect();
      node.lfeFilters?.forEach((filter) => filter.disconnect());
      node.analyser?.disconnect();
      node.meterSplitter?.disconnect();
      node.meterAnalysers.forEach((analyser) => analyser.disconnect());
    });
    nodes.current.clear();
    stemEqNodes.current.clear();
    stemSpectrum.current.clear();
    stemLevels.current.clear();
    appliedGain.current.clear();
    masteringNodes.current.forEach((node) => node.disconnect());
    masteringNodes.current = [];
    compGains.current = [];
    resolvedBass.current = { active: false, lfeGainDb: 0 };
    measuredLkfs.current = -70;
    preGainTpDbtp.current = -70;
    loudnessMeasureState.current = { framesElapsed: 0, done: false };
    speakerBuses.current.forEach((bus) => {
      bus.muteGain.disconnect();
      bus.masterIn.disconnect();
      bus.masterOut.disconnect();
      bus.encoder.in.disconnect();
      bus.encoder.out.disconnect();
      bus.stereoSend?.gainL.disconnect();
      bus.stereoSend?.gainR.disconnect();
    });
    speakerBuses.current.clear();
    channelAnalysers.current.forEach((analyser) => analyser.disconnect());
    channelAnalysers.current.clear();
    channelLevels.current.clear();
    if (headphoneAnalysers.current) {
      headphoneAnalysers.current.splitter.disconnect();
      headphoneAnalysers.current.left.disconnect();
      headphoneAnalysers.current.right.disconnect();
      headphoneAnalysers.current = null;
    }
    headphoneLevels.current = { left: SILENT_METER_LEVEL, right: SILENT_METER_LEVEL };
    stereoMerger.current?.disconnect();
    stereoMerger.current = null;
    nativeMerger.current?.disconnect();
    nativeMerger.current = null;
    binauralGate.current?.disconnect();
    binauralGate.current = null;
    stereoGate.current?.disconnect();
    stereoGate.current = null;
    nativeOutputGain.current?.disconnect();
    nativeOutputGain.current = null;
    nativeSoftLimit.current?.disconnect();
    nativeSoftLimit.current = null;
    nativeMonitorGain.current?.disconnect();
    nativeMonitorGain.current = null;
    nativeChannelCount.current = 0;
    hoaBus.current?.disconnect();
    hoaBus.current = null;
    decodeConvolvers.current.forEach(({ left, right, preGain }) => {
      left.disconnect();
      right.disconnect();
      preGain?.disconnect();
    });
    decodeConvolvers.current = [];
    assignedDecodeProfile.current = null;
    binauralGraphNodes.current.forEach((node) => node.disconnect());
    binauralGraphNodes.current = [];
    voicingChain.current?.nodes.forEach((node) => node.disconnect());
    voicingChain.current = null;
    voicingMerger.current?.disconnect();
    voicingMerger.current = null;
    preMasterBus.current?.disconnect();
    preMasterBus.current = null;
    sidechainSum.current?.disconnect();
    sidechainSum.current = null;
    sidechainSink.current?.disconnect();
    sidechainSink.current = null;
    sidechainCompressor.current?.disconnect();
    sidechainCompressor.current = null;
    lfeBus.current?.disconnect();
    lfeBus.current = null;
    lfeMasterIn.current?.disconnect();
    lfeMasterIn.current = null;
    lfeMasterOut.current?.disconnect();
    lfeMasterOut.current = null;
    lfeMuteGain.current?.disconnect();
    lfeMuteGain.current = null;
    mergePoint.current?.disconnect();
    mergePoint.current = null;
    mergePointAnalyser.current?.disconnect();
    mergePointAnalyser.current = null;
    loudnessMeasureBuf.current = null;
    softLimit.current?.disconnect();
    softLimit.current = null;
    monitorGain.current?.disconnect();
    monitorGain.current = null;
    master.current?.disconnect();
    master.current = null;
    timeline.current = null;
    initPromise.current = null;
    currentTimeRef.current = 0;
    durationRef.current = 0;
    playingRef.current = false;
    scrub.current = null;
    setPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    setReady(false);
  }, [stopSources, stopTicker]);

  React.useEffect(() => () => reset(), [key, reset]);
  React.useEffect(() => {
    setError(null);
  }, [key]);
  React.useEffect(() => () => {
    reset();
    const activeContext = context.current;
    context.current = null;
    void activeContext?.close();
  }, [reset]);

  // Rebuilds the EQ -> compressor -> bass-shelf chain between each channel's
  // `masterIn` and `masterOut` (see `SpeakerBus`) to mirror
  // upmixer/mastering/chain.py's stage order AND its position in the
  // pipeline: mastering runs on the discrete bed BEFORE the spatial/binaural
  // render (upmixer/pipeline.py runs MasteringChain.process() then
  // render_binaural_delivery()), not after it. Stages are entirely omitted
  // when their manifest profile is unset, same as the backend. LFE bypasses
  // this chain entirely (`lfeBus` feeds `mergePoint` directly, downstream of
  // the spatial render) since the backend excludes LFE from EQ, compression,
  // and the sub/mid bass bands — its own gain trim is applied separately at
  // the stem level via `resolvedBass.current.lfeGainDb`.
  //
  // The compressor is architecturally the tricky part: the backend links a
  // single detector across every channel (compressor.py's "linked
  // sidechain"), but a native DynamicsCompressorNode's channelCount cannot
  // exceed 2, so it can't process the (up to 11-channel) discrete bed
  // directly. Instead every channel's post-EQ signal sums into
  // `sidechainSum`, which drives ONE compressor used purely as a detector;
  // `tick()` polls its `.reduction` (~60x/sec — ample resolution for these
  // attack/release times) and applies that as a shared linear gain to every
  // channel's own `compGain` node. That reproduces genuine linked behavior
  // without an AudioWorklet.
  const buildMasteringTopology = React.useCallback(() => {
    const ctx = context.current;
    if (!ctx || speakerBuses.current.size === 0) return;

    speakerBuses.current.forEach((bus) => bus.masterIn.disconnect());
    lfeMasterIn.current?.disconnect();
    masteringNodes.current.forEach((node) => node.disconnect());

    const channelPorts = new Map<string, { input: AudioNode; output: AudioNode }>();
    for (const [channel, bus] of speakerBuses.current.entries()) {
      channelPorts.set(channel, { input: bus.masterIn, output: bus.masterOut });
    }

    const handle = buildMasteringGraph(ctx, channelPorts, mastering, firEqBufferCache.current, {
      sidechain: sidechainSum.current && sidechainSink.current
        ? { sum: sidechainSum.current, sink: sidechainSink.current }
        : undefined,
      refMatchBufferCache: refMatchBufferCache.current,
    });

    masteringNodes.current = handle.nodes;
    compGains.current = handle.compGains;
    sidechainCompressor.current = handle.compressor;
    compMakeupGain.current = handle.compMakeupGain;
    resolvedBass.current = { active: handle.bassActive, lfeGainDb: handle.bassLfeGainDb };

    // LFE reference-match: unlike named-profile EQ (which bypasses LFE, see
    // eq.py's lfe_key bypass), upmixer/mastering/match_reference.py does NOT
    // bypass LFE — it resolves a reference proxy for it too (this module's
    // doc comment, "LFE handling"). `buildMasteringGraph` only wires the
    // positional channel bed, so LFE's own RMS gain + spectral FIR bridge
    // `lfeMasterIn` -> ... -> `lfeMasterOut` here, gated the same way —
    // those two are the stable insert points `initialize()` permanently
    // wired `lfeBus -> lfeMasterIn` and `lfeMasterOut -> lfeMuteGain` around.
    const refCfg = mastering?.match_reference;
    if (lfeMasterIn.current && lfeMasterOut.current) {
      let lfeChainEnd: AudioNode = lfeMasterIn.current;
      if (refCfg?.rms && refCfg.rms_gain_db) {
        const lfeRmsGain = ctx.createGain();
        lfeRmsGain.gain.value = 10 ** (refCfg.rms_gain_db / 20);
        masteringNodes.current.push(lfeRmsGain);
        lfeChainEnd.connect(lfeRmsGain);
        lfeChainEnd = lfeRmsGain;
      }
      if (refCfg?.spectrum && refCfg.fir_url && (refCfg.strength ?? 0) > 0 && refCfg.channels?.includes("LFE")) {
        const firLfeRef = buildFirEqNode(ctx, refCfg.strength ?? 1);
        masteringNodes.current.push(...firLfeRef.nodes);
        lfeChainEnd.connect(firLfeRef.input);
        lfeChainEnd = firLfeRef.output;
        void loadCachedRefMatchBuffers(
          refMatchBufferCache.current, ctx, refCfg.fir_url, refCfg.channels,
        )
          .then((buffers) => {
            const buffer = buffers.get("LFE");
            if (buffer) firLfeRef.convolver.buffer = buffer;
          })
          .catch(() => {});
      }
      lfeChainEnd.connect(lfeMasterOut.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on masteringKey, not `mastering` (see masteringKey comment above)
  }, [masteringKey]);

  React.useEffect(() => {
    buildMasteringTopology();
  }, [masteringKey, buildMasteringTopology]);

  // Rebuilds each stem's `stemGain -> [FIR EQ] -> postEqGain` insert
  // (upmixer/separation/stem_eq.py's per-stem EQ, applied before spatial
  // routing) whenever `mix.stem_eq` changes. Mirrors `buildMasteringTopology`'s
  // rebuild-in-place pattern: the fixed `postEqGain` node `createStemSends`
  // was built against never changes identity, so only the FIR insert
  // feeding it needs replacing. A stem with no (or an unrecognized) profile
  // gets a direct bypass connection, matching the backend's pass-through for
  // unaddressed stems. stem_eq has no wet/dry strength knob on the backend
  // (always fully applied), so `buildFirEqNode` is always built at strength 1.
  const buildStemEqChains = React.useCallback(() => {
    const ctx = context.current;
    if (!ctx) return;
    for (const stem of stemsRef.current) {
      const node = nodes.current.get(stem.id);
      if (!node || !node.stemGain || !node.postEqGain) continue;
      const base = stem.stem_key.split("@", 1)[0];
      const profile = mix?.stem_eq?.[stem.stem_key] || mix?.stem_eq?.[base];

      node.stemGain.disconnect();
      (stemEqNodes.current.get(stem.id) || []).forEach((eqNode) => eqNode.disconnect());

      const assetName = profile && profile in STEM_EQ_FIR_ASSETS
        ? STEM_EQ_FIR_ASSETS[profile as StemEqProfileName]
        : null;
      if (assetName) {
        const firEq = buildFirEqNode(ctx, 1);
        stemEqNodes.current.set(stem.id, firEq.nodes);
        node.ownNodes.push(...firEq.nodes);
        node.stemGain.connect(firEq.input);
        firEq.output.connect(node.postEqGain);
        void loadCachedEqBuffer(firEqBufferCache.current, ctx, assetName)
          .then((buffer) => { firEq.convolver.buffer = buffer; })
          .catch(() => {});
      } else {
        stemEqNodes.current.set(stem.id, []);
        node.stemGain.connect(node.postEqGain);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on stemEqKey, not `mix` (see stemEqKey comment above)
  }, [stemEqKey]);

  React.useEffect(() => {
    buildStemEqChains();
  }, [stemEqKey, buildStemEqChains]);

  const applySpeakerMute = React.useCallback(() => {
    const ctx = context.current;
    speakerBuses.current.forEach((bus, channel) => {
      const target = speakerEnabled[channel] === false ? 0 : 1;
      if (ctx) rampGainTo(bus.muteGain.gain, target, ctx);
      else bus.muteGain.gain.value = target;
    });
    if (lfeMuteGain.current) {
      const target = speakerEnabled.LFE === false ? 0 : 1;
      if (ctx) rampGainTo(lfeMuteGain.current.gain, target, ctx);
      else lfeMuteGain.current.gain.value = target;
    }
  }, [speakerEnabled]);

  React.useEffect(() => {
    applySpeakerMute();
  }, [applySpeakerMute]);

  const toggleSpeaker = React.useCallback((channel: string) => {
    setSpeakerEnabled((current) => ({ ...current, [channel]: current[channel] === false }));
  }, []);

  // Routes `ctx.destination` to whichever render stage the requested mode
  // needs, and gates `preMasterBus`'s two alternate inputs (binaural decoder
  // vs. stereo downmix) accordingly. Falls back to the stereo path if native
  // is requested but the current output device can't carry that many
  // discrete channels — the selector already disables that option, but a
  // device can change after the fact (e.g. unplugged mid-session).
  const applyOutputMode = React.useCallback((mode: OutputMode) => {
    const ctx = context.current;
    if (!ctx) return;
    const destination = ctx.destination;
    // Route from the monitor-gain node, not the soft-limiter directly — the
    // monitor node runs after the limiter on both paths (see `monitorGain`/
    // `nativeMonitorGain`'s declarations), so it is the actual last stage
    // before headphones/speakers on either one, and is where the user's
    // Transport volume/mute lives.
    const stereoOut = monitorGain.current;
    const nativeOut = nativeMonitorGain.current;
    try { stereoOut?.disconnect(destination); } catch { /* not connected */ }
    try { nativeOut?.disconnect(destination); } catch { /* not connected */ }
    const nCh = nativeChannelCount.current;
    const maxChannelCount = destination.maxChannelCount || 2;
    const canNative = mode === "native" && nCh > 0 && nCh <= maxChannelCount;
    if (canNative) {
      destination.channelCount = nCh;
      destination.channelCountMode = "explicit";
      destination.channelInterpretation = "discrete";
      nativeOut?.connect(destination);
    } else {
      destination.channelCount = Math.min(2, maxChannelCount);
      destination.channelCountMode = "explicit";
      destination.channelInterpretation = "speakers";
      stereoOut?.connect(destination);
    }
    const effectiveMode: OutputMode = canNative ? "native" : mode === "native" ? "binaural" : mode;
    // Crossfade between binaural/stereo instead of a hard cut — the two
    // gates are mutually exclusive today, so a short glide is enough to
    // avoid a click on mode switch without audible bleed.
    if (binauralGate.current) rampGainTo(binauralGate.current.gain, effectiveMode === "binaural" ? 1 : 0, ctx);
    if (stereoGate.current) rampGainTo(stereoGate.current.gain, effectiveMode === "stereo" ? 1 : 0, ctx);
  }, []);

  React.useEffect(() => {
    applyOutputMode(outputMode);
  }, [outputMode, applyOutputMode, ready]);

  React.useEffect(() => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    let cancelled = false;
    const load = () => {
      navigator.mediaDevices.enumerateDevices()
        .then((devices) => {
          if (!cancelled) setOutputDevices(devices.filter((device) => device.kind === "audiooutput"));
        })
        .catch(() => {
          // No permission/support to enumerate — device picker stays empty,
          // native mode still plays to the default device.
        });
    };
    load();
    navigator.mediaDevices.addEventListener?.("devicechange", load);
    return () => {
      cancelled = true;
      navigator.mediaDevices.removeEventListener?.("devicechange", load);
    };
  }, []);

  const setOutputDeviceId = React.useCallback(async (deviceId: string) => {
    setOutputDeviceIdState(deviceId);
    const ctx = context.current as (AudioContext & { setSinkId?: (id: string) => Promise<void> }) | null;
    if (!ctx?.setSinkId) return;
    try {
      await ctx.setSinkId(deviceId);
    } catch {
      // Browser or device rejected the sink switch — stays on the previous device.
    }
  }, []);

  // Loads a profile's decode filter set and assigns each ACN/ear filter into
  // its (already-wired) ConvolverNode — see docs/standards/
  // spatial_audio_engine.md §4. Non-blocking: convolvers with no buffer yet
  // simply output silence (per the Web Audio spec), so preview audio can
  // start immediately while filters fetch/decode in the background.
  const loadDecodeFilterSet = React.useCallback(async (profile: SpatialProfile) => {
    const ctx = context.current;
    const convolvers = decodeConvolvers.current;
    if (!ctx || convolvers.length !== N_ACN_CHANNELS) return false;
    if (assignedDecodeProfile.current === profile) return true;
    try {
      const channels = await loadCachedDecodeFilterChannels(
        decodeFilterCache.current, ctx, DECODE_FILTER_SET[profile], fetchDecodeFilterPart,
      );
      // Convolvers may have been rebuilt (or the profile reassigned again)
      // while this fetch/decode was in flight — re-check both before assigning.
      if (context.current !== ctx || decodeConvolvers.current !== convolvers) return false;
      if (assignedDecodeProfile.current === profile) return true;
      assignDecodeFilterBuffers(ctx, convolvers, channels);
      assignedDecodeProfile.current = profile;
      return true;
    } catch {
      return false;
    }
  }, []);

  const apply = React.useCallback(() => {
    const ctx = context.current;
    // The active Spatial Audio Engine profile's own loudness target (if any)
    // overrides the mastering block's target when rendering binaural — see
    // VOICING_PARAMS.loudnessTargetLkfs. All profiles currently leave it null
    // (listening is level-matched to studio/flat), so this falls back to the
    // mastering block's target; the override stays wired for future use.
    const profileLoudnessTarget = outputModeRef.current === "binaural"
      ? VOICING_PARAMS[spatialProfileRef.current].loudnessTargetLkfs
      : null;
    const targetLkfs = profileLoudnessTarget ?? mastering?.loudness?.target ?? -18;
    const normalize = mastering?.loudness?.normalize ?? true;
    // Binaural's collapse-stage correction is capped small (see
    // BINAURAL_LOUDNESS_MAX_GAIN_DB) — the bed is already loudness-matched
    // before collapse, so this only nudges for the collapse's own level
    // shift instead of re-running a full match that would inflate loudness.
    const maxGainDb = outputModeRef.current === "binaural" ? BINAURAL_LOUDNESS_MAX_GAIN_DB : LOUDNESS_MAX_GAIN_DB;
    const loudnessGain = normalize ? loudnessGainFor(measuredLkfs.current, targetLkfs, maxGainDb) : 1;
    // Mirrors normalize_loudness's second gain reduction (upmixer/loudness.py)
    // — gated on the same `normalize` flag as the loudness correction itself:
    // the backend only calls normalize_loudness (which folds in both stages)
    // when loudness_normalize is set, so true-peak protection is skipped
    // exactly when the backend would skip it too. Before the first
    // measurement lands (preGainTpDbtp still its -70 reset default), this is
    // a no-op (see applyTruePeakCeiling).
    const maxTpDbtp = mastering?.loudness?.max_tp ?? -1;
    const tpSafeGain = normalize
      ? applyTruePeakCeiling(preGainTpDbtp.current, loudnessGain, maxTpDbtp)
      : loudnessGain;
    // PROGRAM domain — what a bounce of this graph would contain. Carries
    // only the loudness/true-peak correction, never the user's monitor
    // volume (see `master`'s declaration) — so it stays live even during the
    // warm-up (see `monitorGain` below for where silencing actually
    // happens), and the fallback fires only when `ctx` itself is absent.
    if (master.current) {
      if (ctx) rampGainTo(master.current.gain, tpSafeGain, ctx);
      else master.current.gain.value = tpSafeGain;
    }
    // Native bypasses the stereo mastering chain, so it has no
    // loudness-normalize gain of its own to apply — stays at unity.
    if (nativeOutputGain.current) {
      if (ctx) rampGainTo(nativeOutputGain.current.gain, 1, ctx);
      else nativeOutputGain.current.gain.value = 1;
    }
    // MONITOR domain — strictly downstream of the soft limiter and the
    // channel/headphone meter taps (see `monitorGain`'s declaration), so
    // this is the only gain the volume slider and mute drive, and it can
    // never feed back into the limiter's engagement or the meters' reading.
    // Warmup silence must land instantly here — a ramped mute would leak a
    // sliver of pre-correction level through the glide.
    const monitorTarget = muted ? 0 : faderPositionToGain(volume);
    for (const node of [monitorGain.current, nativeMonitorGain.current]) {
      if (!node) continue;
      if (warmupMuted.current) node.gain.value = 0;
      else if (ctx) rampGainTo(node.gain, monitorTarget, ctx);
      else node.gain.value = monitorTarget;
    }
    const anchor = mix?.stem_source_anchor_strength || 0;
    const source = nodes.current.get("__source_anchor__");
    if (source) {
      // Dry stereo anchor blends straight into the FL/FR speaker buses —
      // mirrors apply_source_anchor blending untouched source L/R into the
      // native front pair, ahead of the ambisonic render.
      const sendFL = source.sends.FL;
      const sendFR = source.sends.FR;
      if (sendFL) sendFL.gain.value = anchor;
      if (sendFR) sendFR.gain.value = anchor;
    }
    for (const stem of stemsRef.current) {
      const node = nodes.current.get(stem.id);
      if (!node) continue;
      const base = stem.stem_key.split("@", 1)[0];
      const value = scene.stems?.[stem.stem_key] || scene.stems?.[base] || {};
      let route = mix?.stem_routing?.[stem.stem_key] || mix?.stem_routing?.[base];
      // No resolved routing yet (e.g. a freshly dropped stem before the
      // backend/manifest has assigned it a channel map) — fall back to the
      // same nearest-3-speakers, inverse-distance weighting the backend's
      // own routing_for_scene uses to turn a dragged position into gains.
      if (!route || Object.keys(route).length === 0) {
        route = value.azimuth_deg != null || value.elevation_deg != null
          ? routingFromAzimuthElevation(value.azimuth_deg || 0, value.elevation_deg || 0)
          : {};
      }

      let total = 0;
      let frontWeight = 0;
      for (const [channel, weight] of Object.entries(route)) {
        if (weight <= 0) continue;
        total += weight;
        if (channel === "FL" || channel === "FR") frontWeight += weight;
      }
      // Only the FL/FR portion of a stem's routing crossfades toward the dry
      // source in the backend (source_anchor.py blends the front zone pair
      // only); other stems' surround/height/back content is left untouched.
      const frontFraction = total > 0 ? frontWeight / total : 0;
      const lfeWeight = route.LFE || 0;

      const muted = Boolean(mix?.stem_solo?.length && !mix.stem_solo.includes(stem.stem_key) && !mix.stem_solo.includes(base))
        || mix?.stem_enabled?.[base] === false || value.enabled === false;
      const gainDb = mix?.stem_rebalance?.[base] || 0;
      const stemGainValue = muted ? 0 : (1.0 - anchor * frontFraction) * 10 ** (gainDb / 20);
      appliedGain.current.set(base, stemGainValue);
      if (node.stemGain) node.stemGain.gain.value = stemGainValue;
      if (node.lfeGain) {
        node.lfeGain.gain.value = muted
          ? 0
          : LFE_GAIN * lfeWeight * 10 ** (resolvedBass.current.lfeGainDb / 20);
      }

      const routeScale = estimateRouteScale(route);
      for (const channel of positionalChannelsRef.current) {
        const send = node.sends[channel];
        if (!send) continue;
        const weight = route[channel] || 0;
        send.gain.value = weight > 0 ? routeScale * weight * channelGroupGain(channel) : 0;
      }
    }
  }, [mix, scene.stems, volume, muted, mastering]);
  applyRef.current = apply;

  React.useEffect(() => {
    apply();
  }, [apply]);

  // Profile switch: retune the already-built voicing chain immediately
  // (cheap, no graph rebuild — see buildVoicingChain), and swap in the new
  // profile's decode filter set in the background.
  React.useEffect(() => {
    if (voicingChain.current) applyVoicingParams(voicingChain.current, VOICING_PARAMS[spatialProfile]);
    applyRef.current();
    void loadDecodeFilterSet(spatialProfile);
    // apply() is intentionally invoked via applyRef, not as a dependency:
    // apply's own identity changes on every mix/volume/mastering edit
    // (see its deps below), none of which should re-trigger a decode
    // filter set load — only a genuine profile switch or graph-ready flip
    // should. See applyRef's own comment for the same pattern.
  }, [spatialProfile, loadDecodeFilterSet, ready]);

  const initialize = React.useCallback(() => {
    if (!supported) return Promise.resolve();
    if (initPromise.current) return initPromise.current;
    const Constructor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!context.current && Constructor) context.current = new Constructor();
    const ctx = context.current;
    if (!ctx) return Promise.resolve();

    // Everything below (including the stem-decode tail that used to be the
    // only async part) now lives in one IIFE, so `initPromise.current` below
    // is set synchronously, before any `await` yields control back to the
    // caller — a second concurrent `initialize()` call must see the guard
    // already in place instead of racing past it and building the graph
    // twice. (Splitting this into an `async` outer callback instead would
    // delay that assignment until after the first `await`, exactly the bug
    // this comment is here to prevent from being reintroduced.)
    const promise = (async () => {
    // Must resolve before the native limiter node below can be constructed.
    // Falls back to the plain tanh WaveShaper (nativeLimiterWorkletReady =
    // false) if the module fails to load — some embedding contexts disable
    // AudioWorklet (e.g. insecure/non-HTTPS origins), and a broken preview
    // is worse than a slightly-less-accurate safety net.
    let nativeLimiterWorkletReady = true;
    try {
      await ctx.audioWorklet.addModule("/limiter.worklet.js");
    } catch {
      nativeLimiterWorkletReady = false;
    }

    const preMasterBusNode = ctx.createGain();
    const lfeBusNode = ctx.createGain();
    const mergePointNode = ctx.createGain();

    // Shared ambisonic core: every speaker's encoder (created per channel
    // below) sums into `binaural.hoaBus` (explicit/discrete at 16ch so
    // multiple encoders' outputs add channel-for-channel instead of being
    // up/down-mixed), which `buildBinauralGraph` (previewGraph.ts) decodes
    // through a 16-way split into one ConvolverNode pair (L, R) per ACN
    // channel — the same bank-convolution decode the core engine runs
    // (docs/standards/spatial_audio_engine.md §4) — summed to stereo and
    // voiced per the active Spatial Audio Engine profile (§5). See this
    // file's top comment and that function's docstring.
    const binaural = buildBinauralGraph(ctx, spatialProfileRef.current);

    // Binaural/stereo are alternate render stages that both feed
    // `preMasterBus` through their own gate — see `applyOutputMode`, which
    // zeroes whichever gate isn't the active mode instead of tearing down
    // and rebuilding this graph on every mode switch. `preMasterBus` is a
    // plain passthrough into `mergePoint` now — mastering already happened
    // upstream, per positional channel, before this spatial render (see the
    // `masterIn`/`masterOut` wiring below and `buildMasteringTopology`).
    const binauralGateNode = ctx.createGain();
    binaural.output.connect(binauralGateNode);
    binauralGateNode.connect(preMasterBusNode);

    const stereoMergerNode = ctx.createChannelMerger(2);
    const stereoGateNode = ctx.createGain();
    stereoMergerNode.connect(stereoGateNode);
    stereoGateNode.connect(preMasterBusNode);
    preMasterBusNode.connect(mergePointNode);

    // Discrete native bus: one ChannelMerger input per layout channel
    // (including LFE), fed straight from each channel's mute gain — the
    // exact per-speaker signal the channel meters already display.
    const layoutChannelList = layoutChannelsRef.current;
    const nativeMergerNode = ctx.createChannelMerger(Math.max(1, layoutChannelList.length));
    const nativeOutputGainNode = ctx.createGain();
    nativeMergerNode.connect(nativeOutputGainNode);
    // Look-ahead true-peak limiter, applied after the volume gain so it
    // only ever engages as a safety net — native would otherwise reach
    // `ctx.destination` unlimited. Mirrors upmixer/mastering/limiter.py's
    // LookAheadLimiter (see masteringProfiles.ts's LIMITER_LOOKAHEAD_MS/
    // LIMITER_RELEASE_MS comment); `ceilingDb` is not a live-editable
    // manifest field today, so baking it in at construction (like the
    // worklet's other parameters) matches this graph's existing pattern of
    // rebuilding structural nodes on `initialize()`, not live-retuning them.
    // Falls back to the plain tanh WaveShaper if the worklet failed to load.
    const nativeSoftLimitNode: AudioWorkletNode | WaveShaperNode = nativeLimiterWorkletReady
      ? new AudioWorkletNode(ctx, "limiter-processor", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          channelCount: Math.max(1, layoutChannelList.length),
          channelCountMode: "explicit",
          channelInterpretation: "discrete",
          processorOptions: {
            ceilingDb: masteringRef.current?.loudness?.max_tp ?? -1,
            lookaheadMs: LIMITER_LOOKAHEAD_MS,
            releaseMs: LIMITER_RELEASE_MS,
            numberOfChannels: Math.max(1, layoutChannelList.length),
          },
        })
      : (() => {
          const fallback = ctx.createWaveShaper();
          fallback.curve = buildSoftLimitCurve();
          fallback.oversample = "4x";
          return fallback;
        })();
    nativeOutputGainNode.connect(nativeSoftLimitNode);
    // MONITOR domain, native path — see `nativeMonitorGain`'s declaration.
    // Explicit discrete channelCount so this node passes every layout
    // channel straight through instead of folding to the default stereo.
    const nativeMonitorGainNode = ctx.createGain();
    nativeMonitorGainNode.channelCount = Math.max(1, layoutChannelList.length);
    nativeMonitorGainNode.channelCountMode = "explicit";
    nativeMonitorGainNode.channelInterpretation = "discrete";
    nativeSoftLimitNode.connect(nativeMonitorGainNode);

    const busesMap = new Map<string, SpeakerBus>();
    const channelAnalysersMap = new Map<string, AnalyserNode>();
    // Sidechain bus-compressor detector — see the ref comment and
    // `buildMasteringTopology`. `sink` is a permanent zero-gain tap so the
    // compressor node stays part of the actively rendered graph.
    const sidechainSumNode = ctx.createGain();
    const sidechainSinkNode = ctx.createGain();
    sidechainSinkNode.gain.value = 0;
    sidechainSinkNode.connect(mergePointNode);

    for (const channel of positionalChannelsRef.current) {
      const muteGain = ctx.createGain();
      muteGain.gain.value = speakerEnabled[channel] === false ? 0 : 1;
      // Stable mastering insert points — `buildMasteringTopology` wires a
      // fresh EQ -> compressor-gain -> bass chain (or a direct passthrough)
      // between these on every rebuild, upstream of the spatial render
      // below, matching upmixer/pipeline.py's mastering-before-binaural
      // order. Everything downstream reads from `masterOut`, never
      // `muteGain` directly, so a mastering-only config change never has to
      // touch the ambisonic/HRTF graph.
      const masterIn = ctx.createGain();
      const masterOut = ctx.createGain();
      muteGain.connect(masterIn);

      const { azim, elev } = positionToAzimuthElevation(speakerCoordinates[channel]);
      const encoder = createPositionalEncoder(ctx, azim, elev);
      masterOut.connect(encoder.in);
      encoder.out.connect(binaural.hoaBus);

      const stereoCoeffs = STEREO_DOWNMIX_GAINS[channel];
      let stereoSend: SpeakerBus["stereoSend"] = null;
      if (stereoCoeffs) {
        const gainL = ctx.createGain();
        gainL.gain.value = stereoCoeffs.left;
        const gainR = ctx.createGain();
        gainR.gain.value = stereoCoeffs.right;
        masterOut.connect(gainL);
        gainL.connect(stereoMergerNode, 0, 0);
        masterOut.connect(gainR);
        gainR.connect(stereoMergerNode, 0, 1);
        stereoSend = { gainL, gainR };
      }

      const nativeIndex = layoutChannelList.indexOf(channel);
      if (nativeIndex >= 0) masterOut.connect(nativeMergerNode, 0, nativeIndex);

      busesMap.set(channel, { muteGain, masterIn, masterOut, encoder, stereoSend, nativeIndex });
      // No output connection — a pure meter tap, cannot affect the audible
      // signal. 2048 samples (42.7ms @ 48kHz) comfortably exceeds the
      // ~16.7ms rAF interval `measureChannelLevels` polls at, so no sample
      // window is ever skipped between reads — the old 256-sample window
      // (5.3ms) left roughly two thirds of every frame's audio unseen.
      // `smoothingTimeConstant` only affects frequency-domain reads, so it's
      // left at its default rather than set for a time-domain tap.
      const channelAnalyser = ctx.createAnalyser();
      channelAnalyser.fftSize = 2048;
      masterOut.connect(channelAnalyser);
      channelAnalysersMap.set(channel, channelAnalyser);
    }

    // Backend final stage, run AFTER the loudness/volume gain (`output`):
    // soft_limit(x, 0.95), a tanh saturator above the threshold
    // (upmixer/utils.py). Limiting the raw pre-gain sum would bake in
    // saturation the gain stage can never undo, so `output`'s gain is
    // applied first and this only ever engages as a true-peak safety net —
    // see render_binaural_delivery's stage ordering.
    const softLimitNode = ctx.createWaveShaper();
    softLimitNode.curve = buildSoftLimitCurve();
    softLimitNode.oversample = "4x";
    // MONITOR domain — see `monitorGain`'s declaration. Deliberately after
    // `softLimitNode`, not before: the channel/headphone meter taps below
    // read `softLimitNode`'s output, so this node's gain (the Transport
    // volume slider) can never move the meters or change how hard the
    // limiter engages.
    const monitorGainNode = ctx.createGain();
    const output = ctx.createGain();
    const lfeMuteGainNode = ctx.createGain();
    lfeMuteGainNode.gain.value = speakerEnabled.LFE === false ? 0 : 1;
    // Stable insert points for LFE reference-match, mirroring the
    // positional bed's masterIn/masterOut — `buildMasteringTopology` bridges
    // these two on every rebuild; nothing downstream needs to know.
    const lfeMasterInNode = ctx.createGain();
    const lfeMasterOutNode = ctx.createGain();
    lfeBusNode.connect(lfeMasterInNode);
    lfeMasterOutNode.connect(lfeMuteGainNode).connect(mergePointNode);
    mergePointNode.connect(output).connect(softLimitNode);
    // Pure meter tap for measureOutputLoudness — see mergePointAnalyser's
    // declaration. Sits before `output`'s own loudness/volume gain, so it
    // reads the same pre-correction signal normalize_loudness measures on
    // the backend.
    const mergePointAnalyserNode = ctx.createAnalyser();
    mergePointAnalyserNode.fftSize = 32768;
    mergePointNode.connect(mergePointAnalyserNode);
    // LFE's own discrete native channel — bypasses the stereo mastering
    // chain entirely, same as every other native channel.
    const lfeNativeIndex = layoutChannelList.indexOf("LFE");
    if (lfeNativeIndex >= 0) lfeMuteGainNode.connect(nativeMergerNode, 0, lfeNativeIndex);
    const lfeAnalyser = ctx.createAnalyser();
    lfeAnalyser.fftSize = 2048;
    lfeMuteGainNode.connect(lfeAnalyser);
    channelAnalysersMap.set("LFE", lfeAnalyser);

    // Headphone L/R tap: splits the final output (post soft-limit, the exact
    // signal reaching headphones) into two mono analysers. Same 2048-sample
    // window as the channel taps above — see that comment.
    const headphoneSplitter = ctx.createChannelSplitter(2);
    const headphoneLeftAnalyser = ctx.createAnalyser();
    const headphoneRightAnalyser = ctx.createAnalyser();
    headphoneLeftAnalyser.fftSize = 2048;
    headphoneRightAnalyser.fftSize = 2048;
    // Tap `softLimitNode` directly (not `monitorGainNode`) — this is the
    // channel meters' sibling tap and must stay pre-monitor for the same
    // reason they do (see `monitorGainNode`'s declaration comment).
    softLimitNode.connect(headphoneSplitter);
    headphoneSplitter.connect(headphoneLeftAnalyser, 0);
    headphoneSplitter.connect(headphoneRightAnalyser, 1);
    softLimitNode.connect(monitorGainNode);

    hoaBus.current = binaural.hoaBus;
    lfeMuteGain.current = lfeMuteGainNode;
    decodeConvolvers.current = binaural.convolverPairs;
    voicingChain.current = binaural.voicing;
    voicingMerger.current = binaural.output as ChannelMergerNode;
    binauralGraphNodes.current = binaural.nodes;
    speakerBuses.current = busesMap;
    channelAnalysers.current = channelAnalysersMap;
    headphoneAnalysers.current = { splitter: headphoneSplitter, left: headphoneLeftAnalyser, right: headphoneRightAnalyser };
    preMasterBus.current = preMasterBusNode;
    sidechainSum.current = sidechainSumNode;
    sidechainSink.current = sidechainSinkNode;
    lfeBus.current = lfeBusNode;
    lfeMasterIn.current = lfeMasterInNode;
    lfeMasterOut.current = lfeMasterOutNode;
    mergePoint.current = mergePointNode;
    mergePointAnalyser.current = mergePointAnalyserNode;
    loudnessMeasureBuf.current = new Float32Array(mergePointAnalyserNode.fftSize);
    softLimit.current = softLimitNode;
    master.current = output;
    monitorGain.current = monitorGainNode;
    stereoMerger.current = stereoMergerNode;
    nativeMerger.current = nativeMergerNode;
    binauralGate.current = binauralGateNode;
    stereoGate.current = stereoGateNode;
    nativeOutputGain.current = nativeOutputGainNode;
    nativeSoftLimit.current = nativeSoftLimitNode;
    nativeMonitorGain.current = nativeMonitorGainNode;
    nativeChannelCount.current = layoutChannelList.length;
    setMaxChannels(ctx.destination.maxChannelCount || 2);
    buildMasteringTopology();
    applyOutputMode(outputModeRef.current);
    setReady(false);
    setLoadProgress(0);

    // Non-blocking: convolvers output silence until their buffers are
    // assigned, so preview audio can start immediately rather than waiting
    // on this network fetch.
    void loadDecodeFilterSet(spatialProfileRef.current);

    const entries: { id: string; url: string; anchor: boolean }[] = [];
    for (const stem of stemsRef.current) {
      const url = stem.preview_url || stem.audio_url;
      if (url) entries.push({ id: stem.id, url, anchor: false });
    }
    if (sourcePreviewUrl) entries.push({ id: "__source_anchor__", url: sourcePreviewUrl, anchor: true });

    try {
      let decoded = 0;
      // Stems finish decoding in tight clusters, not evenly spaced —
      // flushing `setLoadProgress` straight from each `Promise.all` branch
      // would fire a full page re-render per stem in that cluster, right
      // when the main thread is busiest with decode work. Coalesce same-
      // frame completions into a single state update instead.
      let progressFlushScheduled = false;
      const scheduleProgressFlush = () => {
        if (progressFlushScheduled) return;
        progressFlushScheduled = true;
        window.requestAnimationFrame(() => {
          progressFlushScheduled = false;
          setLoadProgress(entries.length ? decoded / entries.length : 1);
        });
      };
      await Promise.all(entries.map(async (entry) => {
        const buffer = await loadBuffer(ctx, entry.url);
        decoded += 1;
        scheduleProgressFlush();

        if (entry.anchor) {
          const stemInput = ctx.createGain();
          const built = createStemSends(ctx, stemInput, busesMap, positionalChannelsRef.current);
          nodes.current.set(entry.id, {
            buffer, source: null, stemGain: null, postEqGain: null, sends: built.sends,
            ownNodes: [stemInput, ...built.ownNodes],
            lfeGain: null, lfeFilters: null, analyser: null,
            meterSplitter: null, meterAnalysers: [],
          });
        } else {
          const stemGain = ctx.createGain();
          // `createStemSends` reads from `postEqGain`, not `stemGain`
          // directly — `buildStemEqChains` wires the (rebuildable)
          // stem_eq filter chain between them, initially as a bypass.
          const postEqGain = ctx.createGain();
          const built = createStemSends(ctx, postEqGain, busesMap, positionalChannelsRef.current);
          const lfeGain = ctx.createGain();
          const lfeFilter1 = ctx.createBiquadFilter();
          const lfeFilter2 = ctx.createBiquadFilter();
          lfeFilter1.type = "lowpass";
          lfeFilter1.frequency.value = LFE_LOWPASS_HZ;
          lfeFilter2.type = "lowpass";
          lfeFilter2.frequency.value = LFE_LOWPASS_HZ;
          lfeGain.connect(lfeFilter1).connect(lfeFilter2).connect(lfeBusNode);
          // No output connection — a pure tap for the 3D scene's halos,
          // cannot affect the audible signal.
          const analyser = ctx.createAnalyser();
          analyser.fftSize = 256;
          analyser.smoothingTimeConstant = 0.7;
          // One meter tap per source channel (capped at stereo — the mixer
          // strip shows at most two bars, and a >2ch stem's extra channels
          // have no strip to appear in). Also output-less, same as
          // `analyser`: splitting a source that feeds nothing audible
          // cannot alter the mix.
          const meterChannels = Math.min(2, Math.max(1, buffer.numberOfChannels));
          const meterSplitter = ctx.createChannelSplitter(meterChannels);
          const meterAnalysers = Array.from({ length: meterChannels }, (_, channel) => {
            const channelAnalyser = ctx.createAnalyser();
            channelAnalyser.fftSize = 256;
            meterSplitter.connect(channelAnalyser, channel);
            return channelAnalyser;
          });
          nodes.current.set(entry.id, {
            buffer, source: null, stemGain, postEqGain, sends: built.sends,
            ownNodes: [stemGain, postEqGain, ...built.ownNodes],
            lfeGain, lfeFilters: [lfeFilter1, lfeFilter2], analyser,
            meterSplitter, meterAnalysers,
          });
        }
      }));
      const durations = Array.from(nodes.current.values())
        .map((node) => node.buffer.duration)
        .filter((value) => Number.isFinite(value) && value > 0);
      if (durations.length) {
        durationRef.current = Math.min(...durations);
        setDuration(durationRef.current);
      }
      buildStemEqChains();
      setReady(nodes.current.size > 0);
      apply();
    } catch {
      setError("Unable to load every preview stem.");
      throw new Error("Preview stems are still loading");
    }
    })();
    initPromise.current = promise;
    return promise;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- speakerEnabled read only for the initial mute value; live changes go through applySpeakerMute; outputMode/spatialProfile read via refs so switching either alone doesn't force a full reset/re-decode
  }, [apply, applyOutputMode, buildMasteringTopology, buildStemEqChains, loadDecodeFilterSet, sourcePreviewUrl, supported]);

  React.useEffect(() => {
    initialize().catch(() => {
      // error state already set inside initialize
    });
  }, [initialize, key]);

  const requireReady = React.useCallback(() => {
    if (!nodes.current.size) throw new Error("Preview stems are still loading");
    for (const node of nodes.current.values()) {
      if (!node.buffer) throw new Error("Preview stems are still loading");
    }
  }, []);

  const moveTo = React.useCallback((time: number) => {
    const target = Math.max(0, Math.min(time, durationRef.current || time));
    currentTimeRef.current = target;
    setCurrentTime(target);
    return target;
  }, []);

  // Runs the real output exactly as an audible start would (same
  // `startSourcesAt` scheduling), but muted (`warmupMuted`), purely so
  // `measureOutputLoudness` sees genuine post-mastering signal instead of the
  // graph's initial silence — without ever letting the pre-correction level
  // reach the listener. Only needed once per hook lifetime: after
  // `loudnessMeasureState.current.done` flips true, `playFrom` skips this
  // entirely and starts audible playback immediately, same as before this
  // warm-up existed.
  //
  // Paced with `setTimeout` rather than the main `tick()` loop's
  // `requestAnimationFrame`: this has no UI frame to draw, only real
  // AudioContext time to wait out while the post-mastering signal fills
  // `mergePointAnalyser`'s ring buffer, and rAF throttles/suspends in
  // backgrounded tabs — which would stall the very first play indefinitely
  // if the user switched tabs right after pressing it.
  const runLoudnessWarmup = React.useCallback((target: number) => {
    return new Promise<void>((resolve) => {
      warmupMuted.current = true;
      // Silences the MONITOR nodes, not `master`/`nativeOutputGain` — those
      // now carry only the PROGRAM-domain loudness correction and are
      // allowed to keep updating (to unity, then to the real measured gain
      // once it lands) while the monitor stays hard-muted downstream. See
      // `monitorGain`'s declaration and `apply()`'s MONITOR domain block.
      if (monitorGain.current) monitorGain.current.gain.value = 0;
      if (nativeMonitorGain.current) nativeMonitorGain.current.gain.value = 0;
      setMeasuring(true);
      startSourcesAt(target);
      let frame = 0;
      const step = () => {
        frame += 1;
        measureOutputLoudness();
        if (loudnessMeasureState.current.done || frame >= LOUDNESS_WARMUP_MAX_FRAMES) {
          stopSources();
          warmupMuted.current = false;
          setMeasuring(false);
          resolve();
          return;
        }
        window.setTimeout(step, WARMUP_STEP_MS);
      };
      window.setTimeout(step, WARMUP_STEP_MS);
    });
  }, [measureOutputLoudness, startSourcesAt, stopSources]);

  const playFrom = React.useCallback(async (time = currentTimeRef.current) => {
    try {
      await initialize();
      const ctx = context.current;
      if (!ctx || !nodes.current.size) return false;
      setError(null);
      requireReady();
      apply();
      const target = durationRef.current > 0 && time >= durationRef.current ? 0 : time;
      stopSources();
      await ctx.resume();
      // First play only: measure real post-mastering loudness while muted,
      // so the very first audible sample already carries the corrected
      // gain instead of ~55 frames (≈0.9s) of unity/uncorrected level — see
      // `runLoudnessWarmup`.
      if (!loudnessMeasureState.current.done) {
        await runLoudnessWarmup(target);
        apply();
      }
      const startAt = startSourcesAt(target);
      if (startAt === null) return false;
      timeline.current = { offset: target, contextTime: startAt };
      currentTimeRef.current = target;
      playingRef.current = true;
      setPlaying(true);
      startTicker();
      return true;
    } catch (nextError) {
      warmupMuted.current = false;
      setMeasuring(false);
      stopSources();
      timeline.current = null;
      playingRef.current = false;
      setPlaying(false);
      setError(nextError instanceof Error && nextError.message === "Preview stems are still loading"
        ? "Preview stems are still loading. Try again in a moment."
        : `Unable to play every preview stem${nextError instanceof Error && nextError.message ? `: ${nextError.message}` : "."}`);
      return false;
    }
  }, [apply, initialize, requireReady, runLoudnessWarmup, startSourcesAt, startTicker, stopSources]);

  const playPause = React.useCallback(async () => {
    if (playingRef.current) {
      pause();
      return;
    }
    await playFrom();
  }, [pause, playFrom]);

  const stop = React.useCallback(() => {
    pause();
    currentTimeRef.current = 0;
    setCurrentTime(0);
  }, [pause]);

  const beginScrub = React.useCallback(() => {
    if (scrub.current) return;
    scrub.current = { wasPlaying: playingRef.current };
    if (playingRef.current) pause();
  }, [pause]);

  const scrubTo = React.useCallback((time: number) => {
    const target = Math.max(0, Math.min(time, durationRef.current || time));
    currentTimeRef.current = target;
    setCurrentTime(target);
  }, []);

  const commitScrub = React.useCallback(async (time: number) => {
    const activeScrub = scrub.current;
    if (!activeScrub) return;
    scrub.current = null;
    try {
      const target = moveTo(time);
      if (activeScrub.wasPlaying && (durationRef.current === 0 || target < durationRef.current)) await playFrom(target);
    } catch {
      setError("Unable to seek every preview stem.");
    }
  }, [moveTo, playFrom]);

  const seek = React.useCallback(async (time: number) => {
    beginScrub();
    scrubTo(time);
    await commitScrub(time);
  }, [beginScrub, commitScrub, scrubTo]);

  const toggleLoop = React.useCallback(() => {
    loopRef.current = !loopRef.current;
    setLoop(loopRef.current);
    const durationValue = durationRef.current;
    nodes.current.forEach((node) => {
      if (!node.source) return;
      node.source.loop = loopRef.current;
      if (loopRef.current && durationValue > 0) {
        node.source.loopStart = 0;
        node.source.loopEnd = durationValue;
      }
    });
  }, []);

  const toggleMute = React.useCallback(() => {
    setMuted((current) => !current);
  }, []);

  return {
    supported,
    ready,
    loadProgress,
    measuring,
    playing,
    currentTime,
    duration,
    volume,
    muted,
    loop,
    error,
    setVolume,
    toggleMute,
    playPause,
    stop,
    seek,
    beginScrub,
    scrubTo,
    commitScrub,
    toggleLoop,
    stemSpectrum,
    stemLevels,
    channelLevels,
    headphoneLevels,
    currentTimeRef,
    speakerEnabled,
    toggleSpeaker,
    loadDecodeFilterSet,
    maxChannels,
    nativeSupported: layoutChannelsRef.current.length > 0 && layoutChannelsRef.current.length <= maxChannels,
    outputDevices,
    outputDeviceId,
    setOutputDeviceId,
  };
}
