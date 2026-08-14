import * as React from "react";
import type { ProjectStem, StemScene } from "@/api";
import { speakerCoordinates } from "@/lib/spatial";
import type { EngineConstants, SpatialProfile, TransauralProfile } from "./masteringProfiles";
import type { MasterPreview } from "./masterPreview";
import {
  PreviewAudioEngine,
  POSITIONAL_CHANNELS,
  type EngineCallbacks,
  type MixPreview,
  type OutputMode,
} from "./audioEngine";

export type { OutputMode, MeterLevel, MixPreview } from "./audioEngine";
export type { SpatialProfile, TransauralProfile } from "./masteringProfiles";
export { applyTruePeakCeiling } from "./audioEngine";

// Thin React binding over PreviewAudioEngine (audioEngine.ts): syncs props/state
// onto the engine's fields and wires effects to the matching engine method.
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
  // already-built graph rather than re-decoding stems, see the engine's
  // `applyOutputMode`.
  outputMode: OutputMode = "binaural",
  // Spatial Audio Engine profile (Studio/Listening/Flat) — selects the
  // decode filter set and voicing chain, see docs/standards/
  // spatial_audio_engine.md. Session-only, like outputMode.
  spatialProfile: SpatialProfile = "studio",
  // Crosstalk-cancellation (transaural) speaker profile (Stereo/Smart
  // speaker/Car) — selects the XTC filter set and voicing chain, see
  // docs/standards/transaural_speakers.md. Session-only, like outputMode;
  // only meaningful when outputMode === "transaural".
  transauralProfile: TransauralProfile = "stereo",
  // Backend-served tunable DSP constants (resolveEngineConstants). Null until
  // the bootstrap GET /api/v1/configuration fetch resolves; the preview's
  // Web Audio graph is not built until it is set — every graph-building effect
  // below is gated on it.
  constants: EngineConstants | null = null,
) {
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
  const [maxChannels, setMaxChannels] = React.useState(2);
  const [outputDevices, setOutputDevices] = React.useState<MediaDeviceInfo[]>([]);
  const [outputDeviceId, setOutputDeviceIdState] = React.useState("");
  // Per-speaker mute state — independent of stem mute/solo, since the
  // renderer input is the channel bed, not the stems.
  // "LFE" is included even though it has no ambisonic bus.
  const [speakerEnabled, setSpeakerEnabled] = React.useState<Record<string, boolean>>(
    () => Object.fromEntries([...positionalChannels, "LFE"].map((channel) => [channel, true])),
  );

  const engineRef = React.useRef<PreviewAudioEngine | null>(null);
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
      onMaxChannels: setMaxChannels,
      onVolume: setVolumeState,
      onMuted: setMutedState,
      onLoop: setLoop,
    };
    engineRef.current = new PreviewAudioEngine(callbacks);
  }
  const engine = engineRef.current;
  const [supported] = React.useState(() => engine.supported);

  // Sync every input prop/state onto the engine's public fields, mirroring
  // the old per-value refs' unconditional per-render assignment — the
  // engine methods below read these fields at call time instead of closing
  // over React values directly.
  engine.stems = stems;
  engine.scene = scene;
  engine.mix = mix;
  engine.sourcePreviewUrl = sourcePreviewUrl;
  engine.mastering = mastering;
  engine.layoutChannels = layoutChannels;
  engine.outputMode = outputMode;
  engine.spatialProfile = spatialProfile;
  engine.transauralProfile = transauralProfile;
  if (constants) engine.constants = constants;
  engine.positionalChannels = positionalChannels;
  engine.speakerEnabled = speakerEnabled;

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

  React.useEffect(() => {
    if (!constants) return;
    engine.buildMasteringTopology();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on masteringKey + constants readiness, not `mastering` (see masteringKey comment above)
  }, [masteringKey, constants]);

  React.useEffect(() => {
    if (!constants) return;
    engine.buildStemEqChains();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on stemEqKey + constants readiness, not `mix` (see stemEqKey comment above)
  }, [stemEqKey, constants]);

  React.useEffect(() => {
    engine.applySpeakerMute();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  }, [speakerEnabled]);

  const toggleSpeaker = React.useCallback((channel: string) => {
    setSpeakerEnabled((current) => ({ ...current, [channel]: current[channel] === false }));
  }, []);

  React.useEffect(() => {
    if (!constants) return;
    engine.applyOutputMode(outputMode);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  }, [outputMode, ready, constants]);

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
    await engine.setOutputSink(deviceId);
  }, [engine]);

  React.useEffect(() => {
    if (!constants) return;
    engine.apply();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  }, [mix, scene.stems, mastering, constants]);

  // Profile switch: retune the already-built voicing chain (cheap, no graph
  // rebuild), swap in the new profile's decode filter set, then recalibrate.
  React.useEffect(() => {
    if (!constants) return;
    void engine.retuneVoicing(spatialProfile);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  }, [spatialProfile, ready, constants]);

  // Transaural profile switch: same pattern as the binaural effect above.
  React.useEffect(() => {
    if (!constants) return;
    void engine.retuneCrosstalkVoicing(transauralProfile);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  }, [transauralProfile, ready, constants]);

  React.useEffect(() => {
    if (!constants) return;
    engine.initialize().catch(() => {
      // error state already set inside initialize
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  }, [key, constants]);

  // eslint-disable-next-line react-hooks/exhaustive-deps -- `engine` is a stable ref-backed singleton (see the lazy engineRef init above), never needs to appear in a dependency array
  React.useEffect(() => () => engine.reset(), [key]);
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
    channelLevels: engine.channelLevels,
    headphoneLevels: engine.headphoneLevels,
    currentTimeRef: engine.currentTimeRef,
    speakerEnabled,
    toggleSpeaker,
    maxChannels,
    nativeSupported: layoutChannels.length > 0 && layoutChannels.length <= maxChannels,
    outputDevices,
    outputDeviceId,
    setOutputDeviceId,
  };
}
