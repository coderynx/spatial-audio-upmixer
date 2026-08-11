import * as React from "react";
import {
  ArrowLeftRight,
  ArrowUpDown,
  AudioWaveform,
  ChevronDown,
  ChevronLeft,
  ChevronUp,
  Download,
  FolderOpen,
  Loader2,
  Package,
  PanelLeft,
  Settings,
  SlidersHorizontal,
  UploadCloud,
  Wand2,
  Waves,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Configuration, type Project, type StemRouting } from "@/api";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { EmptyState } from "@/app/EmptyState";
import { InspectorGroup } from "@/app/InspectorRow";
import { SegmentedControl } from "@/app/SegmentedControl";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Slider } from "@/components/ui/slider";
import { MasteringSection } from "@/features/composer/sections/MasteringSection";
import { normalizeManifest, type Manifest } from "@/lib/manifest";
import { getStemColor, getStemIcon, stemColors } from "@/lib/stems";
import { cn } from "@/lib/utils";
import { AssetsTab } from "./assets/AssetsTab";
import HazeView from "./HazeView";
import ChannelMeters from "./ChannelMeters";
import ElevationView from "./ElevationView";
import { KeyCommandsDialog } from "./KeyCommandsDialog";
import type { SpatialProfile, TransauralProfile } from "./masteringProfiles";
import { StemChannelStrip, StripResizeHandle } from "./ChannelStrip";
import { MasterBypassButton } from "./MasterBypassButton";
import { MixerView } from "./MixerView";
import { OutputModeSelect } from "./OutputModeSelect";
import { monitorMastering } from "./previewGraph";
import { ProjectDeliverySection } from "./ProjectDeliverySection";
import { ProjectSettingsSection } from "./ProjectSettingsSection";
import { TimelineView } from "./TimelineView";
import { Transport } from "./Transport";
import { TrackRail } from "./TrackRail";
import {
  HAZE_MIN_WIDTH,
  METERS_DEFAULT_SHARE,
  METERS_MIN_WIDTH,
  PANE_DEFAULT_HEIGHT,
  PANE_MIN_HEIGHT,
  readStoredTrackRailCollapsed,
  trackRailStorageKey,
} from "./projectDetailLayout";
import { useColumnLayout } from "./useColumnLayout";
import { useKeyCommands } from "./useKeyCommands";
import { usePaneLayout } from "./usePaneLayout";
import { useStemPreview, type OutputMode } from "./useStemPreview";
import { resolveEngineConstants } from "./masteringProfiles";
import { useTrackPeaks } from "./useTrackPeaks";

type Stage = "assets" | "mixing" | "mastering" | "delivery";

const PANE_SEGMENTS = [
  { value: "timeline" as const, label: "Timeline", icon: AudioWaveform },
  { value: "mixer" as const, label: "Mixer", icon: SlidersHorizontal },
];

const STAGES = [
  { value: "assets" as const, label: "Prepare", icon: FolderOpen },
  { value: "mixing" as const, label: "Mixing", icon: SlidersHorizontal },
  { value: "mastering" as const, label: "Mastering", icon: AudioWaveform },
  { value: "delivery" as const, label: "Delivery", icon: Package },
];

const SETTINGS_SEGMENT = [{ value: "settings" as const, label: "Settings", icon: Settings }];

