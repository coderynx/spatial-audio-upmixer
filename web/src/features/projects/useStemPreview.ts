import * as React from "react";
// `ambi-sceneRotator`'s dist build calls the `numeric` library as a bare
// global (`numeric.identity(...)`) instead of importing it — its own bundler
// normally injects that global via a separate <script> tag. Importing the
// package ourselves and attaching it to `globalThis` before any
// `AmbiSceneRotator` is constructed reproduces that environment under Vite.
import numericLib from "numeric";
import AmbiMonoEncoder from "ambisonics/dist/ambi-monoEncoder";
import AmbiSceneRotator from "ambisonics/dist/ambi-sceneRotator";
import AmbiBinDecoder from "ambisonics/dist/ambi-binauralDecoder";
import AmbiHOAloader from "ambisonics/dist/hoa-loader";
import type { ProjectStem, StemScene } from "@/api";

(globalThis as typeof globalThis & { numeric?: unknown }).numeric = numericLib;
import { positionToAzimuthElevation, routingFromAzimuthElevation, speakerCoordinates } from "@/lib/spatial";
import {
  BASS_PROFILES,
  COMP_PROFILES,
  EQ_PROFILES,
  EXCITE_BLEND,
  LFE_GAIN,
  LFE_LOWPASS_HZ,
  LOUDNESS_MAX_GAIN_DB,
  MID_CUTOFF_HZ,
  SUB_CUTOFF_HZ,
  SURROUND_HAAS_MS,
  HEIGHT_HAAS_MS,
  buildDiffuseSend,
  buildEqFilters,
  buildExciteCurve,
  buildHeightSend,
  buildSoftLimitCurve,
  buildSurroundSend,
  channelGroupGain,
  connectSeries,
  estimateRouteScale,
  type BassProfileName,
  type CompProfileName,
  type EqProfileName,
} from "./masteringProfiles";

// Ambisonic order for the preview's virtual-loudspeaker renderer (see the
// module comment below). Higher order = tighter localization, more encoder
// channels ((order+1)^2 = 16 at order 3).
const AMBISONIC_ORDER = 3;

// Bundled default binaural decoding filters (BSD-licensed Aalto University
// order-3 BRIRs shipped with JSAmbisonics' own examples — not a generic
// diffuse-field HRTF, so no compensation EQ is needed). `AmbiHOAloader`
// expects this base name and looks for `<base>_01-08ch.wav`/`_09-16ch.wav`.
const DEFAULT_HRIR_URL = "/hrir/aalto2016_N3.wav";

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
  encoder: AmbiMonoEncoder;
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
};

type Timeline = { offset: number; contextTime: number };

type MixPreview = {
  stem_routing?: Record<string, Record<string, number>>;
  stem_rebalance?: Record<string, number>;
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_source_anchor_strength?: number;
};

type MasterPreview = {
  loudness?: { normalize?: boolean; target?: number; max_tp?: number };
  eq?: { profile?: string | null; strength?: number };
  compressor?: {
    profile?: string | null;
    threshold_db?: number | null;
    ratio?: number | null;
    attack_ms?: number | null;
    release_ms?: number | null;
    knee_db?: number | null;
    makeup_db?: number | null;
  };
  bass?: {
    profile?: string | null;
    sub_gain_db?: number | null;
    mid_gain_db?: number | null;
    mono_cutoff_hz?: number | null;
    excite?: boolean;
    lfe_gain_db?: number | null;
  };
};

// Sources share one AudioContext-clock start time so every stem begins on
// the same sample; the lookahead gives the browser time to schedule all
// AudioBufferSourceNode.start() calls before that instant arrives.
const START_LOOKAHEAD_SECONDS = 0.08;

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

// Cheap, non-gated approximation of BS.1770 integrated loudness: mean-square
// of the mono downmix of every stem summed together, converted with the same
// -0.691 dB offset the K-weighted measurement uses. No K-weighting or gating
// blocks — good enough to steer a preview gain toward the mastering target,
// not to reproduce the exact delivered LKFS.
function measureApproxLkfs(buffers: AudioBuffer[]): number {
  const len = buffers.reduce((min, buffer) => Math.min(min, buffer.length), Infinity);
  if (!Number.isFinite(len) || len <= 0) return -70;
  const mix = new Float32Array(len);
  for (const buffer of buffers) {
    const channelCount = buffer.numberOfChannels || 1;
    for (let channel = 0; channel < channelCount; channel++) {
      const data = buffer.getChannelData(channel);
      for (let i = 0; i < len; i++) mix[i] += data[i] / channelCount;
    }
  }
  let sumSquares = 0;
  for (let i = 0; i < len; i++) sumSquares += mix[i] * mix[i];
  const meanSquare = sumSquares / len;
  if (meanSquare <= 0) return -70;
  return -0.691 + 10 * Math.log10(meanSquare);
}

