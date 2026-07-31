import type { ProjectStem, StemScene } from "@/api";
import { positionToAzimuthElevation, routingFromAzimuthElevation, speakerCoordinates } from "@/lib/spatial";
import {
  N_ACN_CHANNELS,
  applyVoicingParams,
  buildDiffuseSend,
  buildFirEqNode,
  buildHeightSend,
  buildSoftLimitCurve,
  buildSurroundSend,
  buildTruePeakKernel,
  channelGroupGain,
  estimateRouteScale,
  measureBufferTruePeakDbtp,
  type EngineConstants,
  type SpatialProfile,
  type StemEqProfileName,
  type TransauralProfile,
  type VoicingChain,
} from "./masteringProfiles";
import {
  assignDecodeFilterBuffers,
  assignXtcFilterBuffers,
  buildBinauralGraph,
  buildCrosstalkGraph,
  buildMasteringGraph,
  createPositionalEncoder,
  loadCachedDecodeFilterChannels,
  loadCachedEqBuffer,
  loadCachedRefMatchBuffers,
  loadCachedXtcFilterChannels,
  type MasterPreview,
  type XtcConvolvers,
} from "./previewGraph";
import { faderPositionToGain } from "@/lib/fader";
import { TransportClock } from "./transportClock";
import {
  CORRECTION_STEP_MS,
  applyTruePeakCeiling,
  buildAnalysisExcerpts,
  isClippedPeak,
  loudnessGainFor,
} from "./audioAnalysis";
import { fetchDecodeFilterPart, fetchXtcFilterSet, loadBuffer } from "./audioLoaders";

export { applyTruePeakCeiling } from "./audioAnalysis";

// Framework-free DAW audio layer — see docs/web_architecture.md "Preview audio graph".
export type EngineRef<T> = { current: T };
function engineRef<T>(value: T): EngineRef<T> {
  return { current: value };
}

// Preview monitoring mode: which final render stage the channel bed feeds.
// "binaural" is the existing headphone-virtualized render; "transaural" is
// the crosstalk-cancelled stereo-speaker render (upmixer/crosstalk/); "stereo"
// is a BS.775-compliant 2/0 downmix; "native" sends the channel bed's own
// discrete channels straight to the chosen system output device.
export type OutputMode = "binaural" | "transaural" | "stereo" | "native";

// Mirrors upmixer/utils.py::itu_downmix_stereo — see docs/web_architecture.md.
function stereoDownmixGains(c: EngineConstants): Partial<Record<string, { left: number; right: number }>> {
  const itu = c.ituCenterCoeff;
  const surround = c.surroundDownmixCoeff;
  return {
    FL: { left: 1, right: 0 },
    FR: { left: 0, right: 1 },
    C: { left: itu, right: itu },
    SL: { left: surround, right: 0 },
    SR: { left: 0, right: surround },
    BL: { left: surround * itu, right: 0 },
    BR: { left: 0, right: surround * itu },
  };
}

// See docs/web_architecture.md "Preview audio graph" — Routing.
const CHANNEL_SIGNAL: Record<string, keyof StemSignals> = {
  FL: "left", FR: "right", C: "mono",
  SL: "surroundLeft", SR: "surroundRight", BL: "surroundLeft", BR: "surroundRight",
  TFL: "heightLeft", TFR: "heightRight", TBL: "heightLeft", TBR: "heightRight",
};

export const POSITIONAL_CHANNELS = Object.keys(speakerCoordinates);

type StemSignals = {
  left: AudioNode;
  right: AudioNode;
  mono: AudioNode;
  surroundLeft: AudioNode;
  surroundRight: AudioNode;
  heightLeft: AudioNode;
  heightRight: AudioNode;
};

// See docs/web_architecture.md "Preview audio graph" — Speaker buses.
type SpeakerBus = {
  muteGain: GainNode;
  masterIn: GainNode;
  masterOut: GainNode;
  encoder: ReturnType<typeof createPositionalEncoder>;
  stereoSend: { gainL: GainNode; gainR: GainNode } | null;
  nativeIndex: number;
};

// See docs/web_architecture.md "Preview audio graph" — Stem sources.
type AudioNodeSet = {
  buffer: AudioBuffer;
  source: AudioBufferSourceNode | null;
  stemGain: GainNode | null;
  postEqGain: GainNode | null;
  sends: Partial<Record<string, GainNode>>;
  ownNodes: AudioNode[];
  lfeGain: GainNode | null;
  lfeFilters: [BiquadFilterNode, BiquadFilterNode] | null;
  analyser: AnalyserNode | null;
  meterSplitter: ChannelSplitterNode | null;
  meterAnalysers: AnalyserNode[];
};

// One stem's routed gain plan — see `computeMixGains`.
type StemMixPlan = {
  stemGainValue: number;
  lfeGainValue: number;
  sends: Partial<Record<string, number>>;
};

// `clipped` latches true once `peak` reaches 0dBFS until the next `stopSources()`.
export type MeterLevel = { rms: number; peak: number; clipped: boolean };

const SILENT_METER_LEVEL: MeterLevel = { rms: 0, peak: 0, clipped: false };

export type MixPreview = {
  stem_routing?: Record<string, Record<string, number>>;
  stem_rebalance?: Record<string, number>;
  stem_eq?: Record<string, string>;
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_source_anchor_strength?: number;
};

// Click-free gain glide; setTargetAtTime ramps from the param's current value,
// so no cancel/anchor bookkeeping is needed.
const GAIN_RAMP_TIME_CONSTANT = 0.008;
function rampGainTo(param: AudioParam, target: number, ctx: BaseAudioContext) {
  param.setTargetAtTime(target, ctx.currentTime, GAIN_RAMP_TIME_CONSTANT);
}