export function ProjectDetailPage({ configuration }: { configuration: Configuration | null }) {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = React.useState<Project | null>(null);
  const [manifest, setManifest] = React.useState<Manifest | null>(null);
  const [selectedTrack, setSelectedTrack] = React.useState<string | null>(null);
  const [selectedStem, setSelectedStem] = React.useState<string | null>(null);
  const [stemOrder, setStemOrder] = React.useState<string[]>([]);
  const [draggedStem, setDraggedStem] = React.useState<string | null>(null);
  const [editScope, setEditScope] = React.useState<"project" | "track">("project");
  const [activeTab, setActiveTab] = React.useState<Stage>("mixing");
  const [settingsView, setSettingsView] = React.useState(false);
  const [preset, setPreset] = React.useState("balanced");
  const [presetIntensity, setPresetIntensity] = React.useState(1);
  const [error, setError] = React.useState<string | null>(null);
  const [exporting, setExporting] = React.useState(false);
  const saveTimer = React.useRef<number | null>(null);
  const initialized = React.useRef(false);
  React.useEffect(() => { initialized.current = false; }, [projectId]);
  // The poll, the SSE stream, and every save call each fetch/return a full
  // Project snapshot independently; without ordering, a poll issued before a
  // save can resolve after it and clobber the fresh save response with stale
  // data (e.g. flashing reference_match_pending back on after a mute/solo
  // save already cleared it). Every setProject call site stamps its request
  // with a monotonic sequence number and drops responses older than the
  // newest one already applied.
  const projectRequestSeq = React.useRef(0);
  const appliedProjectSeq = React.useRef(0);
  const shouldApplyProject = React.useCallback((seq: number) => {
    if (seq < appliedProjectSeq.current) return false;
    appliedProjectSeq.current = seq;
    return true;
  }, []);
  const load = React.useCallback(async () => {
    if (!projectId) return;
    const seq = ++projectRequestSeq.current;
    try {
      const next = await api.getProject(projectId);
      if (shouldApplyProject(seq)) {
        setProject(next);
        if (!initialized.current) {
          initialized.current = true;
          setManifest(normalizeManifest(next.manifest));
          setSelectedTrack(next.tracks[0]?.id || null);
          // A project with nothing prepared yet has nowhere else useful to
          // land — Mixing/Mastering/Delivery all need a ready track.
          if (next.tracks.length === 0 || !next.prepared_stems.length) setActiveTab("assets");
        }
      }
      setError(null);
    } catch (reason) { setError((reason as Error).message); }
  }, [projectId, shouldApplyProject]);
  // Polling stops at a terminal status unless a reference-match recompute or
  // peaks backfill is still pending server-side (schedule_reference_match/schedule_peaks).
  React.useEffect(() => {
    void load();
    if (
      project
      && ["ready", "failed", "expansion_failed"].includes(project.status)
      && !project.reference_match_pending
      && !project.peaks_pending
    ) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on status/reference_match_pending/peaks_pending only, same as the SSE effect below: a mid-poll `project` update (content changes, same status) shouldn't tear down and restart the interval
  }, [load, project?.status, project?.reference_match_pending, project?.peaks_pending]);
  // While the project is preparing (or a reference-match recompute is
  // pending), layer a realtime SSE stream on top of the 2s poll above so the
  // log/percentage/asset update live instead of in 2s steps. The 2s poll
  // keeps refreshing everything else (exports, other tracks) and acts as the
  // fallback if EventSource is unavailable or the stream drops.
  React.useEffect(() => {
    if (!projectId || !project) return;
    if (
      ["ready", "failed", "expansion_failed"].includes(project.status)
      && !project.reference_match_pending
      && !project.peaks_pending
    ) return;
    const source = new EventSource(api.projectEventsUrl(projectId));
    source.onmessage = (event) => {
      const seq = ++projectRequestSeq.current;
      try {
        const next = JSON.parse(event.data);
        if (shouldApplyProject(seq)) setProject(next);
      } catch { /* ignore malformed frame */ }
    };
    source.onerror = () => source.close();
    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on status and the pending flags only, so a mid-stream progress update (also named `project`) doesn't tear down and reopen the connection
  }, [projectId, project?.status, project?.reference_match_pending, project?.peaks_pending]);
  React.useEffect(() => () => { if (saveTimer.current) window.clearTimeout(saveTimer.current); }, []);
  const queueSave = React.useCallback((next: Manifest) => {
    if (!projectId || !project) return;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      const seq = ++projectRequestSeq.current;
      void api.saveProject(projectId, { manifest: next as unknown as Record<string, unknown>, scene: project.scene as Record<string, unknown> })
        .then((updated) => { if (shouldApplyProject(seq)) setProject(updated); })
        .catch((reason) => setError((reason as Error).message));
    }, 350);
  }, [project, projectId, shouldApplyProject]);
  const selected = project?.tracks.find((track) => track.id === selectedTrack) || null;
  const effectiveManifest = React.useMemo(() => {
    if (!manifest || !selected || editScope === "project") return manifest;
    const overrides = selected.manifest_overrides as Partial<Manifest>;
    return normalizeManifest({
      ...manifest,
      ...overrides,
      engine: { ...manifest.engine, ...overrides.engine },
      mixing: { ...manifest.mixing, ...overrides.mixing },
      routing: { ...manifest.routing, ...overrides.routing },
      mastering: { ...manifest.mastering, ...overrides.mastering },
      processing: { ...manifest.processing, ...overrides.processing },
      format: { ...manifest.format, ...overrides.format },
    });
  }, [editScope, manifest, selected]);
  const updateManifest = React.useCallback((next: Manifest) => {
    if (editScope === "project") {
      setManifest(next);
      queueSave(next);
      return;
    }
    if (!projectId || !selected) return;
    const seq = ++projectRequestSeq.current;
    void api.saveProjectTrack(projectId, selected.id, {
      manifest_overrides: {
        engine: { stems: next.engine.stems }, mixing: next.mixing, routing: next.routing,
        mastering: next.mastering, processing: next.processing, format: next.format,
      },
      scene_overrides: selected.scene_overrides,
    }).then((updated) => { if (shouldApplyProject(seq)) setProject(updated); })
      .catch((reason) => setError((reason as Error).message));
  }, [editScope, projectId, selected, queueSave, shouldApplyProject]);
  // Project-wide settings (name, default speaker layout, preview quality) —
  // ProjectSettingsSection only. This is the inherited default a new track
  // starts from, distinct from `updateTrackManifest` below.
  const updateProjectManifest = (next: Manifest) => {
    setManifest(next);
    queueSave(next);
  };
  // Mastering and Delivery are always per-track — each track carries its own
  // master and delivery format independent of the Mixing tab's project/track
  // edit-scope toggle (which only governs mixing/routing edits). Unlike
  // `effectiveManifest`/`updateManifest` above, this ignores `editScope`
  // entirely: there is no "one master for every track" mode for these two
  // stages, only "this track's master."
  const trackManifest = React.useMemo(() => {
    if (!manifest || !selected) return manifest;
    const overrides = selected.manifest_overrides as Partial<Manifest>;
    return normalizeManifest({
      ...manifest,
      ...overrides,
      engine: { ...manifest.engine, ...overrides.engine },
      mixing: { ...manifest.mixing, ...overrides.mixing },
      routing: { ...manifest.routing, ...overrides.routing },
      mastering: { ...manifest.mastering, ...overrides.mastering },
      processing: { ...manifest.processing, ...overrides.processing },
      format: { ...manifest.format, ...overrides.format },
    });
  }, [manifest, selected]);
  const updateTrackManifest = React.useCallback((next: Manifest) => {
    if (!projectId || !selected) return;
    const seq = ++projectRequestSeq.current;
    void api.saveProjectTrack(projectId, selected.id, {
      manifest_overrides: {
        engine: { stems: next.engine.stems }, mixing: next.mixing, routing: next.routing,
        mastering: next.mastering, processing: next.processing, format: next.format,
      },
      scene_overrides: selected.scene_overrides,
    }).then((updated) => { if (shouldApplyProject(seq)) setProject(updated); })
      .catch((reason) => setError((reason as Error).message));
  }, [projectId, selected, shouldApplyProject]);
  const saveReference = async (mastering_reference_id: string | null) => {
    if (!projectId || !project || !manifest) return;
    const seq = ++projectRequestSeq.current;
    try {
      const updated = await api.saveProject(projectId, {
        manifest: manifest as unknown as Record<string, unknown>,
        scene: project.scene as Record<string, unknown>,
        mastering_reference_id,
      });
      if (shouldApplyProject(seq)) setProject(updated);
    } catch (reason) { setError((reason as Error).message); }
  };
  const renameProject = async (name: string) => {
    if (!projectId || !project || !manifest) return;
    const seq = ++projectRequestSeq.current;
    try {
      const updated = await api.saveProject(projectId, {
        name,
        manifest: manifest as unknown as Record<string, unknown>,
        scene: project.scene as Record<string, unknown>,
      });
      if (shouldApplyProject(seq)) setProject(updated);
    } catch (reason) { setError((reason as Error).message); }
  };
  const savePreviewQuality = async (preview_quality: string) => {
    if (!projectId || !project || !manifest) return;
    const seq = ++projectRequestSeq.current;
    try {
      const updated = await api.saveProject(projectId, {
        preview_quality,
        manifest: manifest as unknown as Record<string, unknown>,
        scene: project.scene as Record<string, unknown>,
      });
      if (shouldApplyProject(seq)) setProject(updated);
    } catch (reason) { setError((reason as Error).message); }
  };
  const previewStems = selected?.stems.filter((stem) => project?.prepared_stems.includes(stem.stem_key.split("@", 1)[0])) || [];
  // Stereo stems get two halos (L/R) in the 3D scene instead of one collapsed
  // to a single point — keyed by base stem name, same convention as routing.
  const stemChannelCounts = React.useMemo(() => {
    const source = selected?.stems || project?.tracks[0]?.stems || [];
    const counts: Record<string, number> = {};
    for (const stem of source) counts[stem.stem_key.split("@", 1)[0]] = stem.channels;
    return counts;
  }, [selected, project]);
  // Stable identity across renders unless the layout actually changes — fed
  // straight into HazeView/ElevationView/ChannelMeters, which are memoized
  // specifically so they don't re-render on every playback frame.
  const routingLayout = effectiveManifest?.mixing.channel_layout || "7.1.4";
  const channels = React.useMemo(
    () => configuration?.choices.layout_channels?.[routingLayout] ?? [],
    [configuration, routingLayout],
  );
  // Session-only monitoring choices — not part of the manifest, so a reload
  // always starts back on binaural/studio.
  const [outputMode, setOutputMode] = React.useState<OutputMode>("binaural");
  const [spatialProfile, setSpatialProfile] = React.useState<SpatialProfile>("studio");
  const [transauralProfile, setTransauralProfile] = React.useState<TransauralProfile>("stereo");
  // A/B monitor bypass for the master chain — see monitorMastering.
  const [masteringBypassed, setMasteringBypassed] = React.useState(false);
  const {
    paneView, paneHeight, previewColumn, changePane, resizePaneTo,
    beginPaneResize, movePaneResize, endPaneResize, paneResizeKeys,
  } = usePaneLayout(projectId);
  const {
    rowRef, rowSize, hazeExtra, setHazeExtra, elevationExtra, setElevationExtra,
    commitColumnExtra, hazeMaxWidth, hazeWidth, metersMaxWidth, metersWidth,
  } = useColumnLayout(projectId);
  const [trackRailCollapsed, setTrackRailCollapsed] = React.useState(() => readStoredTrackRailCollapsed(projectId));
  React.useEffect(() => setTrackRailCollapsed(readStoredTrackRailCollapsed(projectId)), [projectId]);
  React.useEffect(() => {
    try {
      window.localStorage.setItem(trackRailStorageKey(projectId), trackRailCollapsed ? "1" : "0");
    } catch {
      // Storage being unavailable only costs the preference, not the view.
    }
  }, [projectId, trackRailCollapsed]);
  // Mastering is always per-track (see trackManifest below): the Mastering tab
  // edits the selected track's master and saves it to that track's overrides,
  // never the project-level default. The preview must render that same
  // per-track master — sourcing it from `effectiveManifest` instead would read
  // the project-level block in the default project edit-scope, so per-track
  // mastering edits would never reach the audio engine.
  // strength/spectrum/rms/max_db come entirely from the manifest (instant,
  // no round-trip, and genuinely live now — see Ledger D21); only the
  // correction curve (as `fir_url`, realized into a filter on demand) and
  // the level gain come from the server-precomputed asset.
  const previewMastering = React.useMemo(() => {
    if (!trackManifest?.mastering) return trackManifest?.mastering;
    const asset = project?.reference_match;
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
      },
    };
  }, [trackManifest?.mastering, project?.reference_match]);
  const engineConstants = React.useMemo(
    () => (configuration?.constants ? resolveEngineConstants(configuration.constants) : null),
    [configuration],
  );
  const monitoredMastering = React.useMemo(
    () => monitorMastering(previewMastering, masteringBypassed),
    [previewMastering, masteringBypassed],
  );
  const preview = useStemPreview(previewStems, {}, effectiveManifest?.mixing, selected?.source_preview_url || null, monitoredMastering, channels, outputMode, spatialProfile, transauralProfile, engineConstants);
  // One cached fetch per track, independent of stem decode — the envelope and
  // the track's duration arrive together, so the timeline can draw its ruler
  // and lanes while playback is still loading.
  const { peaks, loading: peaksLoading } = useTrackPeaks(selected);
  const ready = Boolean(project?.prepared_stems.length);
  const stemNames = project?.prepared_stems || [];
  // Reorder is a display-only preference (no backend field for it): kept in
  // client state and merged against the current stem list every render, so
  // stems appear/disappear correctly without needing a sync effect.
  const orderedStems = React.useMemo(() => {
    const known = new Set(stemNames);
    const kept = stemOrder.filter((stem) => known.has(stem));
    const missing = stemNames.filter((stem) => !kept.includes(stem));
    return [...kept, ...missing];
  }, [stemNames, stemOrder]);
  const reorderStems = React.useCallback((source: string, target: string) => {
    if (source === target) return;
    const next = orderedStems.filter((stem) => stem !== source);
    const targetIndex = next.indexOf(target);
    if (targetIndex === -1) return;
    next.splice(targetIndex, 0, source);
    setStemOrder(next);
  }, [orderedStems]);
  // Stable callbacks for the memoized `TimelineView` lane list — recreated
  // only when their few real dependencies change, not on every render (e.g.
  // every playback frame), so `React.memo` on `TimelineView` actually holds.
  const clearDraggedStem = React.useCallback(() => setDraggedStem(null), []);
  const handleDropOn = React.useCallback((target: string) => {
    setDraggedStem((current) => {
      if (current) reorderStems(current, target);
      return null;
    });
  }, [reorderStems]);
  const routing: StemRouting = React.useMemo(() => effectiveManifest?.mixing.stem_routing || {}, [effectiveManifest]);
  const updateRoute = (stem: string, patch: Record<string, number>) => {
    if (!effectiveManifest) return;
    updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_routing: { ...routing, [stem]: { ...routing[stem], ...patch } } } });
  };
  const applyPreset = async () => {
    if (!effectiveManifest || !stemNames.length) return;
    try {
      const next = await api.resolveStemRouting({ stems: stemNames, channel_layout: effectiveManifest.mixing.channel_layout, preset, intensity: presetIntensity });
      updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_routing: next } });
    } catch (reason) { setError((reason as Error).message); }
  };
  const toggleEnabled = React.useCallback((stem: string) => {
    if (!effectiveManifest) return;
    const current = effectiveManifest.mixing.stem_enabled[stem] !== false;
    updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_enabled: { ...effectiveManifest.mixing.stem_enabled, [stem]: !current }, stem_solo: effectiveManifest.mixing.stem_solo.filter((solo) => solo !== stem) } });
  }, [effectiveManifest, updateManifest]);
  const toggleSolo = React.useCallback((stem: string) => {
    if (!effectiveManifest) return;
    const solo = effectiveManifest.mixing.stem_solo;
    updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_solo: solo.includes(stem) ? solo.filter((item) => item !== stem) : [...solo, stem] } });
  }, [effectiveManifest, updateManifest]);
  // Stable identities for the memoized TimelineView/MixerView — an inline
  // arrow here would defeat the memo the design spec requires these canvas
  // surfaces to keep.
  const previewCommitScrub = preview.commitScrub;
  const commitScrub = React.useCallback((value: number) => { void previewCommitScrub(value); }, [previewCommitScrub]);
  const setStemGain = React.useCallback((stem: string, gain: number) => {
    if (!effectiveManifest) return;
    updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_rebalance: { ...effectiveManifest.mixing.stem_rebalance, [stem]: gain } } });
  }, [effectiveManifest, updateManifest]);
  const setAnchorStrength = React.useCallback((stem_source_anchor_strength: number) => {
    if (!effectiveManifest) return;
    updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_source_anchor_strength } });
  }, [effectiveManifest, updateManifest]);
  // Stems that produce no sound right now — muted outright, or silenced
  // because something else is soloed. The timeline dims their lanes and the
  // mixer labels the difference, since colour alone can't carry it.
  const silentStems = React.useMemo(() => {
    const solo = effectiveManifest?.mixing.stem_solo || [];
    return orderedStems.filter((stem) => (
      effectiveManifest?.mixing.stem_enabled[stem] === false
      || (solo.length > 0 && !solo.includes(stem))
    ));
  }, [effectiveManifest, orderedStems]);
  const transportDisabled = !preview.supported || !preview.ready || !previewStems.length;
  const { shortcutsOpen, setShortcutsOpen } = useKeyCommands({
    transportEnabled: !transportDisabled,
    preview,
    stems: orderedStems,
    selectedStem,
    onSelectStem: setSelectedStem,
    onToggleMute: toggleEnabled,
    onToggleSolo: toggleSolo,
    manifest: effectiveManifest,
    onManifestChange: updateManifest,
    paneView,
    onChangePane: changePane,
    onToggleMasterBypass: () => setMasteringBypassed((bypassed) => !bypassed),
  });
  const exportProject = async () => {
    if (!projectId) return;
    setExporting(true);
    try { await api.exportProject(projectId); navigate("/jobs"); } catch (reason) { setError((reason as Error).message); } finally { setExporting(false); }
  };
  const retry = async () => {
    if (!projectId) return;
    const seq = ++projectRequestSeq.current;
    const updated = await api.retryProject(projectId);
    if (shouldApplyProject(seq)) setProject(updated);
  };
  const reprepareStems = async () => {
    if (!projectId) return;
    const seq = ++projectRequestSeq.current;
    try {
      const updated = await api.reprepareProjectStems(projectId);
      if (shouldApplyProject(seq)) setProject(updated);
    } catch (reason) { setError((reason as Error).message); }
  };
  // `node` must stay referentially stable across renders — useHeaderTitle's
  // effect keys on it, so a fresh JSX element every render (e.g. inline
  // here) would re-fire the effect every render, which updates provider
  // state, which re-renders this component, forever. It's fine for the
  // memo's own deps to include `activeTab`/`settingsView` now that the
  // stage tabs live inside it — that's a real, bounded state change (a tab
  // click), not a fresh element on every render, so the effect fires once
  // per click and settles, the same as `project.name` changing.
  //
  // Three-column grid, the same `minmax(0,1fr)_auto_minmax(0,1fr)` trick
  // `Transport` uses (see its own `leading` prop comment) so the stage tabs
  // sit at the bar's true centre regardless of how long the project name or
  // the settings segment gets, rather than merely centred in whatever space
  // happens to be left over. The stage tabs are the app's workflow,
  // deliberately condensed into one segmented control — Project settings is
  // not a stage, so it sits on the right as its own one-segment
  // `SegmentedControl` (identical look/press behavior, no fifth tab).
  // Prepare (the "assets" stage) is reachable regardless of readiness —
  // it's where readiness comes from — so these tabs stay visible instead of
  // this being a full-page takeover the user can't navigate out of.
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
        // -mr-4 pulls this flush with Transport's col-3 right edge below:
        // AppShell's header reserves px-3 vs Transport's px-2 (4px), plus a
        // gap-3 before its own trailing icon slot, which this page leaves
        // empty (no onRefresh/onCreate) but which still claims the gap.
        className="justify-self-end -mr-4"
      />
    </div>
  ) : null, [project?.name, activeTab, settingsView]);
  useHeaderTitle(headerTitle);
  if (!project) return <main className="grid h-full place-items-center p-5 text-sm text-muted-foreground">{error || "Loading project…"}</main>;
  // What's left of the old merged stage/transport bar's leading slot once
  // the stage tabs and Save moved up into the top bar (above): just the
  // rail-reveal toggle.
  const transportLeading = (
    activeTab !== "assets" && !settingsView && (
      // Reopens `TrackRail` once collapsed — collapsing takes the rail
      // fully out of the layout (see TrackRail.tsx), so its own header
      // button can't be what brings it back. This is the one place
      // guaranteed to render whenever a rail-bearing stage is active.
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
          onToggle={() => setMasteringBypassed((bypassed) => !bypassed)}
        />
        <OutputModeSelect
          value={outputMode}
          onChange={setOutputMode}
          nativeSupported={preview.nativeSupported}
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
          manifest={effectiveManifest || manifest}
          configuration={configuration}
          onRename={(name) => void renameProject(name)}
          onChange={(next) => updateProjectManifest(next)}
          onPreviewQualityChange={(quality) => void savePreviewQuality(quality)}
        />
      </section>
    ) : activeTab === "assets" ? (
      <section className="flex min-h-0 flex-1 flex-col">
        <AssetsTab
          project={project}
          configuration={configuration}
          onProjectUpdate={(next) => { if (shouldApplyProject(++projectRequestSeq.current)) setProject(next); }}
          onOpenTrack={(trackId) => { setSelectedTrack(trackId); setActiveTab("mixing"); }}
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
          value={selectedTrack}
          onChange={setSelectedTrack}
          collapsed={trackRailCollapsed}
        />
      );
      const previewPanel = <section ref={previewColumn} className="flex min-h-0 flex-col">
        <div className="flex min-h-0 flex-1 flex-col gap-2 p-2">
        {preview.error && <p className="shrink-0 text-[11px] text-destructive">{preview.error}</p>}
        {!preview.error && preview.supported && !preview.ready && previewStems.length > 0 && (
          <div className="flex shrink-0 items-center gap-2 rounded-md border bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
            <span className="flex-1">Preparing preview — decoding stems…</span>
            <Progress value={preview.loadProgress * 100} className="w-24" />
            <span className="w-9 shrink-0 text-right tabular-nums">{Math.round(preview.loadProgress * 100)}%</span>
          </div>
        )}
        {/* Brief, first-play-only: the muted loudness warm-up in
            useStemPreview.ts (runLoudnessWarmup) measures real output level
            before letting any audio through, so playback never starts at an
            uncorrected (potentially louder) level. Reuses the decode-stems
            row's styling so it reads as the same kind of "getting ready"
            status rather than an unresponsive transport. */}
        {!preview.error && preview.ready && preview.measuring && (
          <div className="flex shrink-0 items-center gap-2 rounded-md border bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
            <span className="flex-1">Preparing preview — calibrating loudness…</span>
          </div>
        )}
        {/* The reference-match FIR is computed asynchronously on the server
            (WorkerManager.prepare_reference_match) and can take real time on
            a full song. previewMastering correctly falls back to the plain
            manifest mastering while project.reference_match is still null,
            so playback keeps going — but nothing told the user it's hearing
            the original EQ, not the match. Surface that instead of letting
            the match snap on silently once the SSE event lands. */}
        {!preview.error && project?.reference_match_pending && (
          <div className="flex shrink-0 items-center gap-2 rounded-md border bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
            <span className="flex-1">Preparing reference EQ match — preview using original EQ until ready…</span>
          </div>
        )}
        {/* Per-speaker mute is clickable directly on HazeView's speaker
            points — the preview renders the channel bed (see
            useStemPreview.ts), so a speaker can be silenced independently of
            any stem, same virtual-loudspeaker idea as Apple's Spatial Audio
            renderer. ChannelMeters mirrors the same layout-scoped `channels`
            array and mute state, and stays mounted alongside HazeView across
            Mixing/Mastering/Delivery since both live in this shared panel.
            The row is always Haze | Elevation | Meters, left to right,
            whether the bottom pane is open or collapsed — only the row's
            own total height (below) still depends on that, since a
            collapsed pane frees the vertical space back to this row. */}
        <div ref={rowRef} className={cn("flex min-h-0 gap-2", paneView ? "min-h-[180px] flex-1" : "flex-[3]")}>
          {/* Haze and Meters get an explicit pixel width (natural size + a
              persisted user delta); Elevation is a real `flex-1` and takes
              whatever's left — flexbox, not manual arithmetic, guarantees
              that always accounts for exactly 100% of the row, so the row
              can never develop a gap no display claims (the "magnetic,
              nothing floats loose" property). Both borders are real drag
              targets — a `StripResizeHandle` anchored to the wrapper on
              each border's *left* side, reusing the exact same drag/keys/
              double-click-reset contract the mixer rack's own column resize
              already uses. */}
          <div className="relative min-h-0 shrink-0" style={{ width: hazeWidth }}>
            <HazeView channels={channels} routing={routing} selectedStem={selectedStem} colors={stemColors} channelCounts={stemChannelCounts} onSelectStem={setSelectedStem} stemSpectrum={preview.stemSpectrum} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} active={preview.playing} className="h-full w-full" />
            <StripResizeHandle
              label="Resize Haze view"
              value={hazeExtra}
              onChange={setHazeExtra}
              onCommit={(px) => { setHazeExtra(px); commitColumnExtra("haze", px); }}
              min={HAZE_MIN_WIDTH - rowSize.height}
              max={hazeMaxWidth - rowSize.height}
            />
          </div>
          <div className="relative min-h-0 min-w-0 flex-1">
            <ElevationView channels={channels} routing={routing} selectedStem={selectedStem} colors={stemColors} channelCounts={stemChannelCounts} stemSpectrum={preview.stemSpectrum} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} active={preview.playing} className="h-full w-full" />
            {/* Dragging this border moves `elevationExtra`, the same delta
                as before — it still reads as "resize Elevation" to the
                user, it just now expresses itself by shrinking/growing
                Meters' explicit width (below) rather than Elevation's own,
                since Elevation no longer has an explicit width to change. */}
            <StripResizeHandle
              label="Resize Elevation view"
              value={elevationExtra}
              onChange={setElevationExtra}
              onCommit={(px) => { setElevationExtra(px); commitColumnExtra("elevation", px); }}
              min={METERS_DEFAULT_SHARE - metersMaxWidth}
              max={METERS_DEFAULT_SHARE - METERS_MIN_WIDTH}
            />
          </div>
          <div className="relative min-h-0 shrink-0" style={{ width: metersWidth }}>
            <ChannelMeters channels={channels} channelLevels={preview.channelLevels} headphoneLevels={preview.headphoneLevels} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} outputMode={outputMode} active={preview.playing} className="h-full w-full" />
          </div>
        </div>
        </div>
        {paneView && (
          <div
            role="separator"
            aria-label="Resize bottom pane"
            aria-orientation="horizontal"
            aria-valuenow={paneHeight}
            aria-valuemin={PANE_MIN_HEIGHT}
            tabIndex={0}
            onPointerDown={beginPaneResize}
            onPointerMove={movePaneResize}
            onPointerUp={endPaneResize}
            onPointerCancel={endPaneResize}
            onKeyDown={paneResizeKeys}
            onDoubleClick={() => resizePaneTo(PANE_DEFAULT_HEIGHT)}
            className="group flex h-2 shrink-0 cursor-row-resize touch-none items-center justify-center border-t bg-muted/40 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
          >
            <span className="h-0.5 w-8 rounded-full bg-border transition-colors group-hover:bg-muted-foreground" aria-hidden="true" />
          </div>
        )}
        <div className={cn("flex h-8 shrink-0 items-center gap-2 bg-card px-2", !paneView && "border-t")}>
          <SegmentedControl
            aria-label="Bottom pane"
            size="sm"
            segments={PANE_SEGMENTS}
            value={(paneView ?? "") as "timeline" | "mixer"}
            onChange={changePane}
          />
          <div className="min-w-0 flex-1" />
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            aria-label={paneView ? "Collapse bottom pane" : "Show bottom pane"}
            aria-expanded={Boolean(paneView)}
            onClick={() => changePane(paneView ? null : "timeline")}
          >
            {paneView ? <ChevronDown /> : <ChevronUp />}
          </Button>
        </div>
        {paneView && <div className="h-2 shrink-0 bg-muted/40" aria-hidden="true" />}
        {paneView === "timeline" && (
          <TimelineView
            className="shrink-0 border-t"
            style={{ height: paneHeight }}
            stems={orderedStems}
            peaks={peaks}
            loading={peaksLoading}
            pending={Boolean(project?.peaks_pending)}
            mutedStems={silentStems}
            enabled={effectiveManifest?.mixing.stem_enabled || {}}
            solo={effectiveManifest?.mixing.stem_solo || []}
            onToggleMute={toggleEnabled}
            onToggleSolo={toggleSolo}
            gains={effectiveManifest?.mixing.stem_rebalance || {}}
            onGain={setStemGain}
            stemLevels={preview.stemLevels}
            stemChannelCounts={stemChannelCounts}
            draggedStem={draggedStem}
            onDragStart={setDraggedStem}
            onDragEnd={clearDraggedStem}
            onDropOn={handleDropOn}
            selectedStem={selectedStem}
            onSelectStem={setSelectedStem}
            duration={preview.duration || selected?.peaks_duration_seconds || 0}
            currentTime={preview.currentTime}
            currentTimeRef={preview.currentTimeRef}
            playing={preview.playing}
            disabled={!preview.supported || !previewStems.length}
            onBeginScrub={preview.beginScrub}
            onScrubTo={preview.scrubTo}
            onCommitScrub={commitScrub}
          />
        )}
        {paneView === "mixer" && effectiveManifest && (
          <MixerView
            className="shrink-0 border-t"
            style={{ height: paneHeight }}
            stems={orderedStems}
            stemChannels={stemChannelCounts}
            selectedStem={selectedStem}
            onSelectStem={setSelectedStem}
            gains={effectiveManifest.mixing.stem_rebalance}
            onGain={setStemGain}
            enabled={effectiveManifest.mixing.stem_enabled}
            solo={effectiveManifest.mixing.stem_solo}
            onToggleMute={toggleEnabled}
            onToggleSolo={toggleSolo}
            stemLevels={preview.stemLevels}
            anchorStrength={effectiveManifest.mixing.stem_source_anchor_strength}
            onAnchorStrength={setAnchorStrength}
            headphoneLevels={preview.headphoneLevels}
            volume={preview.volume}
            onVolume={preview.setVolume}
            muted={preview.muted}
            onToggleMasterMute={preview.toggleMute}
            active={preview.playing}
            disabled={!previewStems.length}
          />
        )}
      </section>;
      // Preview stays mounted across all three tabs (same center/left column
      // position) so playback and the routing graphs never stop just because
      // the user switched to Mastering or Delivery.
      // The stem rail was removed once the timeline pane's lanes took over
      // its exact job (select/mute/solo/reorder, see TimelineView.tsx) — one
      // list, shown wherever the bottom pane already is instead of a second
      // copy beside it. Track switching lives in `trackRail` (left of the
      // preview column, below), replacing the old toolbar dropdown.
      // `TrackRail` stays mounted at `w-0` when collapsed rather than
      // unmounting (see TrackRail.tsx), so this grid template stays fixed
      // at 3 tracks regardless of collapse state — the first (`auto`) track
      // just sizes to whatever width the rail is currently animating
      // through, which is what makes the collapse/expand a smooth column
      // resize instead of a CSS Grid auto-placement jump.
      if (activeTab === "mixing") return <div className="grid min-h-0 flex-1 xl:grid-cols-[auto_minmax(0,1fr)_320px]">
        {trackRail}
        {previewPanel}
        {effectiveManifest && <div className="flex min-h-0 flex-col overflow-y-auto border-l bg-card">
          <InspectorGroup
            title="Routing preset"
            actions={<select aria-label="Edit scope" className="h-6 rounded-md border bg-secondary px-1 text-[11px]" value={editScope} onChange={(event) => setEditScope(event.target.value as "project" | "track")}><option value="project">Project</option><option value="track" disabled={!selected}>Track</option></select>}
          >
            <p className="mb-2 text-[11px] text-muted-foreground">{editScope === "project" ? "Default for every track" : `Override: ${selected?.asset.title || selected?.asset.filename}`}</p>
            <select className="flex h-7 w-full rounded-md border bg-secondary px-2 text-[13px]" value={preset} onChange={(event) => setPreset(event.target.value)}>{(configuration?.choices.stem_routing_presets ?? []).map((name) => <option key={name}>{name}</option>)}</select>
            <label className="mt-2.5 block text-[11px] text-muted-foreground">Intensity <span className="float-right tabular-nums">{presetIntensity.toFixed(2)}</span><Slider className="mt-2" min={0} max={1} step={0.01} value={[presetIntensity]} onValueChange={([value]) => setPresetIntensity(value)} /></label>
            <Button className="mt-2.5 w-full" variant="outline" size="sm" onClick={() => void applyPreset()}><Wand2 />Apply preset</Button>
          </InspectorGroup>
          <InspectorGroup title="Stem">
            {selectedStem ? (() => {
              const SelectedStemIcon = getStemIcon(selectedStem);
              const stemMuted = effectiveManifest.mixing.stem_enabled[selectedStem] === false;
              return <>
                {/* The section's one title, standing in for the fader's own
                    nameplate below (`showNameplate={false}`) — repeating the
                    stem name twice in one scroll-length panel is the kind of
                    duplication §6.3 rejects, not a second, useful label. */}
                <p className="mb-3 flex items-center gap-1.5 text-[13px] font-semibold">
                  <SelectedStemIcon className="h-3.5 w-3.5 shrink-0" style={{ color: getStemColor(selectedStem) }} aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{selectedStem}</span>
                  <span className="text-[11px] font-normal text-muted-foreground">{stemMuted ? "muted" : "enabled"}</span>
                </p>
                <StemControls route={routing[selectedStem] || {}} channels={channels} eq={effectiveManifest.mixing.stem_eq[selectedStem] || ""} onRoute={(patch) => updateRoute(selectedStem, patch)} onEq={(eq) => updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_eq: (() => { const next = { ...effectiveManifest.mixing.stem_eq }; if (eq) next[selectedStem] = eq; else delete next[selectedStem]; return next; })() } })}
                  stemEqProfiles={configuration?.choices.stem_eq_profiles}
                />
                {/* The mixer pane can be collapsed or switched to the
                    timeline, so the selected stem's fader lives here too —
                    the exact same control (see ChannelStrip.tsx), adapted
                    for a single centered strip instead of a rack, so it
                    never goes away along with the mixer. */}
                <div className="mt-3 flex justify-center border-t pt-3">
                  <StemChannelStrip
                    stem={selectedStem}
                    subjectName="Selected stem"
                    showNameplate={false}
                    channels={stemChannelCounts[selectedStem] ?? 1}
                    gain={effectiveManifest.mixing.stem_rebalance[selectedStem] || 0}
                    onGain={(gain) => setStemGain(selectedStem, gain)}
                    muted={stemMuted}
                    soloed={effectiveManifest.mixing.stem_solo.includes(selectedStem)}
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
      // Mastering and Delivery are per-track: each track carries its own
      // master and delivery format (`trackManifest`/`updateTrackManifest`,
      // ignoring the Mixing tab's project/track edit-scope toggle — see
      // where those are defined). A project only ever reaches this branch
      // once ready, and `selectedTrack` auto-inits to the first track on
      // load, so `!selected` here means no track survived a delete —
      // point back at the rail rather than rendering panels with nothing to
      // edit.
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
                .then((reference) => saveReference(reference.id))
                .catch((reason) => setError((reason as Error).message));
            }}
            onReferenceClear={() => { void saveReference(null); }}
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
      <StatusCell label="Stems" value={`${stemNames.filter((stem) => effectiveManifest?.mixing.stem_enabled[stem] !== false).length}/${stemNames.length}`} />
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

