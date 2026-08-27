import * as React from "react";
import {
  AudioWaveform,
  ChevronLeft,
  Download,
  FolderOpen,
  Package,
  PanelLeft,
  Settings,
  SlidersHorizontal,
  UploadCloud,
  Wand2,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Configuration, type ProjectTrack, type StemRouting } from "@/api";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { EmptyState } from "@/app/EmptyState";
import { InspectorGroup } from "@/app/InspectorRow";
import { SegmentedControl } from "@/app/SegmentedControl";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Button } from "@/components/ui/button";
import { SliderField, SwitchRow } from "@/components/forms/fields";
import { MasteringSection } from "@/features/composer/sections/MasteringSection";
import { isStereoLayout, outputModeForLayoutSwitch } from "@/lib/layouts";
import { normalizeManifest, type Manifest } from "@/lib/manifest";
import { getStemColor, getStemIcon } from "@/lib/stems";
import { AssetsTab } from "./assets/AssetsTab";
import { KeyCommandsDialog } from "./KeyCommandsDialog";
import type { SpatialProfile, TransauralProfile } from "./masteringProfiles";
import { StemChannelStrip } from "./ChannelStrip";
import { MasterBypassButton, MatchBypassButton } from "./MasterBypassButton";
import { OutputModeSelect } from "./OutputModeSelect";
import { monitorMastering } from "./masterPreview";
import { ProjectDeliverySection } from "./ProjectDeliverySection";
import { PreviewPanel } from "./PreviewPanel";
import { ProjectSettingsSection } from "./ProjectSettingsSection";
import { useProjectViewState } from "./projectViewState";
import { Transport } from "./Transport";
import { TrackRail } from "./TrackRail";
import {
  readStoredTrackRailCollapsed,
  trackRailStorageKey,
} from "./projectDetailLayout";
import { useColumnLayout } from "./useColumnLayout";
import { useEditHistory } from "./useEditHistory";
import { useKeyCommands } from "./useKeyCommands";
import { useLayoutSelection } from "./useLayoutSelection";
import { usePaneLayout } from "./usePaneLayout";
import { useStemPreview, type OutputMode } from "./useStemPreview";
import { resolveEngineConstants } from "./masteringProfiles";
import { useProjectState } from "./useProjectState";
import { StemControls } from "./StemControls";
import { loadPanner, NEUTRAL_PLACEMENT, type Panner, type StemPlacement } from "./wasmEngine/panner";
import { useTrackPeaks } from "./useTrackPeaks";

type Stage = "assets" | "mixing" | "mastering" | "delivery";

const STAGES = [
  { value: "assets" as const, label: "Prepare", icon: FolderOpen },
  { value: "mixing" as const, label: "Mixing", icon: SlidersHorizontal },
  { value: "mastering" as const, label: "Mastering", icon: AudioWaveform },
  { value: "delivery" as const, label: "Delivery", icon: Package },
];

const SETTINGS_SEGMENT = [{ value: "settings" as const, label: "Settings", icon: Settings }];

// Matches `projectViewState.ts`'s save debounce: long enough that a drag's
// continuous ticks never individually reach the backend, short enough that
// stopping the drag feels like it saved immediately.
const COMMIT_DEBOUNCE_MS = 350;

