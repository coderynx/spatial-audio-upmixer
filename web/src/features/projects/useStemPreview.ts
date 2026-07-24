import * as React from "react";
import type { ProjectStem, StemScene } from "@/api";
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
  buildEqFilters,
  buildExciteCurve,
  buildHrtfCompensation,
  buildSoftLimitCurve,
  connectSeries,
  isDryRouted,
  type BassProfileName,
  type CompProfileName,
  type EqProfileName,
} from "./masteringProfiles";

// One spatialized "leg": the same source can feed either an HRTF PannerNode
// (for localization) or a plain StereoPannerNode (for clarity — see
// masteringProfiles.ts's HRTF-clarity section), never both at once.
// `hrtfSend`/`drySend` hold that hard on/off choice, recomputed in
// `apply()` from the leg's position.
type Leg = {
  hrtfSend: GainNode;
  panner: PannerNode;
  drySend: GainNode;
  stereoPanner: StereoPannerNode;
};

type AudioNodeSet = {
  buffer: AudioBuffer;
  source: AudioBufferSourceNode | null;
  // One leg for ordinary stems. Two (L, R) for the stereo dry-source anchor,
  // fed by `splitter` instead of `source` directly — matches the backend's
  // stereo FL/FR crossfade instead of collapsing the anchor to one point.
  legs: Leg[];
  splitter: ChannelSplitterNode | null;
  // LFE send: present for ordinary stems, absent for the dry source anchor
  // (the backend never routes the anchor's dry blend through LFE).
  lfeGain: GainNode | null;
  lfeFilters: [BiquadFilterNode, BiquadFilterNode] | null;
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

const speakerCoordinates: Record<string, { x: number; y: number; z: number }> = {
  FL: { x: -0.5, y: 0, z: -0.87 }, FR: { x: 0.5, y: 0, z: -0.87 }, C: { x: 0, y: 0, z: -1 },
  SL: { x: -0.94, y: 0, z: 0.34 }, SR: { x: 0.94, y: 0, z: 0.34 }, BL: { x: -0.7, y: 0, z: 0.7 }, BR: { x: 0.7, y: 0, z: 0.7 },
  TFL: { x: -0.5, y: 0.6, z: -0.7 }, TFR: { x: 0.5, y: 0.6, z: -0.7 }, TBL: { x: -0.6, y: 0.6, z: 0.6 }, TBR: { x: 0.6, y: 0.6, z: 0.6 },
};

// Sources share one AudioContext-clock start time so every stem begins on
// the same sample; the lookahead gives the browser time to schedule all
// AudioBufferSourceNode.start() calls before that instant arrives.
const START_LOOKAHEAD_SECONDS = 0.08;

function coordinates(azimuth: number, elevation: number) {
  const az = azimuth * Math.PI / 180;
  const el = elevation * Math.PI / 180;
  return { x: -Math.sin(az) * Math.cos(el), y: Math.sin(el), z: -Math.cos(az) * Math.cos(el) };
}

function createLeg(ctx: AudioContext, hrtfBusNode: GainNode, dryBusNode: GainNode): Leg {
  const hrtfSend = ctx.createGain();
  const panner = ctx.createPanner();
  panner.panningModel = "HRTF";
  panner.distanceModel = "inverse";
  panner.refDistance = 1;
  panner.rolloffFactor = 0;
  hrtfSend.connect(panner).connect(hrtfBusNode);

  const drySend = ctx.createGain();
  const stereoPanner = ctx.createStereoPanner();
  drySend.connect(stereoPanner).connect(dryBusNode);

  return { hrtfSend, panner, drySend, stereoPanner };
}

function setLegPosition(leg: Leg, position: { x: number; y: number; z: number }) {
  if (leg.panner.positionX) {
    leg.panner.positionX.value = position.x;
    leg.panner.positionY.value = position.y;
    leg.panner.positionZ.value = position.z;
  } else {
    leg.panner.setPosition(position.x, position.y, position.z);
  }
  leg.stereoPanner.pan.value = Math.min(Math.max(position.x, -1), 1);
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
) {
  const context = React.useRef<AudioContext | null>(null);
  const master = React.useRef<GainNode | null>(null);
  const softLimit = React.useRef<WaveShaperNode | null>(null);
  // Every leg's HRTF panner feeds `hrtfBus`, which passes through the fixed
  // diffuse-field compensation cascade before joining `dryBus` (the parallel
  // non-HRTF sends) at `preMasterBus`. Per-stem LFE sends skip both and feed
  // `lfeBus` directly (EQ/compressor/bass and the HRTF path are all bypassed
  // for LFE, matching the backend). `preMasterBus` and `lfeBus` sum at
  // `mergePoint` ahead of the soft-limiter.
  const hrtfBus = React.useRef<GainNode | null>(null);
  const dryBus = React.useRef<GainNode | null>(null);
  const hrtfCompNodes = React.useRef<BiquadFilterNode[]>([]);
  const preMasterBus = React.useRef<GainNode | null>(null);
  const lfeBus = React.useRef<GainNode | null>(null);
  const mergePoint = React.useRef<GainNode | null>(null);
  const masteringNodes = React.useRef<AudioNode[]>([]);
  const resolvedBass = React.useRef<{ active: boolean; lfeGainDb: number }>({ active: false, lfeGainDb: 0 });
  const measuredLkfs = React.useRef(-70);
  const nodes = React.useRef<Map<string, AudioNodeSet>>(new Map());
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
  const key = `${stems.map((stem) => `${stem.id}:${stem.preview_url || stem.audio_url}`).join("|")}|${sourcePreviewUrl || ""}`;
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
  }, []);

  const tick = React.useCallback(() => {
    if (!playingRef.current) return;
    const nextTime = expectedTime();
    currentTimeRef.current = nextTime;
    setCurrentTime((current) => Math.abs(current - nextTime) >= 0.01 ? nextTime : current);
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
  }, [expectedTime, stopSources]);

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
      node.legs.forEach((leg) => {
        leg.hrtfSend.disconnect();
        leg.panner.disconnect();
        leg.drySend.disconnect();
        leg.stereoPanner.disconnect();
      });
      node.splitter?.disconnect();
      node.lfeGain?.disconnect();
      node.lfeFilters?.forEach((filter) => filter.disconnect());
    });
    nodes.current.clear();
    masteringNodes.current.forEach((node) => node.disconnect());
    masteringNodes.current = [];
    resolvedBass.current = { active: false, lfeGainDb: 0 };
    measuredLkfs.current = -70;
    hrtfCompNodes.current.forEach((node) => node.disconnect());
    hrtfCompNodes.current = [];
    hrtfBus.current?.disconnect();
    hrtfBus.current = null;
    dryBus.current?.disconnect();
    dryBus.current = null;
    preMasterBus.current?.disconnect();
    preMasterBus.current = null;
    lfeBus.current?.disconnect();
    lfeBus.current = null;
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
  // sub/mid bass bands.
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
    // Bass mono-maker (mono_cutoff_hz) is not realized here: every stem is
    // already collapsed to mono by its PannerNode before HRTF processing, so
    // an explicit mono-maker on the point-source model would be a no-op.
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

  const apply = React.useCallback(() => {
    const targetLkfs = mastering?.loudness?.target ?? -18;
    const normalize = mastering?.loudness?.normalize ?? true;
    const loudnessGain = normalize ? loudnessGainFor(measuredLkfs.current, targetLkfs) : 1;
    if (master.current) master.current.gain.value = volume * loudnessGain;
    const anchor = mix?.stem_source_anchor_strength || 0;
    const source = nodes.current.get("__source_anchor__");
    if (source) {
      // Both legs (FL, FR) sit at the same front/ear-level position, so
      // they route the same way — dry, per `isDryRouted` (see
      // masteringProfiles.ts: never both paths at once, that combs).
      const dry = isDryRouted(speakerCoordinates.FL);
      source.legs.forEach((leg) => {
        leg.hrtfSend.gain.value = dry ? 0 : anchor;
        leg.drySend.gain.value = dry ? anchor : 0;
      });
    }
    for (const stem of stemsRef.current) {
      const node = nodes.current.get(stem.id);
      if (!node) continue;
      const base = stem.stem_key.split("@", 1)[0];
      const value = scene.stems?.[stem.stem_key] || scene.stems?.[base] || {};
      const route = mix?.stem_routing?.[stem.stem_key] || mix?.stem_routing?.[base] || {};
      let position = coordinates(value.azimuth_deg || 0, value.elevation_deg || 0);
      let total = 0;
      let frontWeight = 0;
      let x = 0;
      let y = 0;
      let z = 0;
      for (const [channel, weight] of Object.entries(route)) {
        if (weight <= 0) continue;
        if (channel === "FL" || channel === "FR") frontWeight += weight;
        const speaker = speakerCoordinates[channel];
        if (!speaker) continue;
        total += weight;
        x += speaker.x * weight;
        y += speaker.y * weight;
        z += speaker.z * weight;
      }
      if (total > 0) position = { x: x / total, y: y / total, z: z / total };
      // Only the FL/FR portion of a stem's routing crossfades toward the dry
      // source in the backend (source_anchor.py blends the front zone pair
      // only); other stems' surround/height/back content is left untouched.
      const frontFraction = total > 0 ? frontWeight / total : 0;
      const lfeWeight = route.LFE || 0;

      const muted = Boolean(mix?.stem_solo?.length && !mix.stem_solo.includes(stem.stem_key) && !mix.stem_solo.includes(base))
        || mix?.stem_enabled?.[base] === false || value.enabled === false;
      const gainDb = mix?.stem_rebalance?.[base] || 0;
      const stemGain = muted ? 0 : (1.0 - anchor * frontFraction) * 10 ** (gainDb / 20);
      if (node.lfeGain) {
        node.lfeGain.gain.value = muted
          ? 0
          : LFE_GAIN * lfeWeight * 10 ** (resolvedBass.current.lfeGainDb / 20);
      }
      const leg = node.legs[0];
      // Never leave both sends active for the same source at once — see
      // masteringProfiles.ts's HRTF-clarity comment for why that combs.
      const dry = isDryRouted(position);
      leg.hrtfSend.gain.value = dry ? 0 : stemGain;
      leg.drySend.gain.value = dry ? stemGain : 0;
      setLegPosition(leg, position);
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

    const hrtfBusNode = ctx.createGain();
    const dryBusNode = ctx.createGain();
    const preMasterBusNode = ctx.createGain();
    const lfeBusNode = ctx.createGain();
    const mergePointNode = ctx.createGain();
    // See masteringProfiles.ts's HRTF-clarity section: the compensation
    // cascade sits only on the HRTF sub-bus, before it joins the dry bus, so
    // the dry (already-clear) path can't be over-brightened by it.
    const compNodes = buildHrtfCompensation(ctx);
    connectSeries(hrtfBusNode, compNodes).connect(preMasterBusNode);
    dryBusNode.connect(preMasterBusNode);
    // Backend final stage before loudness measurement: soft_limit(x, 0.95),
    // a tanh saturator above the threshold (upmixer/utils.py). Replaces a
    // plain DynamicsCompressor limiter, which has no counterpart in the
    // delivered master at default settings.
    const softLimitNode = ctx.createWaveShaper();
    softLimitNode.curve = buildSoftLimitCurve();
    softLimitNode.oversample = "4x";
    const output = ctx.createGain();
    lfeBusNode.connect(mergePointNode);
    mergePointNode.connect(softLimitNode).connect(output).connect(ctx.destination);
    hrtfBus.current = hrtfBusNode;
    dryBus.current = dryBusNode;
    hrtfCompNodes.current = compNodes;
    preMasterBus.current = preMasterBusNode;
    lfeBus.current = lfeBusNode;
    mergePoint.current = mergePointNode;
    softLimit.current = softLimitNode;
    master.current = output;
    buildMasteringTopology();
    setReady(false);

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
            // Stereo dry anchor: split into independent L/R legs at the
            // FL/FR speaker positions instead of one mono center point,
            // preserving the dry stereo image the backend keeps (it blends
            // untouched source L/R directly into output FL/FR).
            const legL = createLeg(ctx, hrtfBusNode, dryBusNode);
            const legR = createLeg(ctx, hrtfBusNode, dryBusNode);
            setLegPosition(legL, speakerCoordinates.FL);
            setLegPosition(legR, speakerCoordinates.FR);
            const splitter = ctx.createChannelSplitter(2);
            splitter.connect(legL.hrtfSend, 0);
            splitter.connect(legL.drySend, 0);
            splitter.connect(legR.hrtfSend, 1);
            splitter.connect(legR.drySend, 1);
            nodes.current.set(entry.id, {
              buffer, source: null, legs: [legL, legR], splitter,
              lfeGain: null, lfeFilters: null,
            });
          } else {
            const leg = createLeg(ctx, hrtfBusNode, dryBusNode);
            const lfeGain = ctx.createGain();
            const lfeFilter1 = ctx.createBiquadFilter();
            const lfeFilter2 = ctx.createBiquadFilter();
            lfeFilter1.type = "lowpass";
            lfeFilter1.frequency.value = LFE_LOWPASS_HZ;
            lfeFilter2.type = "lowpass";
            lfeFilter2.frequency.value = LFE_LOWPASS_HZ;
            lfeGain.connect(lfeFilter1).connect(lfeFilter2).connect(lfeBusNode);
            nodes.current.set(entry.id, {
              buffer, source: null, legs: [leg], splitter: null,
              lfeGain, lfeFilters: [lfeFilter1, lfeFilter2],
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
  }, [apply, buildMasteringTopology, sourcePreviewUrl, supported]);

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
        if (node.splitter) {
          source.connect(node.splitter);
        } else {
          for (const leg of node.legs) {
            source.connect(leg.hrtfSend);
            source.connect(leg.drySend);
          }
          if (node.lfeGain) source.connect(node.lfeGain);
        }
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
  };
}