const StemControls = React.memo(function StemControls({ route, channels, eq, onRoute, onEq, stemEqProfiles }: { route: Record<string, number>; channels: string[]; eq: string; onRoute: (patch: Record<string, number>) => void; onEq: (eq: string) => void; stemEqProfiles?: string[] }) {
  const position = routePosition(route, channels);
  const setPosition = (patch: Partial<typeof position>) => onRoute(routeForPosition(channels, { ...position, ...patch }, route.LFE || 0));
  const hasHeight = channels.includes("TFL") || channels.includes("TFR") || channels.includes("TBL") || channels.includes("TBR");
  const hasLfe = channels.includes("LFE");
  // Gain has its own control now — the always-accessible fader above (see
  // ProjectDetailPage's "Stem" InspectorGroup) — so this section only covers
  // what the fader doesn't: spatial placement, LFE send, and EQ.
  return <div className="space-y-3"><label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><ArrowLeftRight className="h-3 w-3" />Front <span className="ml-auto">Back</span></span><Slider aria-label="Front to back" className="mt-1.5" min={0} max={1} step={0.01} value={[position.depth]} onValueChange={([depth]) => setPosition({ depth })} /></label>{hasHeight && <label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><ArrowUpDown className="h-3 w-3" />Floor <span className="ml-auto">Height</span></span><Slider aria-label="Floor to height" className="mt-1.5" min={0} max={1} step={0.01} value={[position.height]} onValueChange={([height]) => setPosition({ height })} /></label>}{hasLfe && <label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><Waves className="h-3 w-3" />LFE send</span><Slider aria-label="LFE send" className="mt-1.5" min={0} max={1} step={0.01} value={[route.LFE ?? 0]} onValueChange={([lfe]) => onRoute({ LFE: lfe })} /></label>}<label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><AudioWaveform className="h-3 w-3" />EQ</span><select className="mt-1.5 flex h-7 w-full rounded-md border bg-secondary px-2 text-[13px] text-foreground" value={eq} onChange={(event) => onEq(event.target.value)}><option value="">None</option>{(stemEqProfiles ?? []).filter((name) => name !== "flat").map((name) => <option key={name} value={name}>{name}</option>)}</select></label></div>;
});

