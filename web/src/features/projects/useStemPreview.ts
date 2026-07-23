import * as React from "react";
import type { ProjectStem, StemScene } from "@/api";

type AudioNodeSet = {
  element: HTMLAudioElement;
  source: MediaElementAudioSourceNode;
  gain: GainNode;
  panner: PannerNode;
};

type Timeline = { offset: number; contextTime: number };

type MixPreview = {
  stem_routing?: Record<string, Record<string, number>>;
  stem_rebalance?: Record<string, number>;
  stem_enabled?: Record<string, boolean>;
};

const speakerCoordinates: Record<string, { x: number; y: number; z: number }> = {
  FL: { x: -0.5, y: 0, z: -0.87 }, FR: { x: 0.5, y: 0, z: -0.87 }, C: { x: 0, y: 0, z: -1 },
  SL: { x: -0.94, y: 0, z: 0.34 }, SR: { x: 0.94, y: 0, z: 0.34 }, BL: { x: -0.7, y: 0, z: 0.7 }, BR: { x: 0.7, y: 0, z: 0.7 },
  TFL: { x: -0.5, y: 0.6, z: -0.7 }, TFR: { x: 0.5, y: 0.6, z: -0.7 }, TBL: { x: -0.6, y: 0.6, z: 0.6 }, TBR: { x: 0.6, y: 0.6, z: 0.6 },
};

function coordinates(azimuth: number, elevation: number) {
  const az = azimuth * Math.PI / 180;
  const el = elevation * Math.PI / 180;
  return { x: -Math.sin(az) * Math.cos(el), y: Math.sin(el), z: -Math.cos(az) * Math.cos(el) };
}

function waitForEvent(element: HTMLMediaElement) {
  return new Promise<void>((resolve, reject) => {
    const complete = () => {
      element.removeEventListener("seeked", complete);
      element.removeEventListener("error", failed);
      resolve();
    };
    const failed = () => {
      element.removeEventListener("seeked", complete);
      element.removeEventListener("error", failed);
      reject(new Error("Preview stem could not be loaded"));
    };
    element.addEventListener("seeked", complete, { once: true });
    element.addEventListener("error", failed, { once: true });
  });
}