export function ProjectDetailPage({ configuration }: { configuration: Configuration | null }) {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [selectedStem, setSelectedStem] = React.useState<string | null>(null);
  const [draggedStem, setDraggedStem] = React.useState<string | null>(null);
  const [activeTab, setActiveTab] = React.useState<Stage>("mixing");
  const [settingsView, setSettingsView] = React.useState(false);
  const [preset, setPreset] = React.useState("balanced");
  const [exporting, setExporting] = React.useState(false);
  const {
    project, manifest, error, setError, applyProject,
    saveTrack, saveProjectFields, retry, reprepareStems,
  } = useProjectState(projectId, (next) => {
    if (next.tracks.length === 0 || !next.prepared_stems.length) setActiveTab("assets");
  });
  const history = useEditHistory(projectId);
  const { selection, setSelection } = useLayoutSelection(projectId, project);
  const selectedTrack = selection?.trackId || null;
  const selectedLayout = selection?.layout || "7.1.4";
  const selected = project?.tracks.find((track) => track.id === selectedTrack) || null;
  const serverTrackManifest = React.useMemo(() => {
    if (!manifest || !selected) return manifest;
    const overrides = (selected.layout_overrides[selectedLayout] || {}) as Partial<Manifest>;
    return normalizeManifest({
      ...manifest,
      ...overrides,
      engine: { ...manifest.engine, ...overrides.engine },
      mixing: { ...manifest.mixing, ...overrides.mixing, channel_layout: selectedLayout },
      routing: { ...manifest.routing, ...overrides.routing },
      mastering: { ...manifest.mastering, ...overrides.mastering },
      processing: { ...manifest.processing, ...overrides.processing },
      format: { ...manifest.format, ...overrides.format },
    });
  }, [manifest, selected, selectedLayout]);
  // A drag/wheel/keyboard edit shows its result instantly from here, without
  // waiting on the network — `saveTrack` only fires after COMMIT_DEBOUNCE_MS
  // of quiescence (see `updateTrackManifest`), so mid-drag ticks must not
  // depend on a server round-trip to be visible.
  const [pendingManifest, setPendingManifest] = React.useState<Manifest | null>(null);
  const trackManifest = pendingManifest ?? serverTrackManifest;
  const pendingSaveRef = React.useRef<{ track: ProjectTrack; layout: string; value: Manifest } | null>(null);
  const saveTimerRef = React.useRef<number | null>(null);
  // Flushes whatever edit is still pending — on track/layout switch (so the
  // newly selected manifest isn't shadowed by a stale override) and on
  // unmount (so navigating away mid-drag doesn't drop the edit entirely).
  const resetPendingSave = React.useCallback(() => {
    if (saveTimerRef.current) { window.clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    const pending = pendingSaveRef.current;
    pendingSaveRef.current = null;
    setPendingManifest(null);
    if (pending) void saveTrack(pending.track, pending.layout, pending.value);
  }, [saveTrack]);
  React.useEffect(() => resetPendingSave, [selectedTrack, selectedLayout, resetPendingSave]);
  // `merge` collapses consecutive calls carrying the same manifest field into
  // one undo step (a fader drag) — see `useEditHistory`. Network commits are
  // debounced separately here so a drag's continuous ticks never each reach
  // the backend — only the value in place once the user stops.
  const updateTrackManifest = React.useCallback((next: Manifest, merge?: boolean) => {
    if (!selected || !trackManifest) return;
    const track = selected;
    const layout = selectedLayout;
    history.record(trackManifest, next, (value) => {
      setPendingManifest(value);
      pendingSaveRef.current = { track, layout, value };
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = window.setTimeout(() => {
        saveTimerRef.current = null;
        pendingSaveRef.current = null;
        void saveTrack(track, layout, value).then(() => {
          setPendingManifest((current) => (current === value ? null : current));
        });
      }, COMMIT_DEBOUNCE_MS);
    }, merge);
  }, [selected, selectedLayout, trackManifest, saveTrack, history]);
  const previewStems = selected?.stems.filter((stem) => project?.prepared_stems.includes(stem.stem_key.split("@", 1)[0])) || [];
  const stemChannelCounts = React.useMemo(() => {
    const source = selected?.stems || project?.tracks[0]?.stems || [];
    const counts: Record<string, number> = {};
    for (const stem of source) counts[stem.stem_key.split("@", 1)[0]] = stem.channels;
    return counts;
  }, [selected, project]);
  const routingLayout = trackManifest?.mixing.channel_layout || "7.1.4";
  const stereoLayout = isStereoLayout(routingLayout);
  const channels = React.useMemo(
    () => configuration?.choices.layout_channels?.[routingLayout] ?? [],
    [configuration, routingLayout],
  );
  const { viewState, ready: viewStateReady, patchViewState } = useProjectViewState(projectId, project);
  const outputMode = viewState.outputMode;
  const spatialProfile = viewState.spatialProfile;
  const transauralProfile = viewState.transauralProfile;
  const setOutputMode = React.useCallback((next: OutputMode) => patchViewState({ outputMode: next }), [patchViewState]);
  const setSpatialProfile = React.useCallback((next: SpatialProfile) => patchViewState({ spatialProfile: next }), [patchViewState]);
  const setTransauralProfile = React.useCallback((next: TransauralProfile) => patchViewState({ transauralProfile: next }), [patchViewState]);
  React.useEffect(() => {
    if (stereoLayout && outputMode !== "native") setOutputMode("native");
  }, [stereoLayout, outputMode, setOutputMode]);
  const masteringBypassed = viewState.masteringBypassed;
  const matchBypassed = viewState.matchBypassed;
  const pane = usePaneLayout(projectId);
  const { paneView, previewColumn, changePane } = pane;
  const columns = useColumnLayout(projectId);
  const [trackRailCollapsed, setTrackRailCollapsed] = React.useState(() => readStoredTrackRailCollapsed(projectId));
  React.useEffect(() => setTrackRailCollapsed(readStoredTrackRailCollapsed(projectId)), [projectId]);
  React.useEffect(() => {
    try {
      window.localStorage.setItem(trackRailStorageKey(projectId), trackRailCollapsed ? "1" : "0");
    } catch {
      // Storage being unavailable only costs the preference, not the view.
    }
  }, [projectId, trackRailCollapsed]);
  // See docs/contracts/preview_export_parity.md Ledger D21: only the
  // correction curve and level gain come from the precomputed asset.
  const previewMastering = React.useMemo(() => {
    if (!trackManifest?.mastering) return trackManifest?.mastering;
    const asset = project?.reference_match?.[selectedLayout];
    if (!asset) return trackManifest.mastering;
    const liveMatch = trackManifest.mastering.match_reference;
    return {
      ...trackManifest.mastering,
      match_reference: {
        fir_url: asset.fir_url,
        rms_gain_db: asset.rms_gain_db,
        strength: liveMatch?.strength,
        spectrum: liveMatch?.spectrum,
        rms: liveMatch?.rms,
        max_db: liveMatch?.max_db,
        smooth_octaves: liveMatch?.smooth_octaves,
        low_hz: liveMatch?.low_hz,
        high_hz: liveMatch?.high_hz,
      },
    };
  }, [trackManifest?.mastering, project?.reference_match, selectedLayout]);
  const engineConstants = React.useMemo(
    () => (configuration?.constants ? resolveEngineConstants(configuration.constants) : null),
    [configuration],
  );
  const monitoredMastering = React.useMemo(
    () => monitorMastering(previewMastering, masteringBypassed, matchBypassed),
    [previewMastering, masteringBypassed, matchBypassed],
  );
  const preview = useStemPreview(previewStems, {}, trackManifest?.mixing, selected?.source_preview_url || null, monitoredMastering, channels, outputMode, spatialProfile, transauralProfile, engineConstants, trackManifest?.routing, masteringBypassed, matchBypassed);
  const previousRoutingLayoutRef = React.useRef(routingLayout);
  React.useEffect(() => { previousRoutingLayoutRef.current = routingLayout; }, [projectId]);
  React.useEffect(() => {
    if (stereoLayout || previousRoutingLayoutRef.current === routingLayout) return;
    previousRoutingLayoutRef.current = routingLayout;
    const next = outputModeForLayoutSwitch(preview.nativeSupported);
    setOutputMode(next.outputMode);
    if (next.outputMode === "binaural") setSpatialProfile(next.spatialProfile);
  }, [routingLayout, stereoLayout, preview.nativeSupported, setOutputMode, setSpatialProfile]);
  // The engine, not React, owns `preview.volume`: restore it once per project
  // so the engine's unity default can't race the saved value.
  const volumeRestored = React.useRef(false);
  React.useEffect(() => { volumeRestored.current = false; }, [projectId]);
  React.useEffect(() => {
    if (volumeRestored.current || !viewStateReady || !preview.ready) return;
    volumeRestored.current = true;
    preview.setVolume(viewState.masterVolume);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot restore, guarded by volumeRestored.current; preview.setVolume is stable (see useStemPreview)
  }, [viewStateReady, preview.ready, viewState.masterVolume]);
  React.useEffect(() => {
    if (!volumeRestored.current) return;
    patchViewState({ masterVolume: preview.volume });
  }, [preview.volume, patchViewState]);
  const { peaks, loading: peaksLoading } = useTrackPeaks(selected);
  const ready = Boolean(project?.prepared_stems.length);
  const stemNames = project?.prepared_stems || [];
  const orderedStems = React.useMemo(() => {
    const known = new Set(stemNames);
    const kept = viewState.stemOrder.filter((stem) => known.has(stem));
    const missing = stemNames.filter((stem) => !kept.includes(stem));
    return [...kept, ...missing];
  }, [stemNames, viewState.stemOrder]);
  const reorderStems = React.useCallback((source: string, target: string) => {
    if (source === target) return;
    const next = orderedStems.filter((stem) => stem !== source);
    const targetIndex = next.indexOf(target);
    if (targetIndex === -1) return;
    next.splice(targetIndex, 0, source);
    patchViewState({ stemOrder: next });
  }, [orderedStems, patchViewState]);
  const clearDraggedStem = React.useCallback(() => setDraggedStem(null), []);
  const handleDropOn = React.useCallback((target: string) => {
    setDraggedStem((current) => {
      if (current) reorderStems(current, target);
      return null;
    });
  }, [reorderStems]);
  const setHazeIntensity = React.useCallback((next: number) => patchViewState({ hazeIntensity: next }), [patchViewState]);
  const setElevationIntensity = React.useCallback((next: number) => patchViewState({ elevationIntensity: next }), [patchViewState]);
  const routing: StemRouting = React.useMemo(() => trackManifest?.mixing.stem_routing || {}, [trackManifest]);
  const placements = React.useMemo(
    () => (trackManifest?.mixing.stem_placement || {}) as Record<string, StemPlacement>,
    [trackManifest],
  );
  const [panner, setPanner] = React.useState<Panner | null>(null);
  React.useEffect(() => {
    let live = true;
    loadPanner().then((loaded) => { if (live) setPanner(loaded); }).catch((reason) => setError((reason as Error).message));
    return () => { live = false; };
  }, [setError]);
  const maxElevationDeg = React.useMemo(
    () => (panner && channels.length ? panner.maxElevationDeg(channels) : 0),
    [panner, channels],
  );
  /** A stem with no stored placement falls back to the preset's, so the first
   * edit rotates the image it already had instead of a point at the front. */
  const placementFor = React.useCallback(
    (stem: string): StemPlacement =>
      placements[stem]
      ?? panner?.presetPlacements(preset)[stem.split("@", 1)[0]]
      ?? NEUTRAL_PLACEMENT,
    [placements, panner, preset],
  );
  const updateRoute = (stem: string, patch: Record<string, number>) => {
    if (!trackManifest) return;
    updateTrackManifest({ ...trackManifest, mixing: { ...trackManifest.mixing, stem_routing: { ...routing, [stem]: { ...routing[stem], ...patch } } } }, true);
  };
  /** The stem's reverb and room, split out of it and sent around and above
   * the listener. Both amounts leave the front, so this is a move, not a
   * copy — see `routing::ambient`. */
  const updateAmbient = (stem: string, patch: { rear?: number; height?: number; heightCrossoverHz?: number }) => {
    if (!trackManifest) return;
    const mixing = { ...trackManifest.mixing };
    if (patch.rear !== undefined) {
      mixing.stem_ambient_rear = { ...mixing.stem_ambient_rear, [stem]: patch.rear };
    }
    if (patch.height !== undefined) {
      mixing.stem_ambient_height = { ...mixing.stem_ambient_height, [stem]: patch.height };
    }
    if (patch.heightCrossoverHz !== undefined) {
      mixing.stem_ambient_height_crossover_hz = {
        ...mixing.stem_ambient_height_crossover_hz,
        [stem]: patch.heightCrossoverHz,
      };
    }
    updateTrackManifest({ ...trackManifest, mixing }, true);
  };
  const setSpatialDownmixLock = (spatial_downmix_lock: boolean) => {
    if (!trackManifest) return;
    updateTrackManifest({ ...trackManifest, mixing: { ...trackManifest.mixing, spatial_downmix_lock } }, true);
  };
  const setStemObjectMode = (stem: string, mode: "linked-stereo" | "mono") => {
    if (!trackManifest) return;
    updateTrackManifest({ ...trackManifest, mixing: {
      ...trackManifest.mixing,
      stem_object_mode: { ...trackManifest.mixing.stem_object_mode, [stem]: mode },
    } }, true);
  };
  /** The placement is what the user edits; the gain table is derived from it
   * here so the manifest the export reads never lags behind the UI. */
  const updatePlacement = (stem: string, placement: StemPlacement) => {
    if (!trackManifest || !panner) return;
    const route = panner.placementRoute(placement, channels, routing[stem]?.LFE ?? 0);
    updateTrackManifest({
      ...trackManifest,
      mixing: {
        ...trackManifest.mixing,
        stem_placement: { ...placements, [stem]: placement },
        stem_routing: { ...routing, [stem]: route },
      },
    }, true);
  };
  const applyPreset = () => {
    if (!trackManifest || !stemNames.length || !panner) return;
    const table = panner.presetPlacements(preset);
    const sends = panner.presetSends(preset);
    const nextPlacements: Record<string, StemPlacement> = {};
    const nextRouting: StemRouting = {};
    const nextRear = { ...trackManifest.mixing.stem_ambient_rear };
    const nextHeight = { ...trackManifest.mixing.stem_ambient_height };
    const nextHeightCrossover = { ...trackManifest.mixing.stem_ambient_height_crossover_hz };
    for (const stem of stemNames) {
      const name = stem.split("@", 1)[0];
      const placement = table[name];
      if (!placement) continue;
      const send = sends[name] ?? { lfe: 0, rear: 0, height: 0, heightCrossoverHz: 2000 };
      nextPlacements[stem] = placement;
      nextRouting[stem] = panner.placementRoute(placement, channels, send.lfe);
      nextRear[stem] = send.rear;
      nextHeight[stem] = send.height;
      nextHeightCrossover[stem] = send.heightCrossoverHz;
    }
    updateTrackManifest({ ...trackManifest, mixing: { ...trackManifest.mixing, stem_placement: nextPlacements, stem_routing: nextRouting, stem_ambient_rear: nextRear, stem_ambient_height: nextHeight, stem_ambient_height_crossover_hz: nextHeightCrossover } });
  };
  const toggleEnabled = React.useCallback((stem: string) => {
    if (!trackManifest) return;
    const current = trackManifest.mixing.stem_enabled[stem] !== false;
    updateTrackManifest({ ...trackManifest, mixing: { ...trackManifest.mixing, stem_enabled: { ...trackManifest.mixing.stem_enabled, [stem]: !current }, stem_solo: trackManifest.mixing.stem_solo.filter((solo) => solo !== stem) } });
  }, [trackManifest, updateTrackManifest]);
  const toggleSolo = React.useCallback((stem: string) => {
    if (!trackManifest) return;
    const solo = trackManifest.mixing.stem_solo;
    updateTrackManifest({ ...trackManifest, mixing: { ...trackManifest.mixing, stem_solo: solo.includes(stem) ? solo.filter((item) => item !== stem) : [...solo, stem] } });
  }, [trackManifest, updateTrackManifest]);
  const previewCommitScrub = preview.commitScrub;
  const commitScrub = React.useCallback((value: number) => { void previewCommitScrub(value); }, [previewCommitScrub]);
  const setStemGain = React.useCallback((stem: string, gain: number) => {
    if (!trackManifest) return;
    updateTrackManifest({ ...trackManifest, mixing: { ...trackManifest.mixing, stem_rebalance: { ...trackManifest.mixing.stem_rebalance, [stem]: gain } } }, true);
  }, [trackManifest, updateTrackManifest]);
  const setAnchorStrength = React.useCallback((stem_source_anchor_strength: number) => {
    if (!trackManifest) return;
    updateTrackManifest({ ...trackManifest, mixing: { ...trackManifest.mixing, stem_source_anchor_strength } }, true);
  }, [trackManifest, updateTrackManifest]);
  const silentStems = React.useMemo(() => {
    const solo = trackManifest?.mixing.stem_solo || [];
    return orderedStems.filter((stem) => (
      trackManifest?.mixing.stem_enabled[stem] === false
      || (solo.length > 0 && !solo.includes(stem))
    ));
  }, [trackManifest, orderedStems]);
  const transportDisabled =
    !preview.supported || !preview.ready || !previewStems.length || preview.measuring;
  const { shortcutsOpen, setShortcutsOpen } = useKeyCommands({
    transportEnabled: !transportDisabled,
    preview,
    stems: orderedStems,
    selectedStem,
    onSelectStem: setSelectedStem,
    onToggleMute: toggleEnabled,
    onToggleSolo: toggleSolo,
    manifest: trackManifest,
    onManifestChange: updateTrackManifest,
    paneView,
    onChangePane: changePane,
    onToggleMasterBypass: () => patchViewState({ masteringBypassed: !masteringBypassed }),
    onUndo: history.undo,
    onRedo: history.redo,
  });
  const exportProject = async () => {
    if (!projectId) return;
    setExporting(true);
    try { await api.exportProject(projectId, selectedLayout); navigate("/jobs"); } catch (reason) { setError((reason as Error).message); } finally { setExporting(false); }
  };
  // Must stay referentially stable: useHeaderTitle's effect keys on it, so a
  // fresh element every render would re-fire the effect forever.
  const headerTitle = React.useMemo(() => project ? (
    <div className="grid h-full w-full grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3">
      <div className="flex min-w-0 items-center gap-1.5 justify-self-start">
        <Link to="/projects" className="flex shrink-0 items-center gap-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground">
          <ChevronLeft className="h-3.5 w-3.5" />Projects
        </Link>
        <span className="text-muted-foreground">/</span>
        <span className="truncate text-[13px] font-semibold">{project.name}</span>
      </div>
      <SegmentedControl
        aria-label="Project stage"
        segments={STAGES}
        value={settingsView ? ("" as Stage) : activeTab}
        onChange={(value) => {
          setActiveTab(value);
          setSettingsView(false);
        }}
        className="justify-self-center self-stretch"
        fill
        slideIndicator
        activeClassName="bg-primary shadow-sm"
        activeTextClassName="text-primary-foreground"
      />
      <SegmentedControl
        aria-label="Project settings"
        segments={SETTINGS_SEGMENT}
        value={(settingsView ? "settings" : "") as "settings"}
        onChange={() => setSettingsView(true)}
        // -mr-4 pulls this flush with Transport's col-3 right edge: AppShell's
        // header reserves px-3 vs Transport's px-2, plus an unused gap-3 slot.
        className="justify-self-end -mr-4"
      />
    </div>
  ) : null, [project?.name, activeTab, settingsView]);
  useHeaderTitle(headerTitle);
  if (!project) return <main className="grid h-full place-items-center p-5 text-sm text-muted-foreground">{error || "Loading project…"}</main>;
  const transportLeading = (
    activeTab !== "assets" && !settingsView && (
      // Collapsing takes the rail fully out of the layout (see TrackRail.tsx),
      // so its own header button can't be what brings it back.
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 bg-muted [&_svg]:size-4"
        aria-label={trackRailCollapsed ? "Show tracks" : "Hide tracks"}
        aria-pressed={!trackRailCollapsed}
        onClick={() => setTrackRailCollapsed((current) => !current)}
      >
        <PanelLeft />
      </Button>
    )
  );
  return <main className="flex h-[calc(100vh-var(--topbar-h))] w-full flex-col overflow-hidden">
    {activeTab !== "assets" && (
      <Transport
        playing={preview.playing}
        currentTime={preview.currentTime}
        currentTimeRef={preview.currentTimeRef}
        duration={preview.duration}
        volume={preview.volume}
        muted={preview.muted}
        loop={preview.loop}
        disabled={transportDisabled}
        onPlayPause={() => void preview.playPause()}
        onStop={preview.stop}
        onToggleLoop={preview.toggleLoop}
        onSetVolume={preview.setVolume}
        onToggleMute={preview.toggleMute}
        headphoneLevels={preview.headphoneLevels}
        leading={transportLeading}
      >
        <MasterBypassButton
          bypassed={masteringBypassed}
          onToggle={() => patchViewState({ masteringBypassed: !masteringBypassed })}
        />
        <MatchBypassButton
          bypassed={matchBypassed}
          disabled={masteringBypassed || !project?.reference_match?.[selectedLayout]}
          onToggle={() => patchViewState({ matchBypassed: !matchBypassed })}
        />
        <OutputModeSelect
          value={outputMode}
          onChange={setOutputMode}
          nativeSupported={preview.nativeSupported}
          nativeOnly={stereoLayout}
          devices={preview.outputDevices}
          deviceId={preview.outputDeviceId}
          onDeviceChange={(deviceId) => void preview.setOutputDeviceId(deviceId)}
          spatialProfile={spatialProfile}
          onSpatialProfileChange={setSpatialProfile}
          transauralProfile={transauralProfile}
          onTransauralProfileChange={setTransauralProfile}
        />
      </Transport>
    )}
    {error && <p className="flex-none border-b border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>}
    {settingsView && manifest ? (
      <section className="min-h-0 flex-1 overflow-auto p-3">
        <ProjectSettingsSection
          project={project}
          configuration={configuration}
          onRename={(name) => void saveProjectFields({ name })}
          onPreviewQualityChange={(quality) => void saveProjectFields({ preview_quality: quality })}
        />
      </section>
    ) : activeTab === "assets" ? (
      <section className="flex min-h-0 flex-1 flex-col">
        <AssetsTab
          project={project}
          configuration={configuration}
          onProjectUpdate={applyProject}
          onOpenTrack={(trackId) => {
            const track = project.tracks.find((item) => item.id === trackId);
            setSelection({ trackId, layout: track?.layouts[0] || selectedLayout });
            setActiveTab("mixing");
          }}
          onRetry={() => void retry()}
          onReprepare={() => void reprepareStems()}
        />
      </section>
    ) : !ready ? (
      <EmptyState
        icon={UploadCloud}
        title="No prepared tracks yet"
        description="Upload and prepare at least one track in Prepare before mixing, mastering, or delivering."
        action={<Button size="sm" variant="outline" onClick={() => setActiveTab("assets")}>Go to Prepare</Button>}
        className="flex-1"
      />
    ) : (() => {
      const trackRail = (
        <TrackRail
          tracks={project.tracks}
          value={selection}
          onChange={setSelection}
          collapsed={trackRailCollapsed}
        />
      );
      const previewPanel = <PreviewPanel
        containerRef={previewColumn}
        preview={preview}
        pane={pane}
        columns={columns}
        project={project}
        trackManifest={trackManifest}
        viewState={viewState}
        channels={channels}
        routing={routing}
        outputMode={outputMode}
        stereoLayout={stereoLayout}
        stemChannelCounts={stemChannelCounts}
        selectedStem={selectedStem}
        onSelectStem={setSelectedStem}
        onHazeIntensity={setHazeIntensity}
        onElevationIntensity={setElevationIntensity}
        orderedStems={orderedStems}
        silentStems={silentStems}
        previewStemCount={previewStems.length}
        peaks={peaks}
        peaksLoading={peaksLoading}
        peaksDuration={selected?.peaks_duration_seconds || 0}
        draggedStem={draggedStem}
        onDragStart={setDraggedStem}
        onDragEnd={clearDraggedStem}
        onDropOn={handleDropOn}
        onToggleMute={toggleEnabled}
        onToggleSolo={toggleSolo}
        onGain={setStemGain}
        onAnchorStrength={setAnchorStrength}
        onCommitScrub={commitScrub}
      />;
      if (activeTab === "mixing") return <div className="grid min-h-0 flex-1 xl:grid-cols-[auto_minmax(0,1fr)_320px]">
        {trackRail}
        {previewPanel}
        {trackManifest && <div className="flex min-h-0 flex-col overflow-y-auto border-l bg-card">
          <InspectorGroup title="Routing preset">
            <p className="mb-2 truncate text-[11px] text-muted-foreground">{`${selected?.asset.title || selected?.asset.filename} · ${selectedLayout}`}</p>
            <select className="flex h-7 w-full rounded-md border bg-secondary px-2 text-[13px]" value={preset} onChange={(event) => setPreset(event.target.value)}>{(configuration?.choices.stem_routing_presets ?? []).map((name) => <option key={name}>{name}</option>)}</select>
            <Button className="mt-2.5 w-full" variant="outline" size="sm" onClick={() => void applyPreset()}><Wand2 />Apply preset</Button>
          </InspectorGroup>
          <InspectorGroup title="Stem">
            <div className="mb-3">
              <SwitchRow
                label="Downmix lock"
                checked={trackManifest.mixing.spatial_downmix_lock}
                onChange={setSpatialDownmixLock}
              />
            </div>
            {selectedStem ? (() => {
              const SelectedStemIcon = getStemIcon(selectedStem);
              const stemMuted = trackManifest.mixing.stem_enabled[selectedStem] === false;
              return <>
                <p className="mb-3 flex items-center gap-1.5 text-[13px] font-semibold">
                  <SelectedStemIcon className="h-3.5 w-3.5 shrink-0" style={{ color: getStemColor(selectedStem) }} aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{selectedStem}</span>
                  <span className="text-[11px] font-normal text-muted-foreground">{stemMuted ? "muted" : "enabled"}</span>
                </p>
                <label className="mb-3 block text-[11px] text-muted-foreground">
                  <span>Direct image</span>
                  <select className="mt-1.5 flex h-7 w-full rounded-md border bg-secondary px-2 text-[13px] text-foreground" value={trackManifest.mixing.stem_object_mode[selectedStem] ?? "linked-stereo"} onChange={(event) => setStemObjectMode(selectedStem, event.target.value as "linked-stereo" | "mono")}>
                    <option value="linked-stereo">Linked stereo</option><option value="mono">Mono</option>
                  </select>
                </label>
                <StemControls key={selectedStem} placement={placementFor(selectedStem)} maxElevationDeg={maxElevationDeg} onPlacement={(next) => updatePlacement(selectedStem, next)} route={routing[selectedStem] || {}} channels={channels} eq={trackManifest.mixing.stem_eq[selectedStem] || ""} onRoute={(patch) => updateRoute(selectedStem, patch)} ambientRear={trackManifest.mixing.stem_ambient_rear[selectedStem] ?? 0} ambientHeight={trackManifest.mixing.stem_ambient_height[selectedStem] ?? 0} ambientHeightCrossoverHz={trackManifest.mixing.stem_ambient_height_crossover_hz[selectedStem] ?? 2000} onAmbient={(patch) => updateAmbient(selectedStem, patch)} onEq={(eq) => updateTrackManifest({ ...trackManifest, mixing: { ...trackManifest.mixing, stem_eq: (() => { const next = { ...trackManifest.mixing.stem_eq }; if (eq) next[selectedStem] = eq; else delete next[selectedStem]; return next; })() } })}
                  stemEqProfiles={configuration?.choices.stem_eq_profiles}
                />
                <div className="mt-3 flex justify-center border-t pt-3">
                  <StemChannelStrip
                    stem={selectedStem}
                    subjectName="Selected stem"
                    showNameplate={false}
                    channels={stemChannelCounts[selectedStem] ?? 1}
                    gain={trackManifest.mixing.stem_rebalance[selectedStem] || 0}
                    onGain={(gain) => setStemGain(selectedStem, gain)}
                    muted={stemMuted}
                    soloed={trackManifest.mixing.stem_solo.includes(selectedStem)}
                    silent={silentStems.includes(selectedStem)}
                    onToggleMute={() => toggleEnabled(selectedStem)}
                    onToggleSolo={() => toggleSolo(selectedStem)}
                    meterSource={() => preview.stemLevels.current.get(selectedStem) ?? []}
                    active={preview.playing}
                    disabled={!previewStems.length}
                  />
                </div>
              </>;
            })() : <p className="text-[11px] text-muted-foreground">Select a stem to edit its sends.</p>}
          </InspectorGroup>
        </div>}
      </div>;
      if (activeTab === "mastering") return trackManifest && (selected ? <div className="grid min-h-0 flex-1 xl:grid-cols-[auto_minmax(0,1fr)_460px]">
        {trackRail}
        {previewPanel}
        <section className="min-h-0 overflow-auto border-l bg-card p-3">
          <MasteringSection
            manifest={trackManifest}
            setManifest={(update) => updateTrackManifest(typeof update === "function" ? update(trackManifest) : update)}
            configuration={configuration}
            masteringReference={project.mastering_reference || null}
            referenceUploading={false}
            referenceError={null}
            referencePending={Boolean(project.reference_match_pending)}
            onReferenceUpload={(file) => {
              if (!project.import_id) { setError("Upload a track before attaching a mastering reference."); return; }
              void api.uploadMasteringReference(project.import_id, file)
                .then((reference) => saveProjectFields({ mastering_reference_id: reference.id }))
                .catch((reason) => setError((reason as Error).message));
            }}
            onReferenceClear={() => { void saveProjectFields({ mastering_reference_id: null }); }}
          />
        </section>
      </div> : <EmptyState icon={SlidersHorizontal} title="Select a track" description="Pick a track from the rail to edit its master." className="flex-1" />);
      return trackManifest && (selected ? <div className="grid min-h-0 flex-1 xl:grid-cols-[auto_minmax(0,1fr)_460px]">
        {trackRail}
        {previewPanel}
        <section className="flex min-h-0 flex-col border-l bg-card">
          <div className="min-h-0 flex-1 overflow-auto p-3">
            <ProjectDeliverySection manifest={trackManifest} configuration={configuration} onChange={updateTrackManifest} />
          </div>
          <div className="shrink-0 space-y-1.5 border-t p-2">
            <Button className="w-full" disabled={exporting} onClick={() => void exportProject()}>
              <Download />
              {exporting ? "Queueing" : `Export project · ${project.tracks.length} track${project.tracks.length === 1 ? "" : "s"}`}
            </Button>
            <p className="text-center text-[11px] text-muted-foreground">
              Renders every track with its own master{project.tracks.length > 1 ? ", bundled into one download" : ""}.
            </p>
          </div>
        </section>
      </div> : <EmptyState icon={Package} title="Select a track" description="Pick a track from the rail to edit its delivery format." className="flex-1" />);
    })()}
    <StatusBar>
      <StatusCell label="Layout" value={routingLayout} />
      <StatusSeparator />
      <StatusCell label="Channels" value={channels.length} />
      <StatusSeparator />
      <StatusCell label="Stems" value={`${stemNames.filter((stem) => trackManifest?.mixing.stem_enabled[stem] !== false).length}/${stemNames.length}`} />
      <StatusSeparator />
      <StatusCell
        label="Monitor"
        value={
          outputMode === "binaural" ? `Binaural · ${spatialProfile}`
          : outputMode === "transaural" ? `Transaural · ${transauralProfile}`
          : "Speakers"
        }
      />
      <StatusSpacer />
      <StatusCell label="Transport" value={preview.playing ? "Playing" : preview.ready ? "Ready" : "Loading"} />
      <StatusSeparator />
      <KeyCommandsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </StatusBar>
  </main>;
}
