import * as React from "react";
import { speakerCoordinates } from "@/lib/spatial";
import { isTauriRuntime } from "@/runtime";
import type { EngineConstants, SpatialProfile, TransauralProfile } from "./masteringProfiles";
import {
  PreviewHost,
  POSITIONAL_CHANNELS,
  SILENT_LOUDNESS,
  type EngineCallbacks,
  type OutputMode,
} from "./audioEngine";
import { createPreviewMonitor, type PreviewProgramme } from "./previewProgramme";

export type { OutputMode, MasterMeters, MeterLevel, MixPreview } from "./audioEngine";
export type { LoudnessSummary } from "./audioEngine";
export type { SpatialProfile, TransauralProfile } from "./masteringProfiles";
export { applyTruePeakCeiling } from "./audioEngine";

// Thin React binding over PreviewHost (audioEngine.ts): it prepares the current
// programme and monitor state, then wires effects to the matching host method.
export function useStemPreview(
  programme: PreviewProgramme | null,
  {
    outputMode = "binaural",
    spatialProfile = "studio",
    transauralProfile = "stereo",
    constants = null,
    masteringBypassed = false,
    matchBypassed = false,
    appleHeadTracking = true,
  }: {
    outputMode?: OutputMode;
    spatialProfile?: SpatialProfile;
    transauralProfile?: TransauralProfile;
    constants?: EngineConstants | null;
    masteringBypassed?: boolean;
    matchBypassed?: boolean;
    appleHeadTracking?: boolean;
  } = {},
) {
  const layoutChannels = programme?.layoutChannels ?? POSITIONAL_CHANNELS;
  const layoutChannelsKey = layoutChannels.join(",");
  // Stable-identity, layout-scoped speaker list: drives the ambisonic
  // speaker-bus construction, so switching e.g. 7.1.4 -> 5.1 tears down and
  // rebuilds only the speakers the chosen layout actually has.
  const positionalChannels = React.useMemo(
    () => layoutChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel]),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on layoutChannelsKey, not `layoutChannels` (fresh array identity every render)
    [layoutChannelsKey],
  );

  const [playing, setPlaying] = React.useState(false);
  const [currentTime, setCurrentTime] = React.useState(0);
  const [duration, setDuration] = React.useState(0);
  // Default to unity (fader position 1.0): with the PROGRAM/MONITOR split
  // the engine maintains, unity means "hear the render exactly as it would
  // export" — see `faderPositionToGain` in lib/fader.ts. There is no
  // headroom above it to give up by defaulting lower.
  const [volume, setVolumeState] = React.useState(1);
  // Master mute — independent of `volume` so unmuting restores the exact
  // prior level instead of whatever a slider drag left it at.
  const [muted, setMutedState] = React.useState(false);
  const [loop, setLoop] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [ready, setReady] = React.useState(false);
  const [loadProgress, setLoadProgress] = React.useState(0);
  // True while the fast excerpt loudness pass (see `measureIfNeeded` in
  // audioEngine.ts) is in flight — surfaced so the UI can show a
  // "calibrating" status in place of the transport during that window
  // instead of looking stalled. The exact whole-programme pass that follows
  // runs in the background and does not reopen this.
  const [measuring, setMeasuring] = React.useState(false);
  const [measureProgress, setMeasureProgress] = React.useState(0);
  // Measured loudness and the target it is normalized to. React state rather
  // than a meter ref: it moves when a measurement lands, not per frame.
  const [loudness, setLoudness] = React.useState(SILENT_LOUDNESS);
  const [maxChannels, setMaxChannels] = React.useState(2);
  const [outputDevices, setOutputDevices] = React.useState<MediaDeviceInfo[]>([]);
  const [outputDeviceId, setOutputDeviceIdState] = React.useState("");
  const [engineKind, setEngineKind] = React.useState<"native" | "wasm">(
    isTauriRuntime ? "native" : "wasm",
  );
  const [fallbackReason, setFallbackReason] = React.useState<string | null>(null);
  // Per-speaker mute state — independent of stem mute/solo, since the
  // renderer input is the channel bed, not the stems.
  // "LFE" is included even though it has no ambisonic bus.
  const [speakerEnabled, setSpeakerEnabled] = React.useState<Record<string, boolean>>(
    () => Object.fromEntries([...positionalChannels, "LFE"].map((channel) => [channel, true])),
  );
  const [speakerSolo, setSpeakerSolo] = React.useState<Set<string>>(() => new Set());
  const effectiveSpeakerEnabled = React.useMemo(() => {
    if (!speakerSolo.size) return speakerEnabled;
    return Object.fromEntries(Object.keys(speakerEnabled).map((channel) => [
      channel, speakerSolo.has(channel) && speakerEnabled[channel] !== false,
    ]));
  }, [speakerEnabled, speakerSolo]);

  const engineRef = React.useRef<PreviewHost | null>(null);
  if (!engineRef.current) {
    // `setXxx` state setters have stable identity across renders, so this
    // callbacks object only needs to be constructed once, alongside the
    // engine itself.
    const callbacks: EngineCallbacks = {
      onReady: setReady,
      onLoadProgress: setLoadProgress,
      onError: setError,
      onPlaying: setPlaying,
      onCurrentTime: setCurrentTime,
      onDuration: setDuration,
      onMeasuring: setMeasuring,
      onMeasureProgress: setMeasureProgress,
      onLoudness: setLoudness,
      onMaxChannels: setMaxChannels,
      onVolume: setVolumeState,
      onMuted: setMutedState,
      onLoop: setLoop,
      onEngineStatus: (kind, reason) => {
        setEngineKind(kind);
        setFallbackReason(reason);
      },
    };
    engineRef.current = new PreviewHost(callbacks);
  }
  const engine = engineRef.current;
  const [supported] = React.useState(() => engine.supported);

  engine.setProgramme(programme);
  if (constants) engine.setConstants(constants);
  engine.setMonitor(createPreviewMonitor({
    outputMode,
    spatialProfile,
    transauralProfile,
    appleHeadTracking,
    speakerEnabled: effectiveSpeakerEnabled,
    masteringBypassed,
    matchBypassed,
  }));

  // Layout changed (not just the initial mount): drop mute state for
  // speakers the new layout no longer has and default any newly-added ones
  // to enabled, rather than carrying stale entries across layouts.
  const previousLayoutKey = React.useRef(layoutChannelsKey);
  React.useEffect(() => {
    if (previousLayoutKey.current === layoutChannelsKey) return;
    previousLayoutKey.current = layoutChannelsKey;
    setSpeakerEnabled(Object.fromEntries([...positionalChannels, "LFE"].map((channel) => [channel, true])));
    setSpeakerSolo(new Set());
  }, [layoutChannelsKey, positionalChannels]);

  const hasProgramme = Boolean(programme);
  const key = programme?.sourceKey ?? "";
  const programKey = `${programme?.key ?? ""}:${outputMode}:${spatialProfile}:${transauralProfile}:${appleHeadTracking}:${masteringBypassed}:${matchBypassed}`;

  React.useEffect(() => {
    engine.applySpeakerMute();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  }, [effectiveSpeakerEnabled]);

  const toggleSpeaker = React.useCallback((channel: string) => {
    setSpeakerSolo(new Set());
    setSpeakerEnabled((current) => ({ ...current, [channel]: current[channel] === false }));
  }, []);

  const soloSpeaker = React.useCallback((channel: string) => {
    setSpeakerEnabled((current) => ({ ...current, [channel]: true }));
    setSpeakerSolo((current) => {
      const next = new Set(current);
      if (next.has(channel)) next.delete(channel);
      else next.add(channel);
      return next;
    });
  }, []);

  React.useEffect(() => {
    if (isTauriRuntime) return;
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
    await engine.setOutputSink(deviceId);
  }, [engine]);

  React.useEffect(() => {
    if (!constants) return;
    void engine.syncProgram();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  }, [programKey, ready, constants]);

  React.useEffect(() => {
    if (!constants || !hasProgramme) return;
    engine.initialize().catch(() => {
      // error state already set inside initialize
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  }, [key, constants, hasProgramme]);

  // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  React.useEffect(() => () => engine.reset(), [key, hasProgramme]);
  React.useEffect(() => {
    setError(null);
  }, [key]);
  // Runs only on unmount (`engine` never changes identity — see above), same
  // as the old cleanup-only effect this replaces.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  React.useEffect(() => () => engine.dispose(), []);

  const setVolume = React.useCallback((value: number) => engine.setVolume(value), [engine]);
  const toggleMute = React.useCallback(() => engine.toggleMute(), [engine]);
  const toggleLoop = React.useCallback(() => engine.toggleLoop(), [engine]);
  const playPause = React.useCallback(() => engine.playPause(engine.currentTimeRef.current), [engine]);
  const stop = React.useCallback(() => engine.stop(), [engine]);
  const beginScrub = React.useCallback(() => engine.beginScrub(), [engine]);
  const scrubTo = React.useCallback((time: number) => engine.scrubTo(time), [engine]);
  const commitScrub = React.useCallback((time: number) => engine.commitScrub(time), [engine]);
  const seek = React.useCallback((time: number) => engine.seek(time), [engine]);

  return {
    supported,
    ready,
    loadProgress,
    measuring,
    measureProgress,
    loudness,
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
    stemSpectrum: engine.stemSpectrum,
    stemLevels: engine.stemLevels,
    stemDynamics: engine.stemDynamics,
    stemDynamicEq: engine.stemDynamicEq,
    channelLevels: engine.channelLevels,
    headphoneLevels: engine.headphoneLevels,
    masterMeters: engine.masterMeters,
    currentTimeRef: engine.currentTimeRef,
    speakerEnabled,
    speakerSolo,
    toggleSpeaker,
    soloSpeaker,
    maxChannels,
    nativeSupported: layoutChannels.length > 0 && layoutChannels.length <= maxChannels,
    outputDevices,
    outputDeviceId,
    setOutputDeviceId,
    engineKind,
    fallbackReason,
  };
}
