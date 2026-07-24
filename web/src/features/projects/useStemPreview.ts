import * as React from "react";
import type { ProjectStem, StemScene } from "@/api";

type AudioNodeSet = {
  buffer: AudioBuffer;
  source: AudioBufferSourceNode | null;
  gain: GainNode;
  panner: PannerNode;
};

type Timeline = { offset: number; contextTime: number };

type MixPreview = {
  stem_routing?: Record<string, Record<string, number>>;
  stem_rebalance?: Record<string, number>;
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_source_anchor_strength?: number;
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

async function loadBuffer(ctx: AudioContext, url: string): Promise<AudioBuffer> {
  const response = await fetch(url);
  if (!response.ok) throw new Error("Preview stem could not be loaded");
  const data = await response.arrayBuffer();
  return ctx.decodeAudioData(data);
}

export function useStemPreview(stems: ProjectStem[], scene: { stems?: StemScene }, mix?: MixPreview, sourcePreviewUrl: string | null = null) {
  const context = React.useRef<AudioContext | null>(null);
  const master = React.useRef<GainNode | null>(null);
  const compensation = React.useRef<BiquadFilterNode | null>(null);
  const limiter = React.useRef<DynamicsCompressorNode | null>(null);
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
      node.gain.disconnect();
      node.panner.disconnect();
    });
    nodes.current.clear();
    limiter.current?.disconnect();
    limiter.current = null;
    master.current?.disconnect();
    master.current = null;
    compensation.current?.disconnect();
    compensation.current = null;
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

  const apply = React.useCallback(() => {
    if (master.current) master.current.gain.value = volume;
    const anchor = mix?.stem_source_anchor_strength || 0;
    const source = nodes.current.get("__source_anchor__");
    if (source) source.gain.gain.value = anchor;
    for (const stem of stemsRef.current) {
      const node = nodes.current.get(stem.id);
      if (!node) continue;
      const base = stem.stem_key.split("@", 1)[0];
      const value = scene.stems?.[stem.stem_key] || scene.stems?.[base] || {};
      const route = mix?.stem_routing?.[stem.stem_key] || mix?.stem_routing?.[base];
      let position = coordinates(value.azimuth_deg || 0, value.elevation_deg || 0);
      if (route) {
        let total = 0;
        let x = 0;
        let y = 0;
        let z = 0;
        for (const [channel, weight] of Object.entries(route)) {
          const speaker = speakerCoordinates[channel];
          if (!speaker || weight <= 0) continue;
          total += weight;
          x += speaker.x * weight;
          y += speaker.y * weight;
          z += speaker.z * weight;
        }
        if (total > 0) position = { x: x / total, y: y / total, z: z / total };
      }
      const gainDb = mix?.stem_rebalance?.[base] || 0;
      node.gain.gain.value = mix?.stem_solo?.length && !mix.stem_solo.includes(stem.stem_key) && !mix.stem_solo.includes(base)
        ? 0
        : mix?.stem_enabled?.[base] === false || value.enabled === false
        ? 0
        : (1.0 - anchor) * 10 ** (gainDb / 20);
      if (node.panner.positionX) {
        node.panner.positionX.value = position.x;
        node.panner.positionY.value = position.y;
        node.panner.positionZ.value = position.z;
      } else {
        node.panner.setPosition(position.x, position.y, position.z);
      }
    }
  }, [mix, scene.stems, volume]);

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
    const limiterNode = ctx.createDynamicsCompressor();
    // Web Audio's built-in HRTF panner convolves with a generic, non-personalized
    // HRIR set whose diffuse-field average rolls off above ~4kHz, reading as
    // "muffled" next to the dry final master. Real binaural renderers (Apple
    // Spatial Audio, Dolby headphone virtualizer) counter this with a fixed
    // diffuse-field compensation shelf on the binaural bus; this mirrors that.
    const hrtfCompensation = ctx.createBiquadFilter();
    hrtfCompensation.type = "highshelf";
    hrtfCompensation.frequency.value = 4000;
    hrtfCompensation.gain.value = 6;
    const output = ctx.createGain();
    limiterNode.connect(hrtfCompensation).connect(output).connect(ctx.destination);
    limiter.current = limiterNode;
    master.current = output;
    compensation.current = hrtfCompensation;
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
          const gain = ctx.createGain();
          const panner = ctx.createPanner();
          panner.panningModel = "HRTF";
          panner.distanceModel = "inverse";
          panner.refDistance = 1;
          panner.rolloffFactor = 0;
          if (entry.anchor) {
            if (panner.positionX) {
              panner.positionX.value = 0;
              panner.positionY.value = 0;
              panner.positionZ.value = -1;
            } else {
              panner.setPosition(0, 0, -1);
            }
          }
          gain.connect(panner).connect(limiterNode);
          nodes.current.set(entry.id, { buffer, source: null, gain, panner });
        }));
        const durations = Array.from(nodes.current.values())
          .map((node) => node.buffer.duration)
          .filter((value) => Number.isFinite(value) && value > 0);
        if (durations.length) {
          durationRef.current = Math.min(...durations);
          setDuration(durationRef.current);
        }
        setReady(nodes.current.size > 0);
        apply();
      } catch {
        setError("Unable to load every preview stem.");
        throw new Error("Preview stems are still loading");
      }
    })();
    initPromise.current = promise;
    return promise;
  }, [apply, sourcePreviewUrl, supported]);

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
        source.connect(node.gain);
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