export function useStemPreview(stems: ProjectStem[], scene: { stems?: StemScene }, mix?: MixPreview) {
  const context = React.useRef<AudioContext | null>(null);
  const master = React.useRef<GainNode | null>(null);
  const compensation = React.useRef<BiquadFilterNode | null>(null);
  const nodes = React.useRef<Map<string, AudioNodeSet>>(new Map());
  const stemsRef = React.useRef(stems);
  const timeline = React.useRef<Timeline | null>(null);
  const currentTimeRef = React.useRef(0);
  const durationRef = React.useRef(0);
  const playingRef = React.useRef(false);
  const scrub = React.useRef<{ wasPlaying: boolean } | null>(null);
  const animationFrame = React.useRef<number | null>(null);
  const lastCorrection = React.useRef(0);
  const [playing, setPlaying] = React.useState(false);
  const [currentTime, setCurrentTime] = React.useState(0);
  const [duration, setDuration] = React.useState(0);
  const [volume, setVolume] = React.useState(0.8);
  const [error, setError] = React.useState<string | null>(null);
  const [ready, setReady] = React.useState(false);
  const [supported] = React.useState(() => Boolean(window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext));
  const key = stems.map((stem) => `${stem.id}:${stem.preview_url || stem.audio_url}`).join("|");
  stemsRef.current = stems;

  const expectedTime = React.useCallback(() => {
    const activeTimeline = timeline.current;
    const ctx = context.current;
    if (!activeTimeline || !ctx) return currentTimeRef.current;
    return Math.min(durationRef.current, activeTimeline.offset + ctx.currentTime - activeTimeline.contextTime);
  }, []);

  const stopTicker = React.useCallback(() => {
    if (animationFrame.current !== null) window.cancelAnimationFrame(animationFrame.current);
    animationFrame.current = null;
  }, []);

  const tick = React.useCallback(() => {
    if (!playingRef.current) return;
    const nextTime = expectedTime();
    currentTimeRef.current = nextTime;
    setCurrentTime((current) => Math.abs(current - nextTime) >= 0.01 ? nextTime : current);
    const now = performance.now();
    if (now - lastCorrection.current >= 250) {
      lastCorrection.current = now;
      nodes.current.forEach((node) => {
        if (Math.abs(node.element.currentTime - nextTime) > 0.02) node.element.currentTime = nextTime;
      });
    }
    if (durationRef.current > 0 && nextTime >= durationRef.current) {
      nodes.current.forEach((node) => node.element.pause());
      timeline.current = null;
      playingRef.current = false;
      setPlaying(false);
      return;
    }
    animationFrame.current = window.requestAnimationFrame(tick);
  }, [expectedTime]);

  const startTicker = React.useCallback(() => {
    stopTicker();
    lastCorrection.current = performance.now();
    animationFrame.current = window.requestAnimationFrame(tick);
  }, [stopTicker, tick]);

  const pause = React.useCallback(() => {
    const position = expectedTime();
    stopTicker();
    nodes.current.forEach((node) => node.element.pause());
    timeline.current = null;
    currentTimeRef.current = position;
    playingRef.current = false;
    setCurrentTime(position);
    setPlaying(false);
  }, [expectedTime, stopTicker]);

  const reset = React.useCallback(() => {
    stopTicker();
    nodes.current.forEach((node) => {
      node.element.pause();
      node.source.disconnect();
      node.gain.disconnect();
      node.panner.disconnect();
    });
    nodes.current.clear();
    master.current?.disconnect();
    master.current = null;
    compensation.current?.disconnect();
    compensation.current = null;
    timeline.current = null;
    currentTimeRef.current = 0;
    durationRef.current = 0;
    playingRef.current = false;
    scrub.current = null;
    setPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    setReady(false);
  }, [stopTicker]);

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
      node.gain.gain.value = mix?.stem_enabled?.[base] === false || value.enabled === false
        ? 0
        : 10 ** (gainDb / 20);
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
    if (!supported || nodes.current.size) return;
    const Constructor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!context.current && Constructor) context.current = new Constructor();
    const ctx = context.current;
    if (!ctx) return;
    const limiter = ctx.createDynamicsCompressor();
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
    limiter.connect(hrtfCompensation).connect(output).connect(ctx.destination);
    master.current = output;
    compensation.current = hrtfCompensation;
    setReady(false);
    const refreshReady = () => {
      setReady(nodes.current.size > 0 && Array.from(nodes.current.values()).every(
        (node) => node.element.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA,
      ));
    };
    const refreshDuration = () => {
      const durations = Array.from(nodes.current.values())
        .map((node) => node.element.duration)
        .filter((value) => Number.isFinite(value) && value > 0);
      if (durations.length !== nodes.current.size) return;
      const nextDuration = Math.min(...durations);
      durationRef.current = nextDuration;
      setDuration(nextDuration);
    };
    for (const stem of stemsRef.current) {
      const url = stem.preview_url || stem.audio_url;
      if (!url) continue;
      const element = new Audio(url);
      element.preload = "auto";
      element.crossOrigin = "anonymous";
      const source = ctx.createMediaElementSource(element);
      const gain = ctx.createGain();
      const panner = ctx.createPanner();
      panner.panningModel = "HRTF";
      panner.distanceModel = "inverse";
      panner.refDistance = 1;
      panner.rolloffFactor = 0;
      source.connect(gain).connect(panner).connect(limiter);
      nodes.current.set(stem.id, { element, source, gain, panner });
      element.addEventListener("loadedmetadata", refreshDuration);
      element.addEventListener("canplay", refreshReady);
      element.addEventListener("error", () => {
        setError("Unable to load every preview stem.");
        if (playingRef.current) pause();
      });
      element.addEventListener("ended", () => {
        if (playingRef.current) pause();
      });
    }
    refreshReady();
    refreshDuration();
  }, [pause, supported]);

  React.useEffect(() => {
    initialize();
  }, [initialize, key]);

  const requireReady = React.useCallback(() => {
    for (const node of nodes.current.values()) {
      if (node.element.error) throw new Error("Preview stem could not be loaded");
      if (node.element.readyState < HTMLMediaElement.HAVE_FUTURE_DATA) {
        throw new Error("Preview stems are still loading");
      }
    }
  }, []);

  const moveTo = React.useCallback(async (time: number) => {
    const target = Math.max(0, Math.min(time, durationRef.current || time));
    await Promise.all(Array.from(nodes.current.values()).map(async (node) => {
      if (Math.abs(node.element.currentTime - target) < 0.001) return;
      const moved = waitForEvent(node.element);
      node.element.currentTime = target;
      await moved;
    }));
    currentTimeRef.current = target;
    setCurrentTime(target);
    return target;
  }, []);

  const playFrom = React.useCallback(async (time = currentTimeRef.current) => {
    initialize();
    const ctx = context.current;
    if (!ctx || !nodes.current.size) return false;
    try {
      setError(null);
      requireReady();
      apply();
      const target = durationRef.current > 0 && time >= durationRef.current ? 0 : time;
      const needsMove = Array.from(nodes.current.values()).some((node) => Math.abs(node.element.currentTime - target) >= 0.001);
      if (needsMove) await moveTo(target);
      const resume = ctx.resume();
      const starts = Array.from(nodes.current.values()).map((node) => node.element.play());
      await Promise.all([resume, ...starts]);
      const contextTime = ctx.currentTime;
      timeline.current = { offset: target, contextTime };
      currentTimeRef.current = target;
      playingRef.current = true;
      setPlaying(true);
      startTicker();
      return true;
    } catch (nextError) {
      nodes.current.forEach((node) => node.element.pause());
      timeline.current = null;
      playingRef.current = false;
      setPlaying(false);
      setError(nextError instanceof Error && nextError.message === "Preview stems are still loading"
        ? "Preview stems are still loading. Try again in a moment."
        : `Unable to play every preview stem${nextError instanceof Error && nextError.message ? `: ${nextError.message}` : "."}`);
      return false;
    }
  }, [apply, initialize, moveTo, requireReady, startTicker]);

  const playPause = React.useCallback(async () => {
    if (playingRef.current) {
      pause();
      return;
    }
    await playFrom();
  }, [pause, playFrom]);

  const stop = React.useCallback(() => {
    pause();
    nodes.current.forEach((node) => {
      node.element.currentTime = 0;
    });
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
      const target = await moveTo(time);
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

  return {
    supported,
    ready,
    playing,
    currentTime,
    duration,
    volume,
    error,
    setVolume,
    playPause,
    stop,
    seek,
    beginScrub,
    scrubTo,
    commitScrub,
  };
}
