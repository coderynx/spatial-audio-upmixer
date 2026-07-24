import * as React from "react";
import {
  ArrowLeftRight,
  ArrowUpDown,
  AudioWaveform,
  ChevronLeft,
  Code2,
  Download,
  GripVertical,
  Package,
  RotateCcw,
  SlidersHorizontal,
  Wand2,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Configuration, type Project, type StemRouting } from "@/api";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AdvancedSection } from "@/features/composer/sections/AdvancedSection";
import { MasteringSection } from "@/features/composer/sections/MasteringSection";
import { normalizeManifest, type Manifest } from "@/lib/manifest";
import { getStemColor, getStemIcon, stemColors } from "@/lib/stems";
import { cn } from "@/lib/utils";
import { ProjectDeliverySection } from "./ProjectDeliverySection";
import { SpatialScene } from "./SpatialScene";
import { Transport } from "./Transport";
import { useStemPreview } from "./useStemPreview";

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
  const [activeTab, setActiveTab] = React.useState<"mixing" | "mastering" | "delivery">("mixing");
  const [manifestView, setManifestView] = React.useState(false);
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
  React.useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 2000); return () => window.clearInterval(timer); }, [load]);
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
  const updateManifest = (next: Manifest) => {
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
  };
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
  const previewStems = selected?.stems.filter((stem) => project?.prepared_stems.includes(stem.stem_key.split("@", 1)[0])) || [];
  const preview = useStemPreview(previewStems, {}, effectiveManifest?.mixing, selected?.source_preview_url || null, effectiveManifest?.mastering);
  const ready = Boolean(project?.prepared_stems.length);
  const channels = configuration?.choices.layout_channels?.[effectiveManifest?.mixing.channel_layout || "7.1.4"] || ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR"];
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
  const reorderStems = (source: string, target: string) => {
    if (source === target) return;
    const next = orderedStems.filter((stem) => stem !== source);
    const targetIndex = next.indexOf(target);
    if (targetIndex === -1) return;
    next.splice(targetIndex, 0, source);
    setStemOrder(next);
  };
  const routing: StemRouting = effectiveManifest?.mixing.stem_routing || {};
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
  const toggleEnabled = (stem: string) => {
    if (!effectiveManifest) return;
    const current = effectiveManifest.mixing.stem_enabled[stem] !== false;
    updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_enabled: { ...effectiveManifest.mixing.stem_enabled, [stem]: !current }, stem_solo: effectiveManifest.mixing.stem_solo.filter((solo) => solo !== stem) } });
  };
  const toggleSolo = (stem: string) => {
    if (!effectiveManifest) return;
    const solo = effectiveManifest.mixing.stem_solo;
    updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_solo: solo.includes(stem) ? solo.filter((item) => item !== stem) : [...solo, stem] } });
  };
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
  const headerTitle = React.useMemo(() => project ? <div className="flex min-w-0 items-center gap-2"><Link to="/projects" className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ChevronLeft className="h-3.5 w-3.5" />Projects</Link><span className="text-muted-foreground">/</span><span className="truncate text-sm font-semibold">{project.name}</span></div> : null, [project?.name]);
  useHeaderTitle(headerTitle);
  if (!project) return <main className="p-7">{error || "Loading project…"}</main>;
  if (!ready) return <main className="mx-auto max-w-3xl p-7"><h1 className="text-2xl font-semibold">{project.name}</h1><p className="mt-2 text-sm text-muted-foreground">{project.status_message}</p><Progress className="mt-5" value={project.progress * 100} />{["failed", "expansion_failed"].includes(project.status) && <Button className="mt-5" onClick={() => void retry()}><RotateCcw />Retry preparation</Button>}</main>;
  return <main className="mx-auto flex h-[calc(100vh-4rem)] w-full max-w-[1800px] flex-col overflow-hidden p-4 sm:p-6">
    {error && <p className="mb-3 flex-none rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    <div className="flex flex-none items-center justify-between gap-3">
      <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as typeof activeTab)}>
        <TabsList>
          <TabsTrigger value="mixing" className="gap-2"><SlidersHorizontal className="h-4 w-4" />Mixing</TabsTrigger>
          <TabsTrigger value="mastering" className="gap-2"><AudioWaveform className="h-4 w-4" />Mastering</TabsTrigger>
          <TabsTrigger value="delivery" className="gap-2"><Package className="h-4 w-4" />Delivery</TabsTrigger>
        </TabsList>
      </Tabs>
      <Button
        variant={manifestView ? "default" : "outline"}
        size="sm"
        onClick={() => {
          if (!manifestView && effectiveManifest) setRawManifest(JSON.stringify(effectiveManifest, null, 2));
          setManifestView((value) => !value);
        }}
      >
        <Code2 className="h-4 w-4" />
        {manifestView ? "Close manifest" : "Manifest JSON"}
      </Button>
    </div>
    {manifestView ? (
      <section className="mt-4 min-h-0 flex-1 overflow-auto rounded-lg border p-4">
        <AdvancedSection rawManifest={rawManifest} rawError={rawError} onChange={(value) => {
          setRawManifest(value);
          try {
            const next = normalizeManifest(JSON.parse(value) as Record<string, unknown>);
            setRawError(null);
            updateManifest(next);
          } catch (reason) { setRawError((reason as Error).message); }
        }} />
      </section>
    ) : <>
      {/* Preview + speaker routing graph stay visible on the Mixing tab. It
          needs a narrow side rail next to them (it was designed for 330px);
          Mastering/Delivery reuse wider composer-style panels instead. */}
      {activeTab === "mixing" && <div className="mt-4 grid min-h-0 flex-1 gap-4 xl:grid-cols-[230px_minmax(0,1fr)_330px]">
        <aside className="min-h-0 overflow-y-auto rounded-lg border p-3">
          <p className="mb-3 px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Tracks</p>
          {project.tracks.map((track) => <button key={track.id} onClick={() => setSelectedTrack(track.id)} className={`mb-1 w-full rounded-md px-3 py-2 text-left text-sm ${selectedTrack === track.id ? "bg-accent font-medium" : "hover:bg-muted"}`}>{track.asset.title || track.asset.filename}</button>)}
          <p className="mt-5 px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Stems</p>
          {orderedStems.map((stem) => <StemRow
            key={stem}
            stem={stem}
            selected={selectedStem === stem}
            muted={effectiveManifest?.mixing.stem_enabled[stem] === false}
            soloed={Boolean(effectiveManifest?.mixing.stem_solo.includes(stem))}
            dragging={draggedStem === stem}
            onSelect={() => setSelectedStem(stem)}
            onToggleMute={() => toggleEnabled(stem)}
            onToggleSolo={() => toggleSolo(stem)}
            onDragStart={() => setDraggedStem(stem)}
            onDragEnd={() => setDraggedStem(null)}
            onDropOn={() => { if (draggedStem) reorderStems(draggedStem, stem); setDraggedStem(null); }}
          />)}
        </aside>
        <section className="flex min-h-0 flex-col gap-3">
          <Transport
            playing={preview.playing}
            currentTime={preview.currentTime}
            duration={preview.duration}
            volume={preview.volume}
            loop={preview.loop}
            disabled={!preview.supported || !preview.ready || !previewStems.length}
            onPlayPause={() => void preview.playPause()}
            onStop={preview.stop}
            onToggleLoop={preview.toggleLoop}
            onSetVolume={preview.setVolume}
            onBeginScrub={preview.beginScrub}
            onScrubTo={preview.scrubTo}
            onCommitScrub={(value) => void preview.commitScrub(value)}
          />
          {preview.error && <p className="text-xs text-destructive">{preview.error}</p>}
          <SpatialScene channels={channels} routing={routing} selectedStem={selectedStem} colors={stemColors} onSelectStem={setSelectedStem} className="min-h-0 flex-1" />
        </section>
        <aside className="min-h-0 overflow-y-auto rounded-lg border p-4">{effectiveManifest && <><div className="flex items-center justify-between"><p className="text-sm font-semibold">Routing preset</p><select aria-label="Edit scope" className="h-8 rounded border bg-background px-1 text-xs" value={editScope} onChange={(event) => setEditScope(event.target.value as "project" | "track")}><option value="project">Project</option><option value="track" disabled={!selected}>Track</option></select></div><p className="mt-1 text-xs text-muted-foreground">{editScope === "project" ? "Default for every track" : `Override: ${selected?.asset.title || selected?.asset.filename}`}</p><select className="mt-2 flex h-9 w-full rounded-md border bg-background px-2 text-sm" value={preset} onChange={(event) => setPreset(event.target.value)}>{(configuration?.choices.stem_routing_presets || ["balanced", "intimate", "rhythmic", "spacious", "live", "detailed"]).map((name) => <option key={name}>{name}</option>)}</select><label className="mt-3 block text-xs text-muted-foreground">Intensity <span className="float-right">{presetIntensity.toFixed(2)}</span><Slider className="mt-2" min={0} max={1} step={0.01} value={[presetIntensity]} onValueChange={([value]) => setPresetIntensity(value)} /></label><Button className="mt-3 w-full" variant="outline" size="sm" onClick={() => void applyPreset()}><Wand2 className="h-4 w-4" />Apply preset</Button><div className="mt-5 border-t pt-4">{selectedStem ? <StemControls stem={selectedStem} route={routing[selectedStem] || {}} channels={channels} enabled={effectiveManifest.mixing.stem_enabled[selectedStem] !== false} gain={effectiveManifest.mixing.stem_rebalance[selectedStem] || 0} eq={effectiveManifest.mixing.stem_eq[selectedStem] || ""} onRoute={(patch) => updateRoute(selectedStem, patch)} onGain={(gain) => updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_rebalance: { ...effectiveManifest.mixing.stem_rebalance, [selectedStem]: gain } } })} onEq={(eq) => updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_eq: { ...effectiveManifest.mixing.stem_eq, [selectedStem]: eq } } })} /> : <p className="text-sm text-muted-foreground">Select stem to edit sends.</p>}</div><div className="mt-5 border-t pt-4"><div className="flex items-center justify-between text-sm"><span className="font-medium">Source anchor</span><span className="text-muted-foreground">{Math.round(effectiveManifest.mixing.stem_source_anchor_strength * 100)}%</span></div><Slider aria-label="Source anchor" className="mt-3" min={0} max={1} step={0.01} value={[effectiveManifest.mixing.stem_source_anchor_strength]} onValueChange={([stem_source_anchor_strength]) => updateManifest({ ...effectiveManifest, mixing: { ...effectiveManifest.mixing, stem_source_anchor_strength } })} /><p className="mt-2 text-xs text-muted-foreground">Blends original channel pairs back into the mix.</p></div></>}</aside>
      </div>}
      {activeTab === "mastering" && manifest && <section className="mt-4 min-h-0 flex-1 overflow-auto">
        <MasteringSection
          manifest={manifest}
          setManifest={(update) => updateProjectManifest(typeof update === "function" ? update(manifest) : update)}
          configuration={configuration}
          masteringReference={project.mastering_reference || null}
          referenceUploading={false}
          referenceError={null}
          onReferenceUpload={(file) => {
            void api.uploadMasteringReference(project.import_id, file)
              .then((reference) => saveReference(reference.id))
              .catch((reason) => setError((reason as Error).message));
          }}
          onReferenceClear={() => { void saveReference(null); }}
        />
      </section>}
      {activeTab === "delivery" && manifest && <section className="mt-4 min-h-0 flex-1 space-y-4 overflow-auto">
        <ProjectDeliverySection manifest={manifest} configuration={configuration} onChange={updateProjectManifest} />
        <Button disabled={exporting} onClick={() => void exportProject()}><Download />{exporting ? "Queueing" : "Export project"}</Button>
      </section>}
    </>}
  </main>;
}