// See docs/web_architecture.md "Preview audio graph" — Routing.
function createStemSends(
  ctx: BaseAudioContext,
  input: AudioNode,
  speakerBuses: Map<string, { muteGain: GainNode }>,
  channels: string[],
  c: EngineConstants,
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

  const surroundLeft = buildSurroundSend(ctx, leftTap, c.surroundHaasMs.left, c.surroundBassCutoffHz, c.diffuseSendBlend);
  const surroundRight = buildSurroundSend(ctx, rightTap, c.surroundHaasMs.right, c.surroundBassCutoffHz, c.diffuseSendBlend);
  const heightShapedLeft = buildHeightSend(ctx, leftTap, c.heightShaping);
  const heightShapedRight = buildHeightSend(ctx, rightTap, c.heightShaping);
  const heightLeft = buildDiffuseSend(ctx, heightShapedLeft.output, c.heightHaasMs.left, c.diffuseSendBlend);
  const heightRight = buildDiffuseSend(ctx, heightShapedRight.output, c.heightHaasMs.right, c.diffuseSendBlend);

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

export type EngineCallbacks = {
  onReady(ready: boolean): void;
  onLoadProgress(progress: number): void;
  onError(message: string | null): void;
  onPlaying(playing: boolean): void;
  onCurrentTime(time: number): void;
  onDuration(duration: number): void;
  onMeasuring(measuring: boolean): void;
  onMaxChannels(maxChannels: number): void;
  onVolume(volume: number): void;
  onMuted(muted: boolean): void;
  onLoop(loop: boolean): void;
};

/**
 * The DAW audio engine for the project preview: owns the `AudioContext`,
 * builds and rewires the Web Audio graph, drives the transport clock, applies
 * every mix/mastering/spatial parameter onto live nodes, and measures meters/
 * loudness. Framework-free by design (see the top-of-file note) so it is
 * testable headless and so `useStemPreview.ts` can stay a thin React binding:
 * sync the latest props/state onto this engine's public fields each render,
 * then call the matching method from the appropriate effect.
 */
export class PreviewAudioEngine {
  readonly supported = Boolean(
    window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext,
  );

  // ---- Inputs, synced from the hook every render (mirrors the old refs) ----
  stems: ProjectStem[] = [];
  scene: { stems?: StemScene } = {};
  mix?: MixPreview;
  sourcePreviewUrl: string | null = null;
  mastering?: MasterPreview;
  layoutChannels: string[] = POSITIONAL_CHANNELS;
  outputMode: OutputMode = "binaural";
  spatialProfile: SpatialProfile = "studio";
  transauralProfile: TransauralProfile = "stereo";
  // Backend-served tunable DSP constants (see masteringProfiles.ts). Synced
  // from the hook before any graph is built; graph methods early-return until
  // it is set (see useStemPreview's constants gating).
  constants!: EngineConstants;
  positionalChannels: string[] = [];
  speakerEnabled: Record<string, boolean> = {};

  // ---- Interactive state the engine itself owns (mirrored out via callbacks) ----
  volume = 1;
  muted = false;

  private context: AudioContext | null = null;
  // See docs/web_architecture.md "Preview audio graph" — Engine gain domains and buses.
  private master: GainNode | null = null;
  private softLimit: WaveShaperNode | null = null;
  private monitorGain: GainNode | null = null;
  private hoaBus: GainNode | null = null;
  private decodeConvolvers: { left: ConvolverNode; right: ConvolverNode; preGain: GainNode | null }[] = [];
  private voicingChain: VoicingChain | null = null;
  private voicingMerger: ChannelMergerNode | null = null;
  private binauralGraphNodes: AudioNode[] = [];
  private crosstalkHoaBus: GainNode | null = null;
  private crosstalkDecodeConvolvers: { left: ConvolverNode; right: ConvolverNode; preGain: GainNode | null }[] = [];
  private xtcConvolvers: XtcConvolvers | null = null;
  private crosstalkVoicingChain: VoicingChain | null = null;
  private crosstalkGraphNodes: AudioNode[] = [];
  private crosstalkGate: GainNode | null = null;
  private speakerBuses: Map<string, SpeakerBus> = new Map();
  private preMasterBus: GainNode | null = null;
  private sidechainSum: GainNode | null = null;
  private sidechainSink: GainNode | null = null;
  private sidechainCompressor: DynamicsCompressorNode | null = null;
  private compGains: GainNode[] = [];
  private compMakeupGain = 1;
  private lfeBus: GainNode | null = null;
  // Stable LFE mastering insert points, same role as `SpeakerBus.masterIn`/
  // `masterOut` for the positional bed — permanently wired `lfeBus ->
  // lfeMasterIn` and `lfeMasterOut -> lfeMuteGain` in `initialize()`;
  // `buildMasteringTopology` rewires only the bridge between them on every
  // mastering-config change (LFE reference-match — see that function's LFE
  // block). upmixer/mastering/match_reference.py does NOT bypass LFE (unlike
  // named-profile EQ), so this is the one mastering stage LFE needs.
  private lfeMasterIn: GainNode | null = null;
  private lfeMasterOut: GainNode | null = null;
  // Gates the LFE bus independently of any stem — same per-speaker mute idea
  // as `SpeakerBus.muteGain`, but LFE has no ambisonic encoder (it bypasses
  // the binaural render entirely), so it needs its own gate on the way into
  // `mergePoint`. Keyed into the same `speakerEnabled` map under "LFE".
  private lfeMuteGain: GainNode | null = null;
  private mergePoint: GainNode | null = null;
  // Passive per-channel level taps for the UI's vertical meters — one
  // analyser per positional speaker bus plus "LFE", fed from `masterOut`
  // (post-mastering, same point feeding the ambisonic encoders) and the LFE
  // bypass, so a meter reflects the actual signal reaching the spatial
  // engine (including mute and mastering).
  private channelAnalysers: Map<string, AnalyserNode> = new Map();
  // Headphone L/R tap: a splitter on the final output node, i.e. the actual
  // binaural signal reaching the listener's headphones, post-mastering.
  private headphoneAnalysers: { splitter: ChannelSplitterNode; left: AnalyserNode; right: AnalyserNode } | null = null;
  // Stereo-downmix bus (BS.775) and discrete native-channel bus, built
  // alongside the binaural bus so switching `outputMode` only re-routes
  // which one reaches `ctx.destination` (see `applyOutputMode`) instead of
  // tearing down and re-decoding the whole graph.
  private stereoMerger: ChannelMergerNode | null = null;
  private nativeMerger: ChannelMergerNode | null = null;
  private binauralGate: GainNode | null = null;
  private stereoGate: GainNode | null = null;
  // PROGRAM domain, native path — kept at unity by `apply()` (native has no
  // loudness correction of its own to apply, see that function), same role
  // as `master` above.
  private nativeOutputGain: GainNode | null = null;
  // Look-ahead true-peak limiter on the native discrete path — mirrors
  // upmixer/mastering/limiter.py::LookAheadLimiter (the bed-level limiter
  // `MasteringChain` now runs), via the "limiter-processor" AudioWorklet
  // (`web/public/limiter.worklet.js`); native otherwise bypasses
  // `master`/`softLimit` entirely and would reach `ctx.destination` with no
  // limiting at all. Falls back to the plain tanh `WaveShaperNode` (same as
  // `softLimit`) if the worklet module fails to load — see `initialize()`.
  private nativeSoftLimit: AudioWorkletNode | WaveShaperNode | null = null;
  // MONITOR domain, native path — mirrors `monitorGain` above. Explicit
  // channelCount/channelCountMode/channelInterpretation (set at creation,
  // see `initialize()`) because a bare GainNode defaults to "max"/"speakers"
  // and would fold the discrete N-channel native bus down to stereo; this
  // node must pass every channel through untouched.
  private nativeMonitorGain: GainNode | null = null;
  private nativeChannelCount = 0;
  private masteringNodes: AudioNode[] = [];
  // Current per-stem EQ filter chain nodes (stem id -> nodes), so
  // `buildStemEqChains` can disconnect exactly its own prior chain on
  // rebuild without touching the fixed `stemGain`/`postEqGain` nodes.
  private stemEqNodes: Map<string, AudioNode[]> = new Map();
  // Decoded EQ FIR asset cache (asset name -> pending/loaded AudioBuffer),
  // keyed independently of profile scope (master vs stem asset names never
  // collide, see EngineConstants.eqFirAssets/stemEqFirAssets) so the same profile
  // reused across many stems or across a rebuild fetches/decodes once. Tied
  // to the single AudioContext this engine creates once per lifetime (see
  // `initialize`) — never needs invalidating within that lifetime.
  private firEqBufferCache: Map<string, Promise<AudioBuffer>> = new Map();
  // Same per-context cache lifetime as `firEqBufferCache`, keyed by
  // `fir_url` instead of a profile name (see `loadCachedRefMatchBuffers`) —
  // the URL carries the asset's `?v=<signature>` query param (see
  // `_project_view` in upmixer_web/api.py), so a genuine server recompute
  // naturally busts this cache instead of serving a stale FIR.
  private refMatchBufferCache: Map<string, Promise<Map<string, AudioBuffer>>> = new Map();
  // Same per-context cache lifetime, keyed by decode filter set name — see
  // loadCachedDecodeFilterChannels. Not cleared in reset(): the profile's
  // decoded Float32Arrays stay valid across a graph rebuild within the same
  // AudioContext.
  private decodeFilterCache: Map<string, Promise<Float32Array[]>> = new Map();
  // Profile currently assigned onto the live convolvers, so loadDecodeFilterSet
  // can skip a redundant assignDecodeFilterBuffers call (32 buffer copies +
  // reassignments) when re-invoked with the profile already in place.
  private assignedDecodeProfile: SpatialProfile | null = null;
  // Same per-context cache lifetime as decodeFilterCache, keyed by XTC filter
  // set name — see loadCachedXtcFilterChannels.
  private xtcFilterCache: Map<string, Promise<Float32Array[]>> = new Map();
  // Same role as assignedDecodeProfile, for the transaural XTC filter set.
  private assignedXtcProfile: TransauralProfile | null = null;
  private resolvedBass: { active: boolean; lfeGainDb: number } = { active: false, lfeGainDb: 0 };
  // Whole-program loudness/true-peak measured once, offline, by
  // `precomputeCorrection` before playback starts — static for the whole
  // play, never ratcheted while the transport is running (see that method's
  // doc comment for why a live re-measurement was the actual source of the
  // "kicks in, then pumps" artifact this replaced).
  private measuredLkfs = -70;
  private preGainTpDbtp = -70;
  // Compared in precomputeCorrection instead of a separate dirty flag; null (reset()) always misses.
  private precomputedForMode: OutputMode | null = null;
  private precomputedForProfile: SpatialProfile | null = null;
  private precomputedForTransauralProfile: TransauralProfile | null = null;
  private nodes: Map<string, AudioNodeSet> = new Map();

  // ---- Public ref-shaped fields: UI reads these each frame, same shape a
  // React ref hands out (`.current`), never reassigned after construction. ----
  readonly stemSpectrum: EngineRef<Map<string, { level: number; centroid: number }>> = engineRef(new Map());
  readonly channelLevels: EngineRef<Map<string, MeterLevel>> = engineRef(new Map());
  readonly stemLevels: EngineRef<Map<string, MeterLevel[]>> = engineRef(new Map());
  readonly headphoneLevels: EngineRef<{ left: MeterLevel; right: MeterLevel }> = engineRef({
    left: SILENT_METER_LEVEL,
    right: SILENT_METER_LEVEL,
  });
  readonly currentTimeRef: EngineRef<number> = engineRef(0);

  private channelTimeDomainBuffer: Float32Array | null = null;
  private appliedGain: Map<string, number> = new Map();
  private timeDomainBuffer: Uint8Array | null = null;
  private frequencyBuffer: Uint8Array | null = null;
  // Playback position/scheduling clock — see transport.ts. Owns the
  // timeline this engine used to keep as a plain `{offset, contextTime}`
  // field directly; `expectedTime`/`startSourcesAt` below delegate to it.
  private readonly transport = new TransportClock();
  private durationRef = 0;
  private playingRef = false;
  private scrub: { wasPlaying: boolean } | null = null;
  private animationFrame: number | null = null;
  // Drives `applyCompressorReduction` independently of the visual rAF loop
  // below — see `startTicker`/`stopTicker`. This affects the actual audible
  // signal (the linked bus-compressor's gain), not just an on-screen meter,
  // so it must keep running even in a backgrounded tab, where browsers fully
  // suspend `requestAnimationFrame` (no paint to schedule against) but only
  // throttle `setInterval` to a slower cadence, never fully stop it.
  private correctionInterval: number | null = null;
  private loopRef = false;
  private initPromise: Promise<void> | null = null;

  constructor(private readonly callbacks: EngineCallbacks) {}

  // ---- Interactive setters (own their gain ramp, unlike the input fields
  // above which are pushed in from outside and only take effect on the next
  // apply()/rebuild the caller triggers). ----

  setVolume(volume: number) {
    this.volume = volume;
    this.callbacks.onVolume(volume);
    this.apply();
  }

  toggleMute() {
    this.muted = !this.muted;
    this.callbacks.onMuted(this.muted);
    this.apply();
  }

  toggleLoop() {
    this.loopRef = !this.loopRef;
    this.transport.setLoop(this.loopRef);
    this.callbacks.onLoop(this.loopRef);
    const durationValue = this.durationRef;
    this.nodes.forEach((node) => {
      if (!node.source) return;
      node.source.loop = this.loopRef;
      if (this.loopRef && durationValue > 0) {
        node.source.loopStart = 0;
        node.source.loopEnd = durationValue;
      }
    });
  }

  private expectedTime(): number {
    const ctx = this.context;
    if (!ctx) return this.currentTimeRef.current;
    return this.transport.position(ctx, this.currentTimeRef.current);
  }

  private stopTicker() {
    if (this.animationFrame !== null) window.cancelAnimationFrame(this.animationFrame);
    this.animationFrame = null;
    if (this.correctionInterval !== null) window.clearInterval(this.correctionInterval);
    this.correctionInterval = null;
  }

  private stopSources() {
    this.nodes.forEach((node) => {
      if (!node.source) return;
      try {
        node.source.stop();
      } catch {
        // already stopped/ended
      }
      node.source.disconnect();
      node.source = null;
    });
    this.stemSpectrum.current.clear();
    this.stemLevels.current.clear();
    // Bus-tap analysers stop receiving signal the instant sources are torn
    // down, but the smoothed level refs they feed (see `measureChannelLevels`)
    // don't decay on their own without a running tick — clear them here so
    // the meters drop to zero on pause/stop instead of freezing at the last
    // sample, and so each play/stop/seek resets the latched clip indicator.
    // Peak-hold markers are a separate ref owned by ChannelMeters and still
    // decay normally.
    this.channelLevels.current.clear();
    this.headphoneLevels.current = { left: SILENT_METER_LEVEL, right: SILENT_METER_LEVEL };
  }

  // Schedules every stem/anchor buffer source to start at `target` on the
  // live `AudioContext`. Returns the `AudioContext` time sources were
  // scheduled to start at, or `null` if there's no context to schedule
  // against.
  private startSourcesAt(target: number): number | null {
    const ctx = this.context;
    if (!ctx) return null;
    const startAt = this.transport.reserveStart(ctx);
    this.nodes.forEach((node) => {
      const source = ctx.createBufferSource();
      source.buffer = node.buffer;
      if (this.loopRef && this.durationRef > 0) {
        source.loop = true;
        source.loopStart = 0;
        source.loopEnd = this.durationRef;
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
  }

  // Reads float time-domain data, not the 8-bit getByteTimeDomainData measureLevels
  // uses for the Haze view — that quantization floor (~-48dBFS/sample) is fine for
  // a glow but not a meter that must report a genuine clip.
  private measureAnalyser(analyser: AnalyserNode): { rms: number; peak: number } {
    const size = analyser.fftSize;
    if (!this.channelTimeDomainBuffer || this.channelTimeDomainBuffer.length !== size) {
      this.channelTimeDomainBuffer = new Float32Array(size);
    }
    const buf = this.channelTimeDomainBuffer;
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
  }

  private measureLevels() {
    for (const stem of this.stems) {
      const node = this.nodes.get(stem.id);
      if (!node?.analyser) continue;
      const base = stem.stem_key.split("@", 1)[0];
      // Mixer-strip meter: a genuine 0..1 amplitude from the float tap,
      // scaled by the stem's applied gain so the bar tracks the fader. The
      // byte read below stays as-is for the Haze glow — it is display-scaled
      // and clamped, which a level meter cannot be. `clipped` is always false
      // for the same reason `measureChannelLevels` never latches: this tap is
      // pre-mastering, where exceeding 1.0 is ordinary headroom use.
      const gainForMeter = this.appliedGain.get(base) ?? 1;
      const previousLevels = this.stemLevels.current.get(base);
      this.stemLevels.current.set(base, node.meterAnalysers.map((channelAnalyser, channel) => {
        const measured = this.measureAnalyser(channelAnalyser);
        return {
          rms: (previousLevels?.[channel]?.rms ?? 0) * 0.7 + measured.rms * gainForMeter * 0.3,
          peak: measured.peak * gainForMeter,
          clipped: false,
        };
      }));
      const size = node.analyser.fftSize;
      if (!this.timeDomainBuffer || this.timeDomainBuffer.length !== size) {
        this.timeDomainBuffer = new Uint8Array(size);
      }
      node.analyser.getByteTimeDomainData(this.timeDomainBuffer);
      let sumSquares = 0;
      for (let i = 0; i < size; i++) {
        const deviation = (this.timeDomainBuffer[i] - 128) / 128;
        sumSquares += deviation * deviation;
      }
      const rms = Math.sqrt(sumSquares / size);
      const gain = this.appliedGain.get(base) ?? 1;
      const level = Math.min(1, rms * gain * 2.5);

      const binCount = node.analyser.frequencyBinCount;
      if (!this.frequencyBuffer || this.frequencyBuffer.length !== binCount) {
        this.frequencyBuffer = new Uint8Array(binCount);
      }
      node.analyser.getByteFrequencyData(this.frequencyBuffer);
      let weightedBin = 0;
      let totalAmplitude = 0;
      for (let i = 0; i < binCount; i++) {
        const amplitude = this.frequencyBuffer[i];
        weightedBin += amplitude * i;
        totalAmplitude += amplitude;
      }
      // Linear bin index is frequency-linear, which crams almost all musical
      // energy into the first few bins; sqrt spreads the centroid out across
      // the radar's radius instead of pinning everything near the center.
      const centroidBin = totalAmplitude > 0 ? weightedBin / totalAmplitude : 0;
      const centroid = binCount > 1 ? Math.sqrt(centroidBin / (binCount - 1)) : 0;
      this.stemSpectrum.current.set(base, { level, centroid });
    }
  }

  private measureChannelLevels() {
    this.channelAnalysers.forEach((analyser, channel) => {
      const { rms, peak } = this.measureAnalyser(analyser);
      const previous = this.channelLevels.current.get(channel);
      this.channelLevels.current.set(channel, {
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
    const headphones = this.headphoneAnalysers;
    if (headphones) {
      const left = this.measureAnalyser(headphones.left);
      const right = this.measureAnalyser(headphones.right);
      const previous = this.headphoneLevels.current;
      this.headphoneLevels.current = {
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
  }

  // Ramped, not a raw .value snap: this runs every rAF tick (~60Hz), and applying
  // `.reduction` as instant jumps at that rate produces audible stepping/zipper noise.
  private applyCompressorReduction() {
    const ctx = this.context;
    const comp = this.sidechainCompressor;
    if (!comp || this.compGains.length === 0) return;
    const gain = 10 ** (comp.reduction / 20) * this.compMakeupGain;
    for (const node of this.compGains) {
      if (ctx) rampGainTo(node.gain, gain, ctx);
      else node.gain.value = gain;
    }
  }

  // See docs/web_architecture.md "Preview audio graph" — Offline correction measurement.
  private async precomputeCorrection(): Promise<void> {
    if (this.outputMode === "native" || this.durationRef <= 0) return;
    const alreadyValid = this.precomputedForMode === this.outputMode
      && (this.outputMode !== "binaural" || this.precomputedForProfile === this.spatialProfile)
      && (this.outputMode !== "transaural" || this.precomputedForTransauralProfile === this.transauralProfile);
    if (alreadyValid) return;
    const ctx = this.context;
    if (!ctx) return;
    this.callbacks.onMeasuring(true);
    try {
      const sr = ctx.sampleRate;
      const { excerpts, totalSeconds } = buildAnalysisExcerpts(this.durationRef);
      // Small tail beyond the analyzed audio itself so decode-filter/EQ
      // convolution ringing finishes decaying inside the rendered buffer
      // instead of being cut off mid-decay.
      const tailSeconds = 0.5;
      const length = Math.ceil((totalSeconds + tailSeconds) * sr);
      const offlineCtx = new OfflineAudioContext(2, length, sr);

      const channelPorts = new Map<string, { input: AudioNode; output: AudioNode }>();
      const offlineBuses = new Map<string, { muteGain: GainNode }>();
      let binaural: ReturnType<typeof buildBinauralGraph> | null = null;
      let crosstalk: ReturnType<typeof buildCrosstalkGraph> | null = null;
      let stereoMerger: ChannelMergerNode | null = null;
      if (this.outputMode === "binaural") {
        binaural = buildBinauralGraph(offlineCtx, this.spatialProfile, this.constants);
        const decodeChannels = await loadCachedDecodeFilterChannels(
          this.decodeFilterCache, offlineCtx, this.constants.decodeFilterSet[this.spatialProfile], fetchDecodeFilterPart,
        );
        assignDecodeFilterBuffers(offlineCtx, binaural.convolverPairs, decodeChannels);
      } else if (this.outputMode === "transaural") {
        crosstalk = buildCrosstalkGraph(offlineCtx, this.transauralProfile, this.constants);
        const decodeChannels = await loadCachedDecodeFilterChannels(
          this.decodeFilterCache, offlineCtx, this.constants.decodeFilterSet.flat, fetchDecodeFilterPart,
        );
        assignDecodeFilterBuffers(offlineCtx, crosstalk.binaural.convolverPairs, decodeChannels);
        const xtcChannels = await loadCachedXtcFilterChannels(
          this.xtcFilterCache, offlineCtx, this.constants.xtcFilterSet[this.transauralProfile], fetchXtcFilterSet,
        );
        assignXtcFilterBuffers(offlineCtx, crosstalk.xtcConvolvers, xtcChannels);
      } else {
        stereoMerger = offlineCtx.createChannelMerger(2);
      }

      for (const channel of this.positionalChannels) {
        const muteGain = offlineCtx.createGain();
        muteGain.gain.value = this.speakerEnabled[channel] === false ? 0 : 1;
        const masterIn = offlineCtx.createGain();
        const masterOut = offlineCtx.createGain();
        muteGain.connect(masterIn);
        offlineBuses.set(channel, { muteGain });
        channelPorts.set(channel, { input: masterIn, output: masterOut });

        if (binaural) {
          const { azim, elev } = positionToAzimuthElevation(speakerCoordinates[channel]);
          const encoder = createPositionalEncoder(offlineCtx, azim, elev);
          masterOut.connect(encoder.in);
          encoder.out.connect(binaural.hoaBus);
        }
        if (crosstalk) {
          const { azim, elev } = positionToAzimuthElevation(speakerCoordinates[channel]);
          const encoder = createPositionalEncoder(offlineCtx, azim, elev);
          masterOut.connect(encoder.in);
          encoder.out.connect(crosstalk.hoaBus);
        }
        if (stereoMerger) {
          const coeffs = stereoDownmixGains(this.constants)[channel];
          if (coeffs) {
            const gainL = offlineCtx.createGain();
            gainL.gain.value = coeffs.left;
            const gainR = offlineCtx.createGain();
            gainR.gain.value = coeffs.right;
            masterOut.connect(gainL).connect(stereoMerger, 0, 0);
            masterOut.connect(gainR).connect(stereoMerger, 0, 1);
          }
        }
      }

      const handle = buildMasteringGraph(
        offlineCtx, channelPorts, this.mastering, this.firEqBufferCache, this.constants,
        { refMatchBufferCache: this.refMatchBufferCache },
      );

      // LFE bridge — mirrors `buildMasteringTopology`'s own LFE ref-match
      // block and `initialize()`'s stable lfeBus -> lfeMasterIn/lfeMasterOut
      // -> mute wiring, ending at the binaural pre-voicing insertion point
      // (D11); stereo excludes LFE entirely, matching BS.775, same as the
      // live graph.
      const lfeBus = offlineCtx.createGain();
      const lfeMuteGain = offlineCtx.createGain();
      lfeMuteGain.gain.value = this.speakerEnabled.LFE === false ? 0 : 1;
      let lfeChainEnd: AudioNode = lfeBus;
      const refCfg = this.mastering?.match_reference;
      if (refCfg?.rms && refCfg.rms_gain_db) {
        const lfeRmsGain = offlineCtx.createGain();
        lfeRmsGain.gain.value = 10 ** (refCfg.rms_gain_db / 20);
        lfeChainEnd.connect(lfeRmsGain);
        lfeChainEnd = lfeRmsGain;
      }
      if (refCfg?.spectrum && refCfg.fir_url && (refCfg.strength ?? 0) > 0 && refCfg.channels?.includes("LFE")) {
        const firLfeRef = buildFirEqNode(offlineCtx, refCfg.strength ?? 1);
        lfeChainEnd.connect(firLfeRef.input);
        lfeChainEnd = firLfeRef.output;
        const buffers = await loadCachedRefMatchBuffers(this.refMatchBufferCache, offlineCtx, refCfg.fir_url, refCfg.channels);
        const buffer = buffers.get("LFE");
        if (buffer) firLfeRef.convolver.buffer = buffer;
      }
      lfeChainEnd.connect(lfeMuteGain);
      if (binaural) {
        lfeMuteGain.connect(binaural.preVoicing, 0, 0);
        lfeMuteGain.connect(binaural.preVoicing, 0, 1);
      }
      if (crosstalk) {
        lfeMuteGain.connect(crosstalk.preVoicing, 0, 0);
        lfeMuteGain.connect(crosstalk.preVoicing, 0, 1);
      }

      // Stems: same already-decoded buffers, EQ profile, and mix gains as
      // the live graph (via `computeMixGains`) — set as static values, no
      // ramp needed since this render is never heard.
      const { anchor, perStem } = this.computeMixGains();
      for (const stem of this.stems) {
        const stemNode = this.nodes.get(stem.id);
        const plan = perStem.get(stem.id);
        if (!stemNode || !plan) continue;
        const base = stem.stem_key.split("@", 1)[0];
        const profile = this.mix?.stem_eq?.[stem.stem_key] || this.mix?.stem_eq?.[base];
        const assetName = profile && profile in this.constants.stemEqFirAssets
          ? this.constants.stemEqFirAssets[profile as StemEqProfileName]
          : null;

        // One shared downstream chain per stem, fed by one source per
        // excerpt window (see `buildAnalysisExcerpts`) — the excerpts never
        // overlap in the offline timeline, so fanning them all into the same
        // gain nodes just sums them in sequence, not on top of each other.
        const stemGain = offlineCtx.createGain();
        stemGain.gain.value = plan.stemGainValue;
        let postEqInput: AudioNode = stemGain;
        if (assetName) {
          const firEq = buildFirEqNode(offlineCtx, 1);
          stemGain.connect(firEq.input);
          postEqInput = firEq.output;
          const buffer = await loadCachedEqBuffer(this.firEqBufferCache, offlineCtx, assetName);
          firEq.convolver.buffer = buffer;
        }
        const postEqGain = offlineCtx.createGain();
        postEqInput.connect(postEqGain);
        const built = createStemSends(offlineCtx, postEqGain, offlineBuses, this.positionalChannels, this.constants);
        for (const [channel, sendGain] of Object.entries(built.sends)) {
          if (sendGain) sendGain.gain.value = plan.sends[channel] || 0;
        }

        const lfeGain = offlineCtx.createGain();
        lfeGain.gain.value = plan.lfeGainValue;
        const lfeFilter1 = offlineCtx.createBiquadFilter();
        lfeFilter1.type = "lowpass";
        lfeFilter1.frequency.value = this.constants.lfeLowpassHz;
        const lfeFilter2 = offlineCtx.createBiquadFilter();
        lfeFilter2.type = "lowpass";
        lfeFilter2.frequency.value = this.constants.lfeLowpassHz;
        lfeGain.connect(lfeFilter1).connect(lfeFilter2).connect(lfeBus);

        for (const excerpt of excerpts) {
          const source = offlineCtx.createBufferSource();
          source.buffer = stemNode.buffer;
          source.start(excerpt.offlineStart, excerpt.originalOffset, excerpt.duration);
          source.connect(stemGain);
          source.connect(lfeGain);
        }
      }
      if (this.sourcePreviewUrl) {
        const anchorNode = this.nodes.get("__source_anchor__");
        if (anchorNode) {
          const input = offlineCtx.createGain();
          const built = createStemSends(offlineCtx, input, offlineBuses, this.positionalChannels, this.constants);
          if (built.sends.FL) built.sends.FL.gain.value = anchor;
          if (built.sends.FR) built.sends.FR.gain.value = anchor;
          for (const excerpt of excerpts) {
            const source = offlineCtx.createBufferSource();
            source.buffer = anchorNode.buffer;
            source.start(excerpt.offlineStart, excerpt.originalOffset, excerpt.duration);
            source.connect(input);
          }
        }
      }

      // Keeps the sidechain compressor node part of the rendered graph — see
      // docs/web_architecture.md "Preview audio graph" — Offline correction measurement.
      const collapseOutput = binaural ? binaural.output : crosstalk ? crosstalk.output : stereoMerger!;
      const finalSum = offlineCtx.createGain();
      collapseOutput.connect(finalSum);
      handle.sidechainSink.connect(finalSum);
      finalSum.connect(offlineCtx.destination);

      const rendered = await offlineCtx.startRendering();
      const left = rendered.getChannelData(0);
      const right = rendered.numberOfChannels > 1 ? rendered.getChannelData(1) : left;
      let sumSquares = 0;
      for (let i = 0; i < left.length; i++) {
        const mono = (left[i] + right[i]) * 0.5;
        sumSquares += mono * mono;
      }
      const meanSquare = sumSquares / left.length;
      // Same -0.691 dB offset / ungated mean-square approximation the live
      // measurement always used — good enough to steer this correction gain
      // toward the mastering target, not to reproduce the exact delivered
      // LKFS — now applied over the whole program instead of a snapshot.
      this.measuredLkfs = meanSquare > 0 ? -0.691 + 10 * Math.log10(meanSquare) : -70;
      this.preGainTpDbtp = Math.max(measureBufferTruePeakDbtp(left), measureBufferTruePeakDbtp(right));
      this.precomputedForMode = this.outputMode;
      this.precomputedForProfile = this.spatialProfile;
      this.precomputedForTransauralProfile = this.transauralProfile;
    } catch {
      // Leave measuredLkfs/preGainTpDbtp at their prior value (or the -70
      // default on first play) and don't mark this mode/profile as
      // measured — apply() still runs safely with that fallback, same as if
      // this measurement had never existed, and the next play retries.
    } finally {
      this.callbacks.onMeasuring(false);
    }
  }

  private tick = () => {
    if (!this.playingRef) return;
    const nextTime = this.expectedTime();
    // No React state callback here: a page-wide 60fps re-render starved canvas/CSS
    // repaints. `currentTimeRef` is read directly by Transport's own rAF loop instead.
    this.currentTimeRef.current = nextTime;
    this.measureLevels();
    this.measureChannelLevels();
    // Not called from here — see `correctionInterval`/`startTicker`: this
    // loop (rAF) is purely visual and browsers fully suspend it in a
    // backgrounded tab, which `applyCompressorReduction`/
    // `measureOutputLoudness` cannot tolerate since they drive the actual
    // audible signal, not just an on-screen meter.
    if (!this.loopRef && this.durationRef > 0 && nextTime >= this.durationRef) {
      this.stopSources();
      this.transport.clear();
      this.playingRef = false;
      this.currentTimeRef.current = this.durationRef;
      this.callbacks.onCurrentTime(this.durationRef);
      this.callbacks.onPlaying(false);
      return;
    }
    this.animationFrame = window.requestAnimationFrame(this.tick);
  };

  private startTicker() {
    this.stopTicker();
    this.animationFrame = window.requestAnimationFrame(this.tick);
    this.correctionInterval = window.setInterval(() => {
      this.applyCompressorReduction();
    }, CORRECTION_STEP_MS);
  }

  pause() {
    const position = this.expectedTime();
    this.stopTicker();
    this.stopSources();
    this.transport.clear();
    this.currentTimeRef.current = position;
    this.playingRef = false;
    this.callbacks.onCurrentTime(position);
    this.callbacks.onPlaying(false);
  }

  reset() {
    this.stopTicker();
    this.stopSources();
    this.nodes.forEach((node) => {
      node.stemGain?.disconnect();
      node.ownNodes.forEach((audioNode) => audioNode.disconnect());
      node.lfeGain?.disconnect();
      node.lfeFilters?.forEach((filter) => filter.disconnect());
      node.analyser?.disconnect();
      node.meterSplitter?.disconnect();
      node.meterAnalysers.forEach((analyser) => analyser.disconnect());
    });
    this.nodes.clear();
    this.stemEqNodes.clear();
    this.stemSpectrum.current.clear();
    this.stemLevels.current.clear();
    this.appliedGain.clear();
    this.masteringNodes.forEach((node) => node.disconnect());
    this.masteringNodes = [];
    this.compGains = [];
    this.resolvedBass = { active: false, lfeGainDb: 0 };
    this.measuredLkfs = -70;
    this.preGainTpDbtp = -70;
    this.precomputedForMode = null;
    this.precomputedForProfile = null;
    this.precomputedForTransauralProfile = null;
    this.speakerBuses.forEach((bus) => {
      bus.muteGain.disconnect();
      bus.masterIn.disconnect();
      bus.masterOut.disconnect();
      bus.encoder.in.disconnect();
      bus.encoder.out.disconnect();
      bus.stereoSend?.gainL.disconnect();
      bus.stereoSend?.gainR.disconnect();
    });
    this.speakerBuses.clear();
    this.channelAnalysers.forEach((analyser) => analyser.disconnect());
    this.channelAnalysers.clear();
    this.channelLevels.current.clear();
    if (this.headphoneAnalysers) {
      this.headphoneAnalysers.splitter.disconnect();
      this.headphoneAnalysers.left.disconnect();
      this.headphoneAnalysers.right.disconnect();
      this.headphoneAnalysers = null;
    }
    this.headphoneLevels.current = { left: SILENT_METER_LEVEL, right: SILENT_METER_LEVEL };
    this.stereoMerger?.disconnect();
    this.stereoMerger = null;
    this.nativeMerger?.disconnect();
    this.nativeMerger = null;
    this.binauralGate?.disconnect();
    this.binauralGate = null;
    this.stereoGate?.disconnect();
    this.stereoGate = null;
    this.crosstalkGate?.disconnect();
    this.crosstalkGate = null;
    this.nativeOutputGain?.disconnect();
    this.nativeOutputGain = null;
    this.nativeSoftLimit?.disconnect();
    this.nativeSoftLimit = null;
    this.nativeMonitorGain?.disconnect();
    this.nativeMonitorGain = null;
    this.nativeChannelCount = 0;
    this.hoaBus?.disconnect();
    this.hoaBus = null;
    this.decodeConvolvers.forEach(({ left, right, preGain }) => {
      left.disconnect();
      right.disconnect();
      preGain?.disconnect();
    });
    this.decodeConvolvers = [];
    this.assignedDecodeProfile = null;
    this.binauralGraphNodes.forEach((node) => node.disconnect());
    this.binauralGraphNodes = [];
    this.voicingChain?.nodes.forEach((node) => node.disconnect());
    this.voicingChain = null;
    this.voicingMerger?.disconnect();
    this.voicingMerger = null;
    this.crosstalkHoaBus?.disconnect();
    this.crosstalkHoaBus = null;
    this.crosstalkDecodeConvolvers.forEach(({ left, right, preGain }) => {
      left.disconnect();
      right.disconnect();
      preGain?.disconnect();
    });
    this.crosstalkDecodeConvolvers = [];
    this.xtcConvolvers = null;
    this.assignedXtcProfile = null;
    this.crosstalkGraphNodes.forEach((node) => node.disconnect());
    this.crosstalkGraphNodes = [];
    this.crosstalkVoicingChain?.nodes.forEach((node) => node.disconnect());
    this.crosstalkVoicingChain = null;
    this.preMasterBus?.disconnect();
    this.preMasterBus = null;
    this.sidechainSum?.disconnect();
    this.sidechainSum = null;
    this.sidechainSink?.disconnect();
    this.sidechainSink = null;
    this.sidechainCompressor?.disconnect();
    this.sidechainCompressor = null;
    this.lfeBus?.disconnect();
    this.lfeBus = null;
    this.lfeMasterIn?.disconnect();
    this.lfeMasterIn = null;
    this.lfeMasterOut?.disconnect();
    this.lfeMasterOut = null;
    this.lfeMuteGain?.disconnect();
    this.lfeMuteGain = null;
    this.mergePoint?.disconnect();
    this.mergePoint = null;
    this.softLimit?.disconnect();
    this.softLimit = null;
    this.monitorGain?.disconnect();
    this.monitorGain = null;
    this.master?.disconnect();
    this.master = null;
    this.transport.clear();
    this.transport.setDuration(0);
    this.initPromise = null;
    this.currentTimeRef.current = 0;
    this.durationRef = 0;
    this.playingRef = false;
    this.scrub = null;
    this.callbacks.onPlaying(false);
    this.callbacks.onCurrentTime(0);
    this.callbacks.onDuration(0);
    this.callbacks.onReady(false);
  }

  dispose() {
    this.reset();
    const activeContext = this.context;
    this.context = null;
    void activeContext?.close();
  }

  // Rebuilds the EQ -> compressor -> bass-shelf chain between each channel's
  // masterIn/masterOut. See docs/web_architecture.md "Preview audio graph" —
  // Engine gain domains and buses. LFE bypasses this chain entirely.
  buildMasteringTopology() {
    const ctx = this.context;
    if (!ctx || this.speakerBuses.size === 0) return;

    this.speakerBuses.forEach((bus) => bus.masterIn.disconnect());
    this.lfeMasterIn?.disconnect();
    this.masteringNodes.forEach((node) => node.disconnect());

    const channelPorts = new Map<string, { input: AudioNode; output: AudioNode }>();
    for (const [channel, bus] of this.speakerBuses.entries()) {
      channelPorts.set(channel, { input: bus.masterIn, output: bus.masterOut });
    }

    const handle = buildMasteringGraph(ctx, channelPorts, this.mastering, this.firEqBufferCache, this.constants, {
      sidechain: this.sidechainSum && this.sidechainSink
        ? { sum: this.sidechainSum, sink: this.sidechainSink }
        : undefined,
      refMatchBufferCache: this.refMatchBufferCache,
    });

    this.masteringNodes = handle.nodes;
    this.compGains = handle.compGains;
    this.sidechainCompressor = handle.compressor;
    this.compMakeupGain = handle.compMakeupGain;
    this.resolvedBass = { active: handle.bassActive, lfeGainDb: handle.bassLfeGainDb };

    // Unlike named-profile EQ (which bypasses LFE), match_reference.py does not
    // bypass LFE, so its RMS gain + spectral FIR bridge lfeMasterIn -> lfeMasterOut
    // here — buildMasteringGraph only wires the positional channel bed.
    const refCfg = this.mastering?.match_reference;
    if (this.lfeMasterIn && this.lfeMasterOut) {
      let lfeChainEnd: AudioNode = this.lfeMasterIn;
      if (refCfg?.rms && refCfg.rms_gain_db) {
        const lfeRmsGain = ctx.createGain();
        lfeRmsGain.gain.value = 10 ** (refCfg.rms_gain_db / 20);
        this.masteringNodes.push(lfeRmsGain);
        lfeChainEnd.connect(lfeRmsGain);
        lfeChainEnd = lfeRmsGain;
      }
      if (refCfg?.spectrum && refCfg.fir_url && (refCfg.strength ?? 0) > 0 && refCfg.channels?.includes("LFE")) {
        const firLfeRef = buildFirEqNode(ctx, refCfg.strength ?? 1);
        this.masteringNodes.push(...firLfeRef.nodes);
        lfeChainEnd.connect(firLfeRef.input);
        lfeChainEnd = firLfeRef.output;
        void loadCachedRefMatchBuffers(
          this.refMatchBufferCache, ctx, refCfg.fir_url, refCfg.channels,
        )
          .then((buffers) => {
            const buffer = buffers.get("LFE");
            if (buffer) firLfeRef.convolver.buffer = buffer;
          })
          .catch(() => {});
      }
      lfeChainEnd.connect(this.lfeMasterOut);
    }
  }

  // Rebuilds each stem's stemGain -> [FIR EQ] -> postEqGain insert (mirrors
  // upmixer/separation/stem_eq.py) whenever mix.stem_eq changes; postEqGain's
  // identity never changes, only the FIR insert feeding it is replaced.
  buildStemEqChains() {
    const ctx = this.context;
    if (!ctx) return;
    for (const stem of this.stems) {
      const node = this.nodes.get(stem.id);
      if (!node || !node.stemGain || !node.postEqGain) continue;
      const base = stem.stem_key.split("@", 1)[0];
      const profile = this.mix?.stem_eq?.[stem.stem_key] || this.mix?.stem_eq?.[base];

      node.stemGain.disconnect();
      (this.stemEqNodes.get(stem.id) || []).forEach((eqNode) => eqNode.disconnect());

      const assetName = profile && profile in this.constants.stemEqFirAssets
        ? this.constants.stemEqFirAssets[profile as StemEqProfileName]
        : null;
      if (assetName) {
        const firEq = buildFirEqNode(ctx, 1);
        this.stemEqNodes.set(stem.id, firEq.nodes);
        node.ownNodes.push(...firEq.nodes);
        node.stemGain.connect(firEq.input);
        firEq.output.connect(node.postEqGain);
        void loadCachedEqBuffer(this.firEqBufferCache, ctx, assetName)
          .then((buffer) => { firEq.convolver.buffer = buffer; })
          .catch(() => {});
      } else {
        this.stemEqNodes.set(stem.id, []);
        node.stemGain.connect(node.postEqGain);
      }
    }
  }

  applySpeakerMute() {
    const ctx = this.context;
    this.speakerBuses.forEach((bus, channel) => {
      const target = this.speakerEnabled[channel] === false ? 0 : 1;
      if (ctx) rampGainTo(bus.muteGain.gain, target, ctx);
      else bus.muteGain.gain.value = target;
    });
    if (this.lfeMuteGain) {
      const target = this.speakerEnabled.LFE === false ? 0 : 1;
      if (ctx) rampGainTo(this.lfeMuteGain.gain, target, ctx);
      else this.lfeMuteGain.gain.value = target;
    }
  }

  // Routes `ctx.destination` to whichever render stage the requested mode
  // needs, and gates `preMasterBus`'s two alternate inputs (binaural decoder
  // vs. stereo downmix) accordingly. Falls back to the stereo path if native
  // is requested but the current output device can't carry that many
  // discrete channels — the selector already disables that option, but a
  // device can change after the fact (e.g. unplugged mid-session).
  applyOutputMode(mode: OutputMode) {
    const ctx = this.context;
    if (!ctx) return;
    const destination = ctx.destination;
    // Route from the monitor-gain node, not the soft-limiter directly — the
    // monitor node runs after the limiter on both paths (see `monitorGain`/
    // `nativeMonitorGain`'s declarations), so it is the actual last stage
    // before headphones/speakers on either one, and is where the user's
    // Transport volume/mute lives.
    const stereoOut = this.monitorGain;
    const nativeOut = this.nativeMonitorGain;
    try { stereoOut?.disconnect(destination); } catch { /* not connected */ }
    try { nativeOut?.disconnect(destination); } catch { /* not connected */ }
    const nCh = this.nativeChannelCount;
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
    if (this.binauralGate) rampGainTo(this.binauralGate.gain, effectiveMode === "binaural" ? 1 : 0, ctx);
    if (this.stereoGate) rampGainTo(this.stereoGate.gain, effectiveMode === "stereo" ? 1 : 0, ctx);
    if (this.crosstalkGate) rampGainTo(this.crosstalkGate.gain, effectiveMode === "transaural" ? 1 : 0, ctx);
  }

  async setOutputSink(deviceId: string) {
    const ctx = this.context as (AudioContext & { setSinkId?: (id: string) => Promise<void> }) | null;
    if (!ctx?.setSinkId) return;
    try {
      await ctx.setSinkId(deviceId);
    } catch {
      // Browser or device rejected the sink switch — stays on the previous device.
    }
  }

  // Profile switch: retune the already-built voicing chain immediately
  // (cheap, no graph rebuild — see buildVoicingChain).
  retuneVoicing(profile: SpatialProfile) {
    if (this.voicingChain) applyVoicingParams(this.voicingChain, this.constants.voicingParams[profile]);
  }

  // Transaural profile switch: same immediate-retune contract as
  // retuneVoicing above, for the crosstalk-cancellation voicing chain.
  retuneCrosstalkVoicing(profile: TransauralProfile) {
    if (this.crosstalkVoicingChain) applyVoicingParams(this.crosstalkVoicingChain, this.constants.transauralVoicingParams[profile]);
  }

  // Loads a profile's decode filter set and assigns each ACN/ear filter into
  // its (already-wired) ConvolverNode — see docs/standards/
  // spatial_audio_engine.md §4. Non-blocking: convolvers with no buffer yet
  // simply output silence (per the Web Audio spec), so preview audio can
  // start immediately while filters fetch/decode in the background.
  async loadDecodeFilterSet(profile: SpatialProfile): Promise<boolean> {
    const ctx = this.context;
    const convolvers = this.decodeConvolvers;
    if (!ctx || convolvers.length !== N_ACN_CHANNELS) return false;
    if (this.assignedDecodeProfile === profile) return true;
    try {
      const channels = await loadCachedDecodeFilterChannels(
        this.decodeFilterCache, ctx, this.constants.decodeFilterSet[profile], fetchDecodeFilterPart,
      );
      // Convolvers may have been rebuilt (or the profile reassigned again)
      // while this fetch/decode was in flight — re-check both before assigning.
      if (this.context !== ctx || this.decodeConvolvers !== convolvers) return false;
      if (this.assignedDecodeProfile === profile) return true;
      assignDecodeFilterBuffers(ctx, convolvers, channels);
      this.assignedDecodeProfile = profile;
      return true;
    } catch {
      return false;
    }
  }

  // Loads the crosstalk graph's internal anechoic "flat" binaural sub-decode
  // — always `flat_o3_decode` regardless of `transauralProfile` (a real
  // speaker/room supplies reverberant coloration on playback, see
  // upmixer/crosstalk/renderer.py::render_crosstalk), so this only ever
  // needs to run once per graph build, not per profile switch. Shares
  // `decodeFilterCache` with `loadDecodeFilterSet` (keyed by filter-set
  // name) so a headphone preview already on "flat" doesn't refetch.
  private async loadCrosstalkDecodeFilterSet(): Promise<boolean> {
    const ctx = this.context;
    const convolvers = this.crosstalkDecodeConvolvers;
    if (!ctx || convolvers.length !== N_ACN_CHANNELS) return false;
    try {
      const channels = await loadCachedDecodeFilterChannels(
        this.decodeFilterCache, ctx, this.constants.decodeFilterSet.flat, fetchDecodeFilterPart,
      );
      if (this.context !== ctx || this.crosstalkDecodeConvolvers !== convolvers) return false;
      assignDecodeFilterBuffers(ctx, convolvers, channels);
      return true;
    } catch {
      return false;
    }
  }

  // Loads a transaural profile's XTC filter set and assigns it onto the
  // (already-wired) 2x2 crosstalk-cancellation convolvers — same
  // non-blocking, dedupe-by-profile contract as loadDecodeFilterSet above.
  async loadXtcFilterSet(profile: TransauralProfile): Promise<boolean> {
    const ctx = this.context;
    const convolvers = this.xtcConvolvers;
    if (!ctx || !convolvers) return false;
    if (this.assignedXtcProfile === profile) return true;
    try {
      const channels = await loadCachedXtcFilterChannels(
        this.xtcFilterCache, ctx, this.constants.xtcFilterSet[profile], fetchXtcFilterSet,
      );
      if (this.context !== ctx || this.xtcConvolvers !== convolvers) return false;
      if (this.assignedXtcProfile === profile) return true;
      assignXtcFilterBuffers(ctx, convolvers, channels);
      this.assignedXtcProfile = profile;
      return true;
    } catch {
      return false;
    }
  }

  apply() {
    const ctx = this.context;
    // The active Spatial Audio Engine profile's own loudness target (if any)
    // overrides the mastering block's target when rendering binaural — see
    // VOICING_PARAMS.loudnessTargetLkfs. All profiles currently leave it null
    // (listening is level-matched to studio/flat), so this falls back to the
    // mastering block's target; the override stays wired for future use.
    const profileLoudnessTarget = this.outputMode === "binaural"
      ? this.constants.voicingParams[this.spatialProfile].loudnessTargetLkfs
      : this.outputMode === "transaural"
      ? this.constants.transauralVoicingParams[this.transauralProfile].loudnessTargetLkfs
      : null;
    const targetLkfs = profileLoudnessTarget ?? this.mastering?.loudness?.target ?? -18;
    const normalize = this.mastering?.loudness?.normalize ?? true;
    // Binaural/transaural's collapse-stage correction is capped small (see
    // BINAURAL_LOUDNESS_MAX_GAIN_DB / CROSSTALK_LOUDNESS_MAX_GAIN_DB) — the
    // bed is already loudness-matched before collapse, so this only nudges
    // for the collapse's own level shift instead of re-running a full match
    // that would inflate loudness.
    const maxGainDb = this.outputMode === "binaural"
      ? this.constants.binauralLoudnessMaxGainDb
      : this.outputMode === "transaural"
      ? this.constants.crosstalkLoudnessMaxGainDb
      : this.constants.loudnessMaxGainDb;
    const loudnessGain = normalize ? loudnessGainFor(this.measuredLkfs, targetLkfs, maxGainDb) : 1;
    // Mirrors normalize_loudness's second gain reduction (upmixer/loudness.py)
    // — gated on the same `normalize` flag as the loudness correction itself:
    // the backend only calls normalize_loudness (which folds in both stages)
    // when loudness_normalize is set, so true-peak protection is skipped
    // exactly when the backend would skip it too. Before the first
    // measurement lands (preGainTpDbtp still its -70 reset default), this is
    // a no-op (see applyTruePeakCeiling).
    const maxTpDbtp = this.mastering?.loudness?.max_tp ?? -1;
    const tpSafeGain = normalize
      ? applyTruePeakCeiling(this.preGainTpDbtp, loudnessGain, maxTpDbtp)
      : loudnessGain;
    // PROGRAM domain — what a bounce of this graph would contain. Carries
    // only the loudness/true-peak correction, never the user's monitor
    // volume (see `master`'s declaration) — so it stays live even during the
    // warm-up (see `monitorGain` below for where silencing actually
    // happens), and the fallback fires only when `ctx` itself is absent.
    if (this.master) {
      if (ctx) rampGainTo(this.master.gain, tpSafeGain, ctx);
      else this.master.gain.value = tpSafeGain;
    }
    // Native bypasses the stereo mastering chain, so it has no
    // loudness-normalize gain of its own to apply — stays at unity.
    if (this.nativeOutputGain) {
      if (ctx) rampGainTo(this.nativeOutputGain.gain, 1, ctx);
      else this.nativeOutputGain.gain.value = 1;
    }
    // MONITOR domain — strictly downstream of the soft limiter and the
    // channel/headphone meter taps (see `monitorGain`'s declaration), so
    // this is the only gain the volume slider and mute drive, and it can
    // never feed back into the limiter's engagement or the meters' reading.
    const monitorTarget = this.muted ? 0 : faderPositionToGain(this.volume);
    for (const node of [this.monitorGain, this.nativeMonitorGain]) {
      if (!node) continue;
      if (ctx) rampGainTo(node.gain, monitorTarget, ctx);
      else node.gain.value = monitorTarget;
    }
    const { anchor, perStem } = this.computeMixGains();
    const source = this.nodes.get("__source_anchor__");
    if (source) {
      // Dry stereo anchor blends straight into the FL/FR speaker buses —
      // mirrors apply_source_anchor blending untouched source L/R into the
      // native front pair, ahead of the ambisonic render. Ramped: this
      // strength is a live-editable mix control, and a raw snap would click
      // exactly when the user drags it.
      const sendFL = source.sends.FL;
      const sendFR = source.sends.FR;
      if (sendFL) { if (ctx) rampGainTo(sendFL.gain, anchor, ctx); else sendFL.gain.value = anchor; }
      if (sendFR) { if (ctx) rampGainTo(sendFR.gain, anchor, ctx); else sendFR.gain.value = anchor; }
    }
    for (const stem of this.stems) {
      const node = this.nodes.get(stem.id);
      const plan = perStem.get(stem.id);
      if (!node || !plan) continue;
      const base = stem.stem_key.split("@", 1)[0];
      this.appliedGain.set(base, plan.stemGainValue);
      // Ramped from here down: mute/solo/rebalance, LFE send, and per-
      // channel routing are all live-editable mix controls (fader drags,
      // mute/solo toggles, dragging a stem's position) — a raw `.value`
      // snap would click on every one of those edits, not just the ones
      // already ramped above (volume/mute, speaker mute, output-mode
      // switch). `rampGainTo`'s short time constant is inaudible as a
      // glide but still lands the new value well within one video frame.
      if (node.stemGain) {
        if (ctx) rampGainTo(node.stemGain.gain, plan.stemGainValue, ctx);
        else node.stemGain.gain.value = plan.stemGainValue;
      }
      if (node.lfeGain) {
        if (ctx) rampGainTo(node.lfeGain.gain, plan.lfeGainValue, ctx);
        else node.lfeGain.gain.value = plan.lfeGainValue;
      }
      for (const channel of this.positionalChannels) {
        const send = node.sends[channel];
        if (!send) continue;
        const sendValue = plan.sends[channel] || 0;
        if (ctx) rampGainTo(send.gain, sendValue, ctx);
        else send.gain.value = sendValue;
      }
    }
  }

  // Pure computation of every stem's routed gain (mute/solo/rebalance/anchor
  // duck), LFE send, and per-channel routing weight — the same math `apply()`
  // used to compute and ramp onto live nodes inline, extracted so
  // `precomputeCorrection()`'s offline measurement graph can be driven by the
  // exact same numbers instead of a hand-duplicated copy that could drift.
  private computeMixGains(): { anchor: number; perStem: Map<string, StemMixPlan> } {
    const anchor = this.mix?.stem_source_anchor_strength || 0;
    const perStem = new Map<string, StemMixPlan>();
    for (const stem of this.stems) {
      const base = stem.stem_key.split("@", 1)[0];
      const value = this.scene.stems?.[stem.stem_key] || this.scene.stems?.[base] || {};
      let route = this.mix?.stem_routing?.[stem.stem_key] || this.mix?.stem_routing?.[base];
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

      const muted = Boolean(this.mix?.stem_solo?.length && !this.mix.stem_solo.includes(stem.stem_key) && !this.mix.stem_solo.includes(base))
        || this.mix?.stem_enabled?.[base] === false || value.enabled === false;
      const gainDb = this.mix?.stem_rebalance?.[base] || 0;
      const stemGainValue = muted ? 0 : (1.0 - anchor * frontFraction) * 10 ** (gainDb / 20);
      const lfeGainValue = muted ? 0 : this.constants.lfeGain * lfeWeight * 10 ** (this.resolvedBass.lfeGainDb / 20);

      const routeScale = estimateRouteScale(route, this.constants.channelGains);
      const sends: Partial<Record<string, number>> = {};
      for (const channel of this.positionalChannels) {
        const weight = route[channel] || 0;
        sends[channel] = weight > 0 ? routeScale * weight * channelGroupGain(channel, this.constants.channelGains) : 0;
      }
      perStem.set(stem.id, { stemGainValue, lfeGainValue, sends });
    }
    return { anchor, perStem };
  }

  initialize(): Promise<void> {
    if (!this.supported) return Promise.resolve();
    if (this.initPromise) return this.initPromise;
    const Constructor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!this.context && Constructor) this.context = new Constructor();
    const ctx = this.context;
    if (!ctx) return Promise.resolve();

    // IIFE so `this.initPromise` is set synchronously before any `await` yields
    // control — an `async` method directly would delay that past the first
    // `await`, letting a concurrent initialize() call race past the guard.
    const promise = (async () => {
    // Falls back to the plain tanh WaveShaper if AudioWorklet is unavailable
    // (e.g. insecure/non-HTTPS origins) — a broken preview is worse than a
    // slightly-less-accurate safety net.
    let nativeLimiterWorkletReady = true;
    try {
      await ctx.audioWorklet.addModule("/limiter.worklet.js");
    } catch {
      nativeLimiterWorkletReady = false;
    }

    const preMasterBusNode = ctx.createGain();
    const lfeBusNode = ctx.createGain();
    const mergePointNode = ctx.createGain();

    // See docs/web_architecture.md "Preview audio graph" — Engine gain domains and buses.
    const binaural = buildBinauralGraph(ctx, this.spatialProfile, this.constants);
    // Crosstalk-cancellation (transaural) render — its own anechoic "flat"
    // binaural sub-decode plus a 2x2 XTC matrix and voicing chain, entirely
    // independent of `binaural` above (which decodes whatever
    // `spatialProfile` the headphone preview has selected). See
    // `buildCrosstalkGraph` (previewGraph.ts) and
    // docs/standards/transaural_speakers.md §1.
    const crosstalk = buildCrosstalkGraph(ctx, this.transauralProfile, this.constants);

    // Binaural/transaural/stereo are alternate render stages that all feed
    // `preMasterBus` through their own gate — see `applyOutputMode`, which
    // zeroes whichever gate isn't the active mode instead of tearing down
    // and rebuilding this graph on every mode switch. `preMasterBus` is a
    // plain passthrough into `mergePoint` now — mastering already happened
    // upstream, per positional channel, before this spatial render (see the
    // `masterIn`/`masterOut` wiring below and `buildMasteringTopology`).
    const binauralGateNode = ctx.createGain();
    binaural.output.connect(binauralGateNode);
    binauralGateNode.connect(preMasterBusNode);

    const crosstalkGateNode = ctx.createGain();
    crosstalk.output.connect(crosstalkGateNode);
    crosstalkGateNode.connect(preMasterBusNode);

    const stereoMergerNode = ctx.createChannelMerger(2);
    const stereoGateNode = ctx.createGain();
    stereoMergerNode.connect(stereoGateNode);
    stereoGateNode.connect(preMasterBusNode);
    preMasterBusNode.connect(mergePointNode);

    // Discrete native bus: one ChannelMerger input per layout channel
    // (including LFE), fed straight from each channel's mute gain — the
    // exact per-speaker signal the channel meters already display.
    const layoutChannelList = this.layoutChannels;
    const nativeMergerNode = ctx.createChannelMerger(Math.max(1, layoutChannelList.length));
    const nativeOutputGainNode = ctx.createGain();
    nativeMergerNode.connect(nativeOutputGainNode);
    // Look-ahead true-peak limiter, native-only — see
    // docs/contracts/preview_export_parity.md Ledger D14. Falls back to the
    // plain tanh WaveShaper if the worklet failed to load.
    const nativeSoftLimitNode: AudioWorkletNode | WaveShaperNode = nativeLimiterWorkletReady
      ? new AudioWorkletNode(ctx, "limiter-processor", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          channelCount: Math.max(1, layoutChannelList.length),
          channelCountMode: "explicit",
          channelInterpretation: "discrete",
          processorOptions: {
            ceilingDb: this.mastering?.loudness?.max_tp ?? -1,
            lookaheadMs: this.constants.limiterLookaheadMs,
            releaseMs: this.constants.limiterReleaseMs,
            safetyMarginDb: this.constants.safetyMarginDb,
            truePeakKernel: Array.from(buildTruePeakKernel()),
            numberOfChannels: Math.max(1, layoutChannelList.length),
          },
        })
      : (() => {
          const fallback = ctx.createWaveShaper();
          fallback.curve = buildSoftLimitCurve(this.constants.softLimitThreshold);
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
    // Sidechain bus-compressor detector — see the field comment and
    // `buildMasteringTopology`. `sink` is a permanent zero-gain tap so the
    // compressor node stays part of the actively rendered graph.
    const sidechainSumNode = ctx.createGain();
    const sidechainSinkNode = ctx.createGain();
    sidechainSinkNode.gain.value = 0;
    sidechainSinkNode.connect(mergePointNode);

    for (const channel of this.positionalChannels) {
      const muteGain = ctx.createGain();
      muteGain.gain.value = this.speakerEnabled[channel] === false ? 0 : 1;
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
      encoder.out.connect(crosstalk.hoaBus);

      const stereoCoeffs = stereoDownmixGains(this.constants)[channel];
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

    // Mirrors render_binaural_delivery's soft_limit(x, 0.95) tanh saturator, run
    // after the volume gain so it only engages as a safety net (upmixer/utils.py).
    const softLimitNode = ctx.createWaveShaper();
    softLimitNode.curve = buildSoftLimitCurve(this.constants.softLimitThreshold);
    softLimitNode.oversample = "4x";
    // MONITOR domain — see docs/web_architecture.md "Preview audio graph".
    const monitorGainNode = ctx.createGain();
    const output = ctx.createGain();
    const lfeMuteGainNode = ctx.createGain();
    lfeMuteGainNode.gain.value = this.speakerEnabled.LFE === false ? 0 : 1;
    // Stable insert points for LFE reference-match, mirroring the
    // positional bed's masterIn/masterOut — `buildMasteringTopology` bridges
    // these two on every rebuild; nothing downstream needs to know.
    const lfeMasterInNode = ctx.createGain();
    const lfeMasterOutNode = ctx.createGain();
    lfeBusNode.connect(lfeMasterInNode);
    lfeMasterOutNode.connect(lfeMuteGainNode);
    // LFE summed before voicing, matching render_binaural — see
    // docs/contracts/preview_export_parity.md Ledger D11.
    lfeMuteGainNode.connect(binaural.preVoicing, 0, 0);
    lfeMuteGainNode.connect(binaural.preVoicing, 0, 1);
    // Same LFE-before-voicing order for the crosstalk render — see
    // `CrosstalkGraphHandle.preVoicing`'s doc comment.
    lfeMuteGainNode.connect(crosstalk.preVoicing, 0, 0);
    lfeMuteGainNode.connect(crosstalk.preVoicing, 0, 1);
    mergePointNode.connect(output).connect(softLimitNode);
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

    this.hoaBus = binaural.hoaBus;
    this.lfeMuteGain = lfeMuteGainNode;
    this.decodeConvolvers = binaural.convolverPairs;
    this.voicingChain = binaural.voicing;
    this.voicingMerger = binaural.output as ChannelMergerNode;
    this.binauralGraphNodes = binaural.nodes;
    this.crosstalkHoaBus = crosstalk.binaural.hoaBus;
    this.crosstalkDecodeConvolvers = crosstalk.binaural.convolverPairs;
    this.xtcConvolvers = crosstalk.xtcConvolvers;
    this.crosstalkVoicingChain = crosstalk.voicing;
    this.crosstalkGraphNodes = [...crosstalk.nodes, ...crosstalk.binaural.nodes];
    this.crosstalkGate = crosstalkGateNode;
    this.speakerBuses = busesMap;
    this.channelAnalysers = channelAnalysersMap;
    this.headphoneAnalysers = { splitter: headphoneSplitter, left: headphoneLeftAnalyser, right: headphoneRightAnalyser };
    this.preMasterBus = preMasterBusNode;
    this.sidechainSum = sidechainSumNode;
    this.sidechainSink = sidechainSinkNode;
    this.lfeBus = lfeBusNode;
    this.lfeMasterIn = lfeMasterInNode;
    this.lfeMasterOut = lfeMasterOutNode;
    this.mergePoint = mergePointNode;
    this.softLimit = softLimitNode;
    this.master = output;
    this.monitorGain = monitorGainNode;
    this.stereoMerger = stereoMergerNode;
    this.nativeMerger = nativeMergerNode;
    this.binauralGate = binauralGateNode;
    this.stereoGate = stereoGateNode;
    this.nativeOutputGain = nativeOutputGainNode;
    this.nativeSoftLimit = nativeSoftLimitNode;
    this.nativeMonitorGain = nativeMonitorGainNode;
    this.nativeChannelCount = layoutChannelList.length;
    this.callbacks.onMaxChannels(ctx.destination.maxChannelCount || 2);
    this.buildMasteringTopology();
    this.applyOutputMode(this.outputMode);
    this.callbacks.onReady(false);
    this.callbacks.onLoadProgress(0);

    // Non-blocking: convolvers output silence until their buffers are
    // assigned, so preview audio can start immediately rather than waiting
    // on this network fetch.
    void this.loadDecodeFilterSet(this.spatialProfile);
    void this.loadCrosstalkDecodeFilterSet();
    void this.loadXtcFilterSet(this.transauralProfile);

    const entries: { id: string; url: string; anchor: boolean }[] = [];
    for (const stem of this.stems) {
      const url = stem.preview_url || stem.audio_url;
      if (url) entries.push({ id: stem.id, url, anchor: false });
    }
    if (this.sourcePreviewUrl) entries.push({ id: "__source_anchor__", url: this.sourcePreviewUrl, anchor: true });

    try {
      let decoded = 0;
      // Stems finish decoding in tight clusters, not evenly spaced —
      // flushing progress straight from each `Promise.all` branch would
      // fire a full page re-render per stem in that cluster, right when the
      // main thread is busiest with decode work. Coalesce same-frame
      // completions into a single callback instead.
      let progressFlushScheduled = false;
      const scheduleProgressFlush = () => {
        if (progressFlushScheduled) return;
        progressFlushScheduled = true;
        window.requestAnimationFrame(() => {
          progressFlushScheduled = false;
          this.callbacks.onLoadProgress(entries.length ? decoded / entries.length : 1);
        });
      };
      await Promise.all(entries.map(async (entry) => {
        const buffer = await loadBuffer(ctx, entry.url);
        decoded += 1;
        scheduleProgressFlush();

        if (entry.anchor) {
          const stemInput = ctx.createGain();
          const built = createStemSends(ctx, stemInput, busesMap, this.positionalChannels, this.constants);
          this.nodes.set(entry.id, {
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
          const built = createStemSends(ctx, postEqGain, busesMap, this.positionalChannels, this.constants);
          const lfeGain = ctx.createGain();
          const lfeFilter1 = ctx.createBiquadFilter();
          const lfeFilter2 = ctx.createBiquadFilter();
          lfeFilter1.type = "lowpass";
          lfeFilter1.frequency.value = this.constants.lfeLowpassHz;
          lfeFilter2.type = "lowpass";
          lfeFilter2.frequency.value = this.constants.lfeLowpassHz;
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
          this.nodes.set(entry.id, {
            buffer, source: null, stemGain, postEqGain, sends: built.sends,
            ownNodes: [stemGain, postEqGain, ...built.ownNodes],
            lfeGain, lfeFilters: [lfeFilter1, lfeFilter2], analyser,
            meterSplitter, meterAnalysers,
          });
        }
      }));
      const durations = Array.from(this.nodes.values())
        .map((node) => node.buffer.duration)
        .filter((value) => Number.isFinite(value) && value > 0);
      if (durations.length) {
        this.durationRef = Math.min(...durations);
        this.transport.setDuration(this.durationRef);
        this.callbacks.onDuration(this.durationRef);
      }
      this.buildStemEqChains();
      this.callbacks.onReady(this.nodes.size > 0);
      this.apply();
    } catch {
      this.callbacks.onError("Unable to load every preview stem.");
      throw new Error("Preview stems are still loading");
    }
    })();
    this.initPromise = promise;
    return promise;
  }

  private requireReady() {
    if (!this.nodes.size) throw new Error("Preview stems are still loading");
    for (const node of this.nodes.values()) {
      if (!node.buffer) throw new Error("Preview stems are still loading");
    }
  }

  moveTo(time: number): number {
    const target = Math.max(0, Math.min(time, this.durationRef || time));
    this.currentTimeRef.current = target;
    this.callbacks.onCurrentTime(target);
    return target;
  }

  async playFrom(time: number): Promise<boolean> {
    try {
      await this.initialize();
      const ctx = this.context;
      if (!ctx || !this.nodes.size) return false;
      this.callbacks.onError(null);
      this.requireReady();
      this.apply();
      const target = this.durationRef > 0 && time >= this.durationRef ? 0 : time;
      this.stopSources();
      await ctx.resume();
      // Whole-program loudness/true-peak measurement (see
      // `precomputeCorrection`'s doc comment) — a no-op after the first
      // successful measurement for the current output mode/spatial profile,
      // so this costs nothing on ordinary replays. Faster than realtime, so
      // even a first-ever play stays effectively instant instead of the old
      // ~0.9s muted warm-up.
      await this.precomputeCorrection();
      this.apply();
      const passStartedAt = ctx.currentTime;
      const startAt = this.startSourcesAt(target);
      if (startAt === null) return false;
      this.transport.commit(ctx, target, startAt, passStartedAt);
      this.currentTimeRef.current = target;
      this.playingRef = true;
      this.callbacks.onPlaying(true);
      this.startTicker();
      return true;
    } catch (nextError) {
      this.callbacks.onMeasuring(false);
      this.stopSources();
      this.transport.clear();
      this.playingRef = false;
      this.callbacks.onPlaying(false);
      this.callbacks.onError(nextError instanceof Error && nextError.message === "Preview stems are still loading"
        ? "Preview stems are still loading. Try again in a moment."
        : `Unable to play every preview stem${nextError instanceof Error && nextError.message ? `: ${nextError.message}` : "."}`);
      return false;
    }
  }

  async playPause(currentTime: number) {
    if (this.playingRef) {
      this.pause();
      return;
    }
    await this.playFrom(currentTime);
  }

  stop() {
    this.pause();
    this.currentTimeRef.current = 0;
    this.callbacks.onCurrentTime(0);
  }

  beginScrub() {
    if (this.scrub) return;
    this.scrub = { wasPlaying: this.playingRef };
    if (this.playingRef) this.pause();
  }

  scrubTo(time: number) {
    const target = Math.max(0, Math.min(time, this.durationRef || time));
    this.currentTimeRef.current = target;
    this.callbacks.onCurrentTime(target);
  }

  async commitScrub(time: number) {
    const activeScrub = this.scrub;
    if (!activeScrub) return;
    this.scrub = null;
    try {
      const target = this.moveTo(time);
      if (activeScrub.wasPlaying && (this.durationRef === 0 || target < this.durationRef)) await this.playFrom(target);
    } catch {
      this.callbacks.onError("Unable to seek every preview stem.");
    }
  }

  async seek(time: number) {
    this.beginScrub();
    this.scrubTo(time);
    await this.commitScrub(time);
  }
}