function routePosition(route: Record<string, number>, channels: string[]) {
  const weight = (names: string[]) => names.reduce((total, name) => total + (route[name] || 0), 0);
  const top = weight(["TFL", "TFR", "TBL", "TBR"]);
  const floor = weight(["FL", "FR", "C", "SL", "SR", "BL", "BR"]);
  const front = weight(["FL", "FR", "C", "TFL", "TFR"]);
  const hasBack = channels.includes("BL") || channels.includes("BR");
  const side = weight(["SL", "SR"]);
  const back = weight(["BL", "BR", "TBL", "TBR"]);
  const middle = hasBack ? side : 0;
  const rear = hasBack ? back : side;
  const total = front + middle + rear || 1;
  return { depth: Math.min(1, Math.max(0, (middle * 0.5 + rear) / total)), height: Math.min(1, Math.max(0, top / (top + floor || 1))) };
}

function routeForPosition(channels: string[], position: { depth: number; height: number }, lfe: number) {
  const present = new Set(channels);
  const hasBack = present.has("BL") || present.has("BR");
  const front = hasBack ? Math.max(0, 1 - position.depth * 2) : 1 - position.depth;
  const middle = hasBack ? 1 - Math.abs(position.depth * 2 - 1) : 0;
  const back = hasBack ? Math.max(0, position.depth * 2 - 1) : position.depth;
  const floor = 1 - position.height;
  const route: Record<string, number> = Object.fromEntries(channels.map((channel) => [channel, 0]));
  const send = (names: string[], total: number) => {
    const available = names.filter((channel) => present.has(channel));
    for (const channel of available) route[channel] = total / available.length;
  };
  send(["FL", "FR", "C"], floor * front);
  send(["SL", "SR"], floor * (middle + (hasBack ? 0 : back)));
  send(["BL", "BR"], floor * back);
  send(["TFL", "TFR"], position.height * (1 - position.depth));
  send(["TBL", "TBR"], position.height * position.depth);
  if (present.has("LFE")) route.LFE = lfe;
  return route;
}