function loudnessGainFor(measuredLkfs: number, targetLkfs: number): number {
  if (measuredLkfs <= -70) return 1;
  const gainDb = Math.min(targetLkfs - measuredLkfs, LOUDNESS_MAX_GAIN_DB);
  return 10 ** (gainDb / 20);
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
) {
  const layoutChannelsKey = layoutChannels.join(",");
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
  const master = React.useRef<GainNode | null>(null);
  const softLimit = React.useRef<WaveShaperNode | null>(null);
  // The ambisonic rendering core: every positional speaker's encoder feeds
  // `hoaBus` (a plain summing gain, explicit/discrete at 16 channels so
  // multiple encoders' 16-channel outputs add channel-for-channel), which
  // passes through `rotator` (identity — yaw/pitch/roll 0, kept for future
  // head-tracking) into `binDecoder`, which renders the whole channel bed to
  // stereo using the loaded HRIR set. This is the "virtual loudspeaker"
  // model: the renderer sees speaker feeds, not per-stem objects, matching
  // what StemUpmixPipeline actually delivers and letting a speaker be muted
  // independently of any stem. `preMasterBus`/`lfeBus`/`mergePoint` sum the
  // binaural render with the LFE bypass ahead of the soft-limiter, same
  // topology as before.
  const hoaBus = React.useRef<GainNode | null>(null);
  const rotator = React.useRef<AmbiSceneRotator | null>(null);
  const binDecoder = React.useRef<AmbiBinDecoder | null>(null);
  const speakerBuses = React.useRef<Map<string, SpeakerBus>>(new Map());
  const preMasterBus = React.useRef<GainNode | null>(null);
  const lfeBus = React.useRef<GainNode | null>(null);
  // Gates the LFE bus independently of any stem — same per-speaker mute idea
  // as `SpeakerBus.muteGain`, but LFE has no ambisonic encoder (it bypasses
  // the binaural render entirely), so it needs its own gate on the way into
  // `mergePoint`. Keyed into the same `speakerEnabled` map under "LFE".
  const lfeMuteGain = React.useRef<GainNode | null>(null);
  const mergePoint = React.useRef<GainNode | null>(null);
  const masteringNodes = React.useRef<AudioNode[]>([]);
  const resolvedBass = React.useRef<{ active: boolean; lfeGainDb: number }>({ active: false, lfeGainDb: 0 });
  const measuredLkfs = React.useRef(-70);
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
  const [volume, setVolume] = React.useState(0.8);
  const [loop, setLoop] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [ready, setReady] = React.useState(false);
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
  }, []);

  const measureLevels = React.useCallback(() => {
    for (const stem of stemsRef.current) {
      const node = nodes.current.get(stem.id);
      if (!node?.analyser) continue;
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
      const base = stem.stem_key.split("@", 1)[0];
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
  }, []);

  const tick = React.useCallback(() => {
    if (!playingRef.current) return;
    const nextTime = expectedTime();
    currentTimeRef.current = nextTime;
    setCurrentTime((current) => Math.abs(current - nextTime) >= 0.01 ? nextTime : current);
    measureLevels();
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
  }, [expectedTime, measureLevels, stopSources]);

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
    });
    nodes.current.clear();
    stemSpectrum.current.clear();
    appliedGain.current.clear();
    masteringNodes.current.forEach((node) => node.disconnect());
    masteringNodes.current = [];
    resolvedBass.current = { active: false, lfeGainDb: 0 };
    measuredLkfs.current = -70;
    speakerBuses.current.forEach((bus) => {
      bus.muteGain.disconnect();
      bus.encoder.in.disconnect();
      bus.encoder.out.disconnect();
    });
    speakerBuses.current.clear();
    hoaBus.current?.disconnect();
    hoaBus.current = null;
    rotator.current?.in.disconnect();
    rotator.current?.out.disconnect();
    rotator.current = null;
    binDecoder.current?.in.disconnect();
    binDecoder.current?.out.disconnect();
    binDecoder.current = null;
    preMasterBus.current?.disconnect();
    preMasterBus.current = null;
    lfeBus.current?.disconnect();
    lfeBus.current = null;
    lfeMuteGain.current?.disconnect();
    lfeMuteGain.current = null;
    mergePoint.current?.disconnect();
    mergePoint.current = null;
    softLimit.current?.disconnect();
    softLimit.current = null;
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

  // Rebuilds the EQ -> compressor -> bass-shelf chain between `preMasterBus`
  // and `mergePoint` to mirror upmixer/mastering/chain.py's stage order.
  // Stages are entirely omitted when their manifest profile is unset, same
  // as the backend. LFE bypasses this chain (`lfeBus` feeds `mergePoint`
  // directly) since the backend excludes LFE from EQ, compression, and the
  // sub/mid bass bands. Runs on the post-binaural stereo bus (the backend
  // EQs/compresses per output channel; a single shared instance here is
  // tonally equivalent for EQ/bass and a close approximation for the
  // compressor).
  const buildMasteringTopology = React.useCallback(() => {
    const ctx = context.current;
    const bus = preMasterBus.current;
    const merge = mergePoint.current;
    if (!ctx || !bus || !merge) return;

    bus.disconnect();
    masteringNodes.current.forEach((node) => node.disconnect());
    const created: AudioNode[] = [];

    const eqCfg = mastering?.eq;
    const eqNodes = eqCfg?.profile && eqCfg.profile in EQ_PROFILES
      ? buildEqFilters(ctx, EQ_PROFILES[eqCfg.profile as EqProfileName], eqCfg.strength ?? 1)
      : [];
    created.push(...eqNodes);

    const compCfg = mastering?.compressor;
    const compNodes: AudioNode[] = [];
    if (compCfg?.profile && compCfg.profile in COMP_PROFILES) {
      const preset = COMP_PROFILES[compCfg.profile as CompProfileName];
      const comp = ctx.createDynamicsCompressor();
      comp.threshold.value = compCfg.threshold_db ?? preset.threshold_db;
      comp.ratio.value = compCfg.ratio ?? preset.ratio;
      comp.attack.value = (compCfg.attack_ms ?? preset.attack_ms) / 1000;
      comp.release.value = (compCfg.release_ms ?? preset.release_ms) / 1000;
      comp.knee.value = compCfg.knee_db ?? preset.knee_db;
      const makeup = ctx.createGain();
      makeup.gain.value = 10 ** ((compCfg.makeup_db ?? preset.makeup_db) / 20);
      compNodes.push(comp, makeup);
    }
    created.push(...compNodes);

    const bassCfg = mastering?.bass;
    const bassPreset = bassCfg?.profile && bassCfg.profile in BASS_PROFILES
      ? BASS_PROFILES[bassCfg.profile as BassProfileName]
      : undefined;
    const bassActive = Boolean(bassPreset) || Boolean(
      bassCfg && (
        bassCfg.sub_gain_db != null || bassCfg.mid_gain_db != null
        || bassCfg.mono_cutoff_hz != null || bassCfg.lfe_gain_db != null || bassCfg.excite
      ),
    );
    const subGainDb = bassCfg?.sub_gain_db ?? bassPreset?.sub_gain_db ?? 0;
    const midGainDb = bassCfg?.mid_gain_db ?? bassPreset?.mid_gain_db ?? 0;
    const lfeGainDb = bassCfg?.lfe_gain_db ?? bassPreset?.lfe_gain_db ?? 0;
    // Bass mono-maker (mono_cutoff_hz) is not realized here: the channel bed
    // already carries the backend's own L/R signal per channel; there is no
    // separate "mono below cutoff" stage to add on the binaural bus.
    const exciteActive = bassActive && Boolean(bassCfg?.excite || bassPreset?.excite);
    resolvedBass.current = { active: bassActive, lfeGainDb: bassActive ? lfeGainDb : 0 };

    const bassNodes: AudioNode[] = [];
    if (bassActive && subGainDb !== 0) {
      const shelf = ctx.createBiquadFilter();
      shelf.type = "lowshelf";
      shelf.frequency.value = SUB_CUTOFF_HZ;
      shelf.gain.value = subGainDb;
      bassNodes.push(shelf);
    }
    if (bassActive && midGainDb !== 0) {
      const peak = ctx.createBiquadFilter();
      peak.type = "peaking";
      peak.frequency.value = Math.sqrt(SUB_CUTOFF_HZ * MID_CUTOFF_HZ);
      peak.Q.value = 1;
      peak.gain.value = midGainDb;
      bassNodes.push(peak);
    }
    created.push(...bassNodes);

    const preBassPoint = connectSeries(bus, [...eqNodes, ...compNodes]);
    const chainEnd = connectSeries(preBassPoint, bassNodes);
    chainEnd.connect(merge);

    if (exciteActive) {
      const lowpass = ctx.createBiquadFilter();
      lowpass.type = "lowpass";
      lowpass.frequency.value = SUB_CUTOFF_HZ;
      const shaper = ctx.createWaveShaper();
      shaper.curve = buildExciteCurve();
      const blend = ctx.createGain();
      blend.gain.value = EXCITE_BLEND;
      preBassPoint.connect(lowpass);
      lowpass.connect(shaper);
      shaper.connect(blend);
      blend.connect(merge);
      created.push(lowpass, shaper, blend);
    }

    masteringNodes.current = created;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on masteringKey, not `mastering` (see masteringKey comment above)
  }, [masteringKey]);

  React.useEffect(() => {
    buildMasteringTopology();
  }, [masteringKey, buildMasteringTopology]);

  const applySpeakerMute = React.useCallback(() => {
    speakerBuses.current.forEach((bus, channel) => {
      bus.muteGain.gain.value = speakerEnabled[channel] === false ? 0 : 1;
    });
    if (lfeMuteGain.current) lfeMuteGain.current.gain.value = speakerEnabled.LFE === false ? 0 : 1;
  }, [speakerEnabled]);

  React.useEffect(() => {
    applySpeakerMute();
  }, [applySpeakerMute]);

  const toggleSpeaker = React.useCallback((channel: string) => {
    setSpeakerEnabled((current) => ({ ...current, [channel]: current[channel] === false }));
  }, []);

  const loadHrtf = React.useCallback((url: string) => {
    const ctx = context.current;
    const decoder = binDecoder.current;
    if (!ctx || !decoder) return Promise.resolve(false);
    return new Promise<boolean>((resolve) => {
      try {
        const loader = new AmbiHOAloader(ctx, AMBISONIC_ORDER, url, (buffer: AudioBuffer) => {
          decoder.updateFilters(buffer);
          resolve(true);
        });
        loader.load();
      } catch {
        resolve(false);
      }
    });
  }, []);

  const apply = React.useCallback(() => {
    const targetLkfs = mastering?.loudness?.target ?? -18;
    const normalize = mastering?.loudness?.normalize ?? true;
    const loudnessGain = normalize ? loudnessGainFor(measuredLkfs.current, targetLkfs) : 1;
    if (master.current) master.current.gain.value = volume * loudnessGain;
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
  }, [mix, scene.stems, volume, mastering]);

  React.useEffect(() => {
    apply();
  }, [apply]);

  const initialize = React.useCallback(() => {
    if (!supported) return Promise.resolve();
    if (initPromise.current) return initPromise.current;
    const Constructor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!context.current && Constructor) context.current = new Constructor();
    const ctx = context.current;
    if (!ctx) return Promise.resolve();

    const preMasterBusNode = ctx.createGain();
    const lfeBusNode = ctx.createGain();
    const mergePointNode = ctx.createGain();

    // Shared ambisonic core: every speaker's encoder sums into `hoaBusNode`
    // (explicit/discrete at 16ch so multiple encoders' outputs add
    // channel-for-channel instead of being up/down-mixed), then the
    // rotator (identity for now) and binaural decoder render the whole bed
    // to stereo. See this file's top comment.
    const nCh = (AMBISONIC_ORDER + 1) * (AMBISONIC_ORDER + 1);
    const hoaBusNode = ctx.createGain();
    hoaBusNode.channelCount = nCh;
    hoaBusNode.channelCountMode = "explicit";
    hoaBusNode.channelInterpretation = "discrete";
    const rotatorNode = new AmbiSceneRotator(ctx, AMBISONIC_ORDER);
    const binDecoderNode = new AmbiBinDecoder(ctx, AMBISONIC_ORDER);
    hoaBusNode.connect(rotatorNode.in);
    rotatorNode.out.connect(binDecoderNode.in);
    binDecoderNode.out.connect(preMasterBusNode);

    const busesMap = new Map<string, SpeakerBus>();
    for (const channel of positionalChannelsRef.current) {
      const muteGain = ctx.createGain();
      muteGain.gain.value = speakerEnabled[channel] === false ? 0 : 1;
      const encoder = new AmbiMonoEncoder(ctx, AMBISONIC_ORDER);
      const { azim, elev } = positionToAzimuthElevation(speakerCoordinates[channel]);
      encoder.azim = azim;
      encoder.elev = elev;
      encoder.updateGains();
      muteGain.connect(encoder.in);
      encoder.out.connect(hoaBusNode);
      busesMap.set(channel, { muteGain, encoder });
    }

    // Backend final stage before loudness measurement: soft_limit(x, 0.95),
    // a tanh saturator above the threshold (upmixer/utils.py). Replaces a
    // plain DynamicsCompressor limiter, which has no counterpart in the
    // delivered master at default settings.
    const softLimitNode = ctx.createWaveShaper();
    softLimitNode.curve = buildSoftLimitCurve();
    softLimitNode.oversample = "4x";
    const output = ctx.createGain();
    const lfeMuteGainNode = ctx.createGain();
    lfeMuteGainNode.gain.value = speakerEnabled.LFE === false ? 0 : 1;
    lfeBusNode.connect(lfeMuteGainNode).connect(mergePointNode);
    mergePointNode.connect(softLimitNode).connect(output).connect(ctx.destination);

    hoaBus.current = hoaBusNode;
    lfeMuteGain.current = lfeMuteGainNode;
    rotator.current = rotatorNode;
    binDecoder.current = binDecoderNode;
    speakerBuses.current = busesMap;
    preMasterBus.current = preMasterBusNode;
    lfeBus.current = lfeBusNode;
    mergePoint.current = mergePointNode;
    softLimit.current = softLimitNode;
    master.current = output;
    buildMasteringTopology();
    setReady(false);

    // Non-blocking: the decoder starts on JSAmbisonics' built-in cardioid
    // fallback and swaps to the real BRIR set once it's fetched, so preview
    // audio can start immediately rather than waiting on this network call.
    void loadHrtf(DEFAULT_HRIR_URL);

    const entries: { id: string; url: string; anchor: boolean }[] = [];
    for (const stem of stemsRef.current) {
      const url = stem.preview_url || stem.audio_url;
      if (url) entries.push({ id: stem.id, url, anchor: false });
    }
    if (sourcePreviewUrl) entries.push({ id: "__source_anchor__", url: sourcePreviewUrl, anchor: true });

    const promise = (async () => {
      try {
        await Promise.all(entries.map(async (entry) => {
          const buffer = await loadBuffer(ctx, entry.url);

          if (entry.anchor) {
            const stemInput = ctx.createGain();
            const built = createStemSends(ctx, stemInput, busesMap, positionalChannelsRef.current);
            nodes.current.set(entry.id, {
              buffer, source: null, stemGain: null, sends: built.sends, ownNodes: [stemInput, ...built.ownNodes],
              lfeGain: null, lfeFilters: null, analyser: null,
            });
          } else {
            const stemGain = ctx.createGain();
            const built = createStemSends(ctx, stemGain, busesMap, positionalChannelsRef.current);
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
            nodes.current.set(entry.id, {
              buffer, source: null, stemGain, sends: built.sends, ownNodes: [stemGain, ...built.ownNodes],
              lfeGain, lfeFilters: [lfeFilter1, lfeFilter2], analyser,
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
        const stemBuffers = stemsRef.current
          .map((stem) => nodes.current.get(stem.id)?.buffer)
          .filter((buffer): buffer is AudioBuffer => Boolean(buffer));
        if (stemBuffers.length) measuredLkfs.current = measureApproxLkfs(stemBuffers);
        setReady(nodes.current.size > 0);
        apply();
      } catch {
        setError("Unable to load every preview stem.");
        throw new Error("Preview stems are still loading");
      }
    })();
    initPromise.current = promise;
    return promise;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- speakerEnabled read only for the initial mute value; live changes go through applySpeakerMute
  }, [apply, buildMasteringTopology, loadHrtf, sourcePreviewUrl, supported]);

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
        source.start(startAt, target);
        node.source = source;
      });
      timeline.current = { offset: target, contextTime: startAt };
      currentTimeRef.current = target;
      playingRef.current = true;
      setPlaying(true);
      startTicker();
      return true;
    } catch (nextError) {
      stopSources();
      timeline.current = null;
      playingRef.current = false;
      setPlaying(false);
      setError(nextError instanceof Error && nextError.message === "Preview stems are still loading"
        ? "Preview stems are still loading. Try again in a moment."
        : `Unable to play every preview stem${nextError instanceof Error && nextError.message ? `: ${nextError.message}` : "."}`);
      return false;
    }
  }, [apply, initialize, requireReady, startTicker, stopSources]);

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

  return {
    supported,
    ready,
    playing,
    currentTime,
    duration,
    volume,
    loop,
    error,
    setVolume,
    playPause,
    stop,
    seek,
    beginScrub,
    scrubTo,
    commitScrub,
    toggleLoop,
    stemSpectrum,
    speakerEnabled,
    toggleSpeaker,
    loadHrtf,
  };
}
