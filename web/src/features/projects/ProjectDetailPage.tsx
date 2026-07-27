import * as React from "react";
import {
  ArrowLeftRight,
  ArrowUpDown,
  AudioWaveform,
  ChevronLeft,
  Code2,
  Download,
  GripVertical,
  Loader2,
  Package,
  Settings,
  SlidersHorizontal,
  Wand2,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Configuration, type Project, type StemRouting } from "@/api";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { InspectorGroup } from "@/app/InspectorRow";
import { SegmentedControl } from "@/app/SegmentedControl";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Toolbar, ToolbarGroup, ToolbarSpacer } from "@/app/Toolbar";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Slider } from "@/components/ui/slider";
import { AdvancedSection } from "@/features/composer/sections/AdvancedSection";
import { MasteringSection } from "@/features/composer/sections/MasteringSection";
import { normalizeManifest, type Manifest } from "@/lib/manifest";
import { getStemColor, getStemIcon, stemColors } from "@/lib/stems";
import { cn } from "@/lib/utils";
import HazeView from "./HazeView";
import ChannelMeters from "./ChannelMeters";
import ElevationView from "./ElevationView";
import type { SpatialProfile } from "./masteringProfiles";
import { OutputModeSelect } from "./OutputModeSelect";
import { PreparationView } from "./PreparationView";
import { ProjectDeliverySection } from "./ProjectDeliverySection";
import { ProjectSettingsSection } from "./ProjectSettingsSection";
import { Transport } from "./Transport";
import { useStemPreview, type OutputMode } from "./useStemPreview";

type Stage = "mixing" | "mastering" | "delivery";