function StemRow({
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
  onSelect: () => void;
  onToggleMute: () => void;
  onToggleSolo: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDropOn: () => void;
}) {
  const StemIcon = getStemIcon(stem);
  return <div
    draggable
    onDragStart={(event) => { event.dataTransfer.effectAllowed = "move"; onDragStart(); }}
    onDragEnd={onDragEnd}
    onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "move"; }}
    onDrop={(event) => { event.preventDefault(); onDropOn(); }}
    onClick={onSelect}
    className={cn(
      "mt-1 flex cursor-pointer items-center gap-1 rounded-md border-l-4 py-2 pl-1.5 pr-1 transition-colors",
      selected ? "bg-accent" : "hover:bg-muted/60",
      dragging && "opacity-40",
    )}
    style={{ borderLeftColor: getStemColor(stem) }}
  >
    <GripVertical className="h-3.5 w-3.5 shrink-0 cursor-grab text-muted-foreground/60" aria-hidden="true" />
    <StemIcon className={cn("h-4 w-4 shrink-0", muted && "opacity-30")} style={{ color: getStemColor(stem) }} aria-hidden="true" />
    <span className={cn("min-w-0 flex-1 truncate text-left text-sm", muted && "text-muted-foreground line-through")}>{stem}</span>
    <button
      type="button"
      aria-pressed={muted}
      aria-label={`${muted ? "Enable" : "Mute"} ${stem}`}
      onClick={(event) => { event.stopPropagation(); onToggleMute(); }}
      className={cn(
        "flex h-6 w-6 shrink-0 items-center justify-center rounded text-[11px] font-bold",
        muted ? "bg-red-500 text-white" : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      M
    </button>
    <button
      type="button"
      aria-pressed={soloed}
      aria-label={`${soloed ? "Clear solo" : "Solo"} ${stem}`}
      onClick={(event) => { event.stopPropagation(); onToggleSolo(); }}
      className={cn(
        "flex h-6 w-6 shrink-0 items-center justify-center rounded text-[11px] font-bold",
        soloed ? "bg-amber-400 text-black" : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      S
    </button>
  </div>;
}

function StemControls({ stem, route, channels, enabled, gain, eq, onRoute, onGain, onEq }: { stem: string; route: Record<string, number>; channels: string[]; enabled: boolean; gain: number; eq: string; onRoute: (patch: Record<string, number>) => void; onGain: (gain: number) => void; onEq: (eq: string) => void }) {
  const position = routePosition(route, channels);
  const setPosition = (patch: Partial<typeof position>) => onRoute(routeForPosition(channels, { ...position, ...patch }, route.LFE || 0));
  const StemIcon = getStemIcon(stem);
  return <div className="space-y-4"><p className="flex items-center gap-2 text-sm font-semibold"><StemIcon className="h-4 w-4 shrink-0" style={{ color: getStemColor(stem) }} /><span className="min-w-0 flex-1 truncate">{stem}</span><span className="text-xs font-normal text-muted-foreground">{enabled ? "enabled" : "muted"}</span></p><p className="text-xs text-muted-foreground">Position writes the same explicit speaker matrix used by export.</p><label className="block text-xs text-muted-foreground"><span className="flex items-center gap-1"><ArrowLeftRight className="h-3.5 w-3.5" />Front <span className="ml-auto">Back</span></span><Slider aria-label="Front to back" className="mt-2" min={0} max={1} step={0.01} value={[position.depth]} onValueChange={([depth]) => setPosition({ depth })} /></label><label className="block text-xs text-muted-foreground"><span className="flex items-center gap-1"><ArrowUpDown className="h-3.5 w-3.5" />Floor <span className="ml-auto">Height</span></span><Slider aria-label="Floor to height" className="mt-2" min={0} max={1} step={0.01} value={[position.height]} onValueChange={([height]) => setPosition({ height })} /></label><label className="block text-xs text-muted-foreground"><span className="flex items-center gap-1"><SlidersHorizontal className="h-3.5 w-3.5" />Gain <span className="ml-auto">{gain.toFixed(1)} dB</span></span><Slider className="mt-2" min={-12} max={6} step={0.1} value={[gain]} onValueChange={([value]) => onGain(value)} /></label><label className="block text-xs text-muted-foreground"><span className="flex items-center gap-1"><AudioWaveform className="h-3.5 w-3.5" />EQ</span><select className="mt-2 flex h-8 w-full rounded border bg-background px-2" value={eq} onChange={(event) => onEq(event.target.value)}><option value="">None</option><option value="vocal-presence">Vocal presence</option><option value="vocal-warmth">Vocal warmth</option><option value="bass-warmth">Bass warmth</option><option value="bass-cut">Bass cut</option><option value="drums-punch">Drums punch</option><option value="other-air">Other air</option></select></label></div>;
}

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