const STAGES = [
  { value: "mixing" as const, label: "Mixing", icon: SlidersHorizontal },
  { value: "mastering" as const, label: "Mastering", icon: AudioWaveform },
  { value: "delivery" as const, label: "Delivery", icon: Package },
];

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
  const [manifestView, setManifestView] = React.useState(false);
  const [settingsView, setSettingsView] = React.useState(false);
  const [rawManifest, setRawManifest] = React.useState("");
  const [rawError, setRawError] = React.useState<string | null>(null);
  const [preset, setPreset] = React.useState("balanced");
  const [presetIntensity, setPresetIntensity] = React.useState(1);
  const [error, setError] = React.useState<string | null>(null);
  const [exporting, setExporting] = React.useState(false);
  const saveTimer = React.useRef<number | null>(null);
  const initialized = React.useRef(false);
  React.useEffect(() => { initialized.current = false; }, [projectId]);
  const load = React.useCallback(async () => {
    if (!projectId) return;
    try {
      const next = await api.getProject(projectId);
      setProject(next);
      if (!initialized.current) {
        initialized.current = true;
        setManifest(normalizeManifest(next.manifest));
        setSelectedTrack(next.tracks[0]?.id || null);
      }
      setError(null);
    } catch (reason) { setError((reason as Error).message); }
  }, [projectId]);
  // Polling stops once the project reaches a terminal status — the SSE
  // stream below covers live progress while preparing, and once ready there
  // is nothing server-side left to pick up on this page (saves/exports all
  // come back through their own API responses) — except a reference-match
  // recompute, which runs asynchronously after a settings save (see
  // upmixer_web/worker.py::WorkerManager.schedule_reference_match); keep
  // polling while one is pending so `reference_match` refreshes once it
  // lands. Re-subscribes on `status`/`reference_match_pending` so a retry or
  // a pending recompute resumes polling.
  React.useEffect(() => {
    void load();
    if (
      project
      && ["ready", "failed", "expansion_failed"].includes(project.status)
      && !project.reference_match_pending
    ) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on status/reference_match_pending only, same as the SSE effect below: a mid-poll `project` update (content changes, same status) shouldn't tear down and restart the interval
  }, [load, project?.status, project?.reference_match_pending]);
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
    ) return;
    const source = new EventSource(api.projectEventsUrl(projectId));
    source.onmessage = (event) => {
      try { setProject(JSON.parse(event.data)); } catch { /* ignore malformed frame */ }
    };
    source.onerror = () => source.close();
    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on status/reference_match_pending only, so a mid-stream progress update (also named `project`) doesn't tear down and reopen the connection
  }, [projectId, project?.status, project?.reference_match_pending]);
  React.useEffect(() => () => { if (saveTimer.current) window.clearTimeout(saveTimer.current); }, []);
  const queueSave = React.useCallback((next: Manifest) => {
    if (!projectId || !project) return;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void api.saveProject(projectId, { manifest: next as unknown as Record<string, unknown>, scene: project.scene as Record<string, unknown> })
        .then(setProject).catch((reason) => setError((reason as Error).message));
    }, 350);
  }, [project, projectId]);
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
    void api.saveProjectTrack(projectId, selected.id, {
      manifest_overrides: {
        engine: next.engine, mixing: next.mixing, routing: next.routing,
        mastering: next.mastering, processing: next.processing, format: next.format,
      },
      scene_overrides: selected.scene_overrides,
    }).then(setProject).catch((reason) => setError((reason as Error).message));
  }, [editScope, projectId, selected, queueSave]);
  // Mastering and delivery are whole-project concerns (one master, one
  // deliverable) — always write straight to the project manifest regardless
  // of the mixing tab's project/track edit scope. Track-scope saves
  // (above) only persist `mixing` overrides today, so routing these through
  // `updateManifest` while a track is selected would silently drop the edit.
  const updateProjectManifest = (next: Manifest) => {
    setManifest(next);
    queueSave(next);
  };
  const saveReference = async (mastering_reference_id: string | null) => {
    if (!projectId || !project || !manifest) return;
    try {
      setProject(await api.saveProject(projectId, {
        manifest: manifest as unknown as Record<string, unknown>,
        scene: project.scene as Record<string, unknown>,
        mastering_reference_id,
      }));
    } catch (reason) { setError((reason as Error).message); }
  };
  const renameProject = async (name: string) => {
    if (!projectId || !project || !manifest) return;
    try {
      setProject(await api.saveProject(projectId, {
        name,
        manifest: manifest as unknown as Record<string, unknown>,
        scene: project.scene as Record<string, unknown>,
      }));
    } catch (reason) { setError((reason as Error).message); }
  };
  const savePreviewQuality = async (preview_quality: string) => {
    if (!projectId || !project || !manifest) return;
    try {
      setProject(await api.saveProject(projectId, {
        preview_quality,
        manifest: manifest as unknown as Record<string, unknown>,
        scene: project.scene as Record<string, unknown>,
      }));
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
    () => configuration?.choices.layout_channels?.[routingLayout] || ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR"],
    [configuration, routingLayout],
  );
  // Session-only monitoring choices — not part of the manifest, so a reload
  // always starts back on binaural/studio.
  const [outputMode, setOutputMode] = React.useState<OutputMode>("binaural");
  const [spatialProfile, setSpatialProfile] = React.useState<SpatialProfile>("studio");
  // Live strength/spectrum/rms toggles come from the manifest (instant, no
  // server round-trip needed — they're just a wet/dry blend and an RMS gate,
  // same as the named-EQ `strength` slider); the FIR itself and the
  // computed RMS gain value come from the project's server-precomputed
  // asset (`project.reference_match`), which only changes when the
  // reference, layout, or match params are saved (see
  // upmixer_web/worker.py::WorkerManager.prepare_reference_match and
  // docs/contracts/preview_export_parity.md Ledger D12).
  const previewMastering = React.useMemo(() => {
    if (!effectiveManifest?.mastering) return effectiveManifest?.mastering;
    const asset = project?.reference_match;
    if (!asset) return effectiveManifest.mastering;
    const liveMatch = effectiveManifest.mastering.match_reference;
    return {
      ...effectiveManifest.mastering,
      match_reference: {
        fir_url: asset.fir_url,
        channels: asset.channels,
        rms_gain_db: asset.rms_gain_db,
        strength: liveMatch?.strength ?? asset.strength,
        spectrum: liveMatch?.spectrum ?? asset.spectrum,
        rms: liveMatch?.rms ?? asset.rms,
      },
    };
  }, [effectiveManifest?.mastering, project?.reference_match]);
  const preview = useStemPreview(previewStems, {}, effectiveManifest?.mixing, selected?.source_preview_url || null, previewMastering, channels, outputMode, spatialProfile);
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
  // Stable callbacks for the memoized `StemRow` list — recreated only when
  // their few real dependencies change, not on every render (e.g. every
  // playback frame), so `React.memo` on `StemRow` actually holds.
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
  const exportProject = async () => {
    if (!projectId) return;
    setExporting(true);
    try { await api.exportProject(projectId); navigate("/jobs"); } catch (reason) { setError((reason as Error).message); } finally { setExporting(false); }
  };
  const retry = async () => { if (projectId) setProject(await api.retryProject(projectId)); };
  // `node` must stay referentially stable across renders — useHeaderTitle's
  // effect keys on it, so a fresh JSX element every render (e.g. inline
  // here) would re-fire the effect every render, which updates provider
  // state, which re-renders this component, forever.
  const headerTitle = React.useMemo(() => project ? <div className="flex min-w-0 items-center gap-1.5"><Link to="/projects" className="flex shrink-0 items-center gap-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground"><ChevronLeft className="h-3.5 w-3.5" />Projects</Link><span className="text-muted-foreground">/</span><span className="truncate text-[13px] font-semibold">{project.name}</span></div> : null, [project?.name]);
  useHeaderTitle(headerTitle);
  if (!project) return <main className="grid h-full place-items-center p-5 text-sm text-muted-foreground">{error || "Loading project…"}</main>;
  if (!ready) return <PreparationView project={project} onRetry={() => void retry()} />;
  return <main className="flex h-[calc(100vh-var(--topbar-h))] w-full flex-col overflow-hidden">
    <Toolbar>
      <SegmentedControl
        aria-label="Project stage"
        segments={STAGES}
        value={settingsView || manifestView ? ("" as Stage) : activeTab}
        onChange={(value) => {
          setActiveTab(value);
          setSettingsView(false);
          setManifestView(false);
        }}
      />
      <ToolbarSpacer />
      <ToolbarGroup>
        <Button
          variant={settingsView ? "secondary" : "ghost"}
          size="sm"
          aria-pressed={settingsView}
          onClick={() => { setManifestView(false); setSettingsView(true); }}
        >
          <Settings />
          Project settings
        </Button>
        <Button
          variant={manifestView ? "secondary" : "ghost"}
          size="sm"
          aria-pressed={manifestView}
          onClick={() => {
            if (!manifestView && effectiveManifest) setRawManifest(JSON.stringify(effectiveManifest, null, 2));
            setSettingsView(false);
            setManifestView(true);
          }}
        >
          <Code2 />
          Manifest JSON
        </Button>
      </ToolbarGroup>
    </Toolbar>
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
    ) : manifestView ? (
      <section className="min-h-0 flex-1 overflow-auto p-3">
        <AdvancedSection rawManifest={rawManifest} rawError={rawError} onChange={(value) => {
          setRawManifest(value);
          try {
            const next = normalizeManifest(JSON.parse(value) as Record<string, unknown>);
            setRawError(null);
            updateManifest(next);
          } catch (reason) { setRawError((reason as Error).message); }
        }} />
      </section>
    ) : (() => {
      // Transport is chrome, not one of the displays: it sits flush at the
      // top of the preview column as a full-bleed bar, so the only things
      // reading as floating panels here are the metering surfaces.
      const previewPanel = <section className="flex min-h-0 flex-col">
        <Transport
          playing={preview.playing}
          currentTime={preview.currentTime}
          currentTimeRef={preview.currentTimeRef}
          duration={preview.duration}
          volume={preview.volume}
          muted={preview.muted}
          loop={preview.loop}
          disabled={!preview.supported || !preview.ready || !previewStems.length}
          onPlayPause={() => void preview.playPause()}
          onStop={preview.stop}
          onToggleLoop={preview.toggleLoop}
          onSetVolume={preview.setVolume}
          onToggleMute={preview.toggleMute}
          onBeginScrub={preview.beginScrub}
          onScrubTo={preview.scrubTo}
          onCommitScrub={(value) => void preview.commitScrub(value)}
        >
          <OutputModeSelect
            value={outputMode}
            onChange={setOutputMode}
            nativeSupported={preview.nativeSupported}
            devices={preview.outputDevices}
            deviceId={preview.outputDeviceId}
            onDeviceChange={(deviceId) => void preview.setOutputDeviceId(deviceId)}
            spatialProfile={spatialProfile}
            onSpatialProfileChange={setSpatialProfile}
          />
        </Transport>
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
            Mixing/Mastering/Delivery since both live in this shared panel. */}
        <div className="flex min-h-0 flex-[3] gap-2">
          <HazeView channels={channels} routing={routing} selectedStem={selectedStem} colors={stemColors} channelCounts={stemChannelCounts} onSelectStem={setSelectedStem} stemSpectrum={preview.stemSpectrum} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} active={preview.playing} className="min-h-0 min-w-0 flex-[2]" />
          <ChannelMeters channels={channels} channelLevels={preview.channelLevels} headphoneLevels={preview.headphoneLevels} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} outputMode={outputMode} active={preview.playing} />
        </div>
        <ElevationView channels={channels} routing={routing} selectedStem={selectedStem} colors={stemColors} channelCounts={stemChannelCounts} stemSpectrum={preview.stemSpectrum} speakerEnabled={preview.speakerEnabled} onToggleSpeaker={preview.toggleSpeaker} active={preview.playing} className="h-40 shrink-0" />
        </div>
      </section>;
      // Preview stays mounted across all three tabs (same center/left column
      // position) so playback and the routing graphs never stop just because
      // the user switched to Mastering or Delivery.
      if (activeTab === "mixing") return <div className="grid min-h-0 flex-1 xl:grid-cols-[248px_minmax(0,1fr)_320px]">
        <aside className="min-h-0 overflow-y-auto border-r bg-card p-2">
          {project.tracks.length > 1 && <>
            <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground">Tracks</p>
            {project.tracks.map((track) => <button key={track.id} onClick={() => setSelectedTrack(track.id)} className={cn("mb-0.5 flex h-7 w-full items-center rounded-md px-2 text-left text-[13px] transition-colors", selectedTrack === track.id ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground hover:bg-accent hover:text-foreground")}><span className="truncate">{track.asset.title || track.asset.filename}</span></button>)}
          </>}
          <p className={cn("px-2 pb-1 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground", project.tracks.length > 1 && "mt-3")}>Stems</p>
          {orderedStems.map((stem) => <StemRow
            key={stem}
            stem={stem}
            selected={selectedStem === stem}
            muted={effectiveManifest?.mixing.stem_enabled[stem] === false}
            soloed={Boolean(effectiveManifest?.mixing.stem_solo.includes(stem))}
            dragging={draggedStem === stem}
            onSelect={setSelectedStem}
            onToggleMute={toggleEnabled}
            onToggleSolo={toggleSolo}
            onDragStart={setDraggedStem}
            onDragEnd={clearDraggedStem}
            onDropOn={handleDropOn}
          />)}
        </aside>
        {previewPanel}
        {effectiveManifest && <div className="flex min-h-0 flex-col overflow-y-auto border-l bg-card">
          <InspectorGroup
            title="Routing preset"
            actions={<select aria-label="Edit scope" className="h-6 rounded-md border bg-secondary px-1 text-[11px]" value={editScope} onChange={(event) => setEditScope(event.target.value as "project" | "track")}><option value="project">Project</option><option value="track" disabled={!selected}>Track</option></select>}
          >
            <p className="mb-2 text-[11px] text-muted-foreground">{editScope === "project" ? "Default for every track" : `Override: ${selected?.asset.title || selected?.asset.filename}`}</p>
            <select className="flex h-7 w-full rounded-md border bg-secondary px-2 text-[13px]" value={preset} onChange={(event) => setPreset(event.target.value)}>{(configuration?.choices.stem_routing_presets || ["balanced", "intimate", "rhythmic", "spacious", "live", "detailed"]).map((name) => <option key={name}>{name}</option>)}</select>
            <label className="mt-2.5 block text-[11px] text-muted-foreground">Intensity <span className="float-right tabular-nums">{presetIntensity.toFixed(2)}</span><Slider className="mt-2" min={0} max={1} step={0.01} value={[presetIntensity]} onValueChange={([value]) => setPresetIntensity(value)} /></label>
            <Button className="mt-2.5 w-full" variant="outline" size="sm" onClick={() => void applyPreset()}><Wand2 />Apply preset</Button>
          </InspectorGroup>
          <InspectorGroup title="Stem">
            {selectedStem ? <StemControls stem={selectedStem} route={routing[selectedStem] || {}} channels={channels} enabled={effectiveManifest.mixing.stem_enabled[selectedStem] !== false} gain={effectiveManifest.mixing.stem_rebalance[selectedStem] || 0} eq={effectiveManifest.mixing.stem_eq[selectedStem] || ""} onRoute={(patch) => updateRoute(selectedStem, patch)} onGain={(gain) => updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_rebalance: { ...effectiveManifest.mixing.stem_rebalance, [selectedStem]: gain } } })} onEq={(eq) => updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_eq: (() => { const next = { ...effectiveManifest.mixing.stem_eq }; if (eq) next[selectedStem] = eq; else delete next[selectedStem]; return next; })() } })}
              stemEqProfiles={configuration?.choices.stem_eq_profiles}
            /> : <p className="text-[11px] text-muted-foreground">Select a stem to edit its sends.</p>}
          </InspectorGroup>
          <InspectorGroup title="Source anchor">
            <div className="flex items-center justify-between text-[11px]">
              <span className="text-muted-foreground">Blend</span>
              <span className="font-medium tabular-nums">{Math.round(effectiveManifest.mixing.stem_source_anchor_strength * 100)}%</span>
            </div>
            <Slider aria-label="Source anchor" className="mt-2" min={0} max={1} step={0.01} value={[effectiveManifest.mixing.stem_source_anchor_strength]} onValueChange={([stem_source_anchor_strength]) => updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_source_anchor_strength } })} />
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">Blends original channel pairs back into the mix.</p>
          </InspectorGroup>
        </div>}
      </div>;
      if (activeTab === "mastering") return manifest && <div className="grid min-h-0 flex-1 xl:grid-cols-[minmax(0,1fr)_460px]">
        {previewPanel}
        <section className="min-h-0 overflow-auto border-l bg-card p-3">
          <MasteringSection
            manifest={manifest}
            setManifest={(update) => updateProjectManifest(typeof update === "function" ? update(manifest) : update)}
            configuration={configuration}
            masteringReference={project.mastering_reference || null}
            referenceUploading={false}
            referenceError={null}
            referencePending={Boolean(project.reference_match_pending)}
            onReferenceUpload={(file) => {
              void api.uploadMasteringReference(project.import_id, file)
                .then((reference) => saveReference(reference.id))
                .catch((reason) => setError((reason as Error).message));
            }}
            onReferenceClear={() => { void saveReference(null); }}
          />
        </section>
      </div>;
      return manifest && <div className="grid min-h-0 flex-1 xl:grid-cols-[minmax(0,1fr)_460px]">
        {previewPanel}
        <section className="flex min-h-0 flex-col border-l bg-card">
          <div className="min-h-0 flex-1 overflow-auto p-3">
            <ProjectDeliverySection manifest={manifest} configuration={configuration} onChange={updateProjectManifest} />
          </div>
          <div className="shrink-0 border-t p-2">
            <Button className="w-full" disabled={exporting} onClick={() => void exportProject()}><Download />{exporting ? "Queueing" : "Export project"}</Button>
          </div>
        </section>
      </div>;
    })()}
    <StatusBar>
      <StatusCell label="Layout" value={routingLayout} />
      <StatusSeparator />
      <StatusCell label="Channels" value={channels.length} />
      <StatusSeparator />
      <StatusCell label="Stems" value={`${stemNames.filter((stem) => effectiveManifest?.mixing.stem_enabled[stem] !== false).length}/${stemNames.length}`} />
      <StatusSeparator />
      <StatusCell label="Monitor" value={outputMode === "binaural" ? `Binaural · ${spatialProfile}` : "Speakers"} />
      <StatusSpacer />
      <StatusCell label="Transport" value={preview.playing ? "Playing" : preview.ready ? "Ready" : "Loading"} />
    </StatusBar>
  </main>;
}

const StemRow = React.memo(function StemRow({
  stem,
  selected,
  muted,
  soloed,
  dragging,
  onSelect,
  onToggleMute,
  onToggleSolo,
  onDragStart,
  onDragEnd,
  onDropOn,
}: {
  stem: string;
  selected: boolean;
  muted: boolean;
  soloed: boolean;
  dragging: boolean;
  onSelect: (stem: string) => void;
  onToggleMute: (stem: string) => void;
  onToggleSolo: (stem: string) => void;
  onDragStart: (stem: string) => void;
  onDragEnd: () => void;
  onDropOn: (stem: string) => void;
}) {
  const StemIcon = getStemIcon(stem);
  return <div
    draggable
    onDragStart={(event) => { event.dataTransfer.effectAllowed = "move"; onDragStart(stem); }}
    onDragEnd={onDragEnd}
    onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "move"; }}
    onDrop={(event) => { event.preventDefault(); onDropOn(stem); }}
    onClick={() => onSelect(stem)}
    className={cn(
      "mb-0.5 flex cursor-pointer items-center gap-1 rounded-md border-l-[3px] py-1.5 pl-1.5 pr-1 transition-colors",
      selected ? "bg-primary/15" : "hover:bg-accent/60",
      dragging && "opacity-40",
    )}
    style={{ borderLeftColor: getStemColor(stem) }}
  >
    <GripVertical className="h-3.5 w-3.5 shrink-0 cursor-grab text-muted-foreground/60" aria-hidden="true" />
    <StemIcon className={cn("h-3.5 w-3.5 shrink-0", muted && "opacity-30")} style={{ color: getStemColor(stem) }} aria-hidden="true" />
    <span className={cn("min-w-0 flex-1 truncate text-left text-[13px]", muted && "text-muted-foreground line-through")}>{stem}</span>
    <button
      type="button"
      aria-pressed={muted}
      aria-label={`${muted ? "Enable" : "Mute"} ${stem}`}
      onClick={(event) => { event.stopPropagation(); onToggleMute(stem); }}
      className={cn(
        "flex h-5 w-5 shrink-0 items-center justify-center rounded-[5px] text-[10px] font-bold transition-colors",
        muted ? "bg-destructive text-destructive-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      M
    </button>
    <button
      type="button"
      aria-pressed={soloed}
      aria-label={`${soloed ? "Clear solo" : "Solo"} ${stem}`}
      onClick={(event) => { event.stopPropagation(); onToggleSolo(stem); }}
      className={cn(
        "flex h-5 w-5 shrink-0 items-center justify-center rounded-[5px] text-[10px] font-bold transition-colors",
        soloed ? "bg-warning text-warning-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      S
    </button>
  </div>;
});

const StemControls = React.memo(function StemControls({ stem, route, channels, enabled, gain, eq, onRoute, onGain, onEq, stemEqProfiles }: { stem: string; route: Record<string, number>; channels: string[]; enabled: boolean; gain: number; eq: string; onRoute: (patch: Record<string, number>) => void; onGain: (gain: number) => void; onEq: (eq: string) => void; stemEqProfiles?: string[] }) {
  const position = routePosition(route, channels);
  const setPosition = (patch: Partial<typeof position>) => onRoute(routeForPosition(channels, { ...position, ...patch }, route.LFE || 0));
  const StemIcon = getStemIcon(stem);
  const hasHeight = channels.includes("TFL") || channels.includes("TFR") || channels.includes("TBL") || channels.includes("TBR");
  return <div className="space-y-3"><p className="flex items-center gap-1.5 text-[13px] font-semibold"><StemIcon className="h-3.5 w-3.5 shrink-0" style={{ color: getStemColor(stem) }} /><span className="min-w-0 flex-1 truncate">{stem}</span><span className="text-[11px] font-normal text-muted-foreground">{enabled ? "enabled" : "muted"}</span></p><label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><ArrowLeftRight className="h-3 w-3" />Front <span className="ml-auto">Back</span></span><Slider aria-label="Front to back" className="mt-1.5" min={0} max={1} step={0.01} value={[position.depth]} onValueChange={([depth]) => setPosition({ depth })} /></label>{hasHeight && <label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><ArrowUpDown className="h-3 w-3" />Floor <span className="ml-auto">Height</span></span><Slider aria-label="Floor to height" className="mt-1.5" min={0} max={1} step={0.01} value={[position.height]} onValueChange={([height]) => setPosition({ height })} /></label>}<label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><SlidersHorizontal className="h-3 w-3" />Gain <span className="ml-auto tabular-nums">{gain.toFixed(1)} dB</span></span><Slider className="mt-1.5" min={-12} max={6} step={0.1} value={[gain]} onValueChange={([value]) => onGain(value)} /></label><label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><AudioWaveform className="h-3 w-3" />EQ</span><select className="mt-1.5 flex h-7 w-full rounded-md border bg-secondary px-2 text-[13px] text-foreground" value={eq} onChange={(event) => onEq(event.target.value)}><option value="">None</option>{(stemEqProfiles || ["vocal-presence", "vocal-warmth", "bass-warmth", "bass-cut", "drums-punch", "other-air"]).filter((name) => name !== "flat").map((name) => <option key={name} value={name}>{name}</option>)}</select></label></div>;
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
