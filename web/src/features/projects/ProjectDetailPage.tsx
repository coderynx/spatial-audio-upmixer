import * as React from "react";
import { ChevronLeft, Download, Layers3, Play, RotateCcw, Save, Sparkles, Square, Volume2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, type Configuration, type Project, type StemScene } from "@/api";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Slider } from "@/components/ui/slider";
import { normalizeManifest, type Manifest } from "@/lib/manifest";
import { SpatialScene } from "./SpatialScene";
import { useStemPreview } from "./useStemPreview";

type SaveState = "saved" | "saving" | "failed";

const childStemsByParent: Record<string, string[]> = {
  Vocals: ["Lead Vocals", "Backing Vocals"],
  Drums: ["Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash"],
};

const stemColors: Record<string, string> = {
  Vocals: "#f43f5e", Bass: "#14b8a6", Drums: "#f97316", Guitar: "#10b981", Piano: "#8b5cf6", Other: "#64748b",
  Kick: "#ef4444", Snare: "#ec4899", Toms: "#84cc16", "Hi-Hat": "#eab308", Ride: "#06b6d4", Crash: "#0ea5e9", Crowd: "#3b82f6", "Lead Vocals": "#f43f5e", "Backing Vocals": "#d946ef",
};

function hierarchicalStems(stems: string[]) {
  const selected = new Set(stems);
  return stems.filter((stem) => !(stem in childStemsByParent && childStemsByParent[stem].some((child) => selected.has(child))));
}

function timeLabel(seconds: number) {
  const safe = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

export function ProjectDetailPage({ configuration }: { configuration: Configuration | null }) {
  const { projectId } = useParams();
  const [project, setProject] = React.useState<Project | null>(null);
  const [manifest, setManifest] = React.useState<Manifest | null>(null);
  const [scene, setScene] = React.useState<{ stems?: StemScene }>({});
  const [selectedTrack, setSelectedTrack] = React.useState<string | null>(null);
  const [selectedStem, setSelectedStem] = React.useState<string | null>(null);
  const [saveState, setSaveState] = React.useState<SaveState>("saved");
  const [error, setError] = React.useState<string | null>(null);
  const [exporting, setExporting] = React.useState(false);
  const [stemToPrepare, setStemToPrepare] = React.useState("");
  const saveTimer = React.useRef<number | null>(null);
  const initialized = React.useRef(false);
  React.useEffect(() => { initialized.current = false; }, [projectId]);
  const load = React.useCallback(async () => {
    if (!projectId) return;
    try { const next = await api.getProject(projectId); setProject(next); if (!initialized.current) { initialized.current = true; setManifest(normalizeManifest(next.manifest)); setScene(next.scene); setSelectedTrack(next.tracks[0]?.id || null); } setError(null); }
    catch (nextError) { setError((nextError as Error).message); }
  }, [projectId]);
  React.useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 2000); return () => window.clearInterval(timer); }, [load]);
  React.useEffect(() => () => { if (saveTimer.current) window.clearTimeout(saveTimer.current); }, []);
  const queueSave = React.useCallback((nextManifest: Manifest, nextScene: { stems?: StemScene }) => {
    if (!projectId || !project) return;
    setSaveState("saving");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void api.saveProject(projectId, { manifest: nextManifest as unknown as Record<string, unknown>, scene: nextScene as Record<string, unknown> }).then((next) => { setProject(next); setSaveState("saved"); }).catch((nextError) => { setSaveState("failed"); setError((nextError as Error).message); });
    }, 500);
  }, [project, projectId]);
  const updateScene = (stem: string, patch: Record<string, unknown>) => {
    if (!manifest) return;
    const next = { stems: { ...scene.stems, [stem]: { ...scene.stems?.[stem], ...patch } } };
    setScene(next); queueSave(manifest, next);
  };
  const updateManifest = (next: Manifest) => {
    setManifest(next);
    queueSave(next, scene);
  };
  const selectStem = (stem: string | null) => {
    if (!stem) return;
    setSelectedStem(stem);
    if (!scene.stems?.[stem]) updateScene(stem, { azimuth_deg: 0, elevation_deg: 0 });
  };
  const selected = project?.tracks.find((track) => track.id === selectedTrack) || null;
  const previewStems = (selected?.stems || []).filter((stem) => (project?.prepared_stems || []).includes(stem.stem_key.split("@", 1)[0]));
  const preview = useStemPreview(previewStems, scene);
  const expand = async () => {
    if (!projectId || !stemToPrepare) return;
    try { setProject(await api.expandProjectStems(projectId, [stemToPrepare])); setStemToPrepare(""); } catch (nextError) { setError((nextError as Error).message); }
  };
  const retry = async () => { if (projectId) setProject(await api.retryProject(projectId)); };
  const exportProject = async () => { if (!projectId) return; setExporting(true); try { await api.exportProject(projectId); await load(); } catch (nextError) { setError((nextError as Error).message); } finally { setExporting(false); } };
  if (!project) return <main className="p-7">{error || "Loading project…"}</main>;
  const ready = project.prepared_stems.length > 0;
  const availableStems = (configuration?.choices.stems || []).filter((stem) => {
    if (project.requested_stems.includes(stem)) return false;
    if (stem in childStemsByParent) return !childStemsByParent[stem].some((child) => project.requested_stems.includes(child));
    return !Object.entries(childStemsByParent).some(([parent, children]) => children.includes(stem) && project.requested_stems.includes(parent));
  });
  const visibleStems = hierarchicalStems(project.prepared_stems);
  return <main className="mx-auto max-w-[1500px] p-4 sm:p-7"><div className="mb-5 flex flex-wrap items-center justify-between gap-3"><div><Link to="/projects" className="text-xs text-muted-foreground hover:text-foreground"><ChevronLeft className="inline h-3.5 w-3.5" />Projects</Link><h1 className="mt-1 text-2xl font-semibold">{project.name}</h1><p className="mt-1 text-sm text-muted-foreground">{project.status_message}</p></div><div className="flex items-center gap-2"><span className={saveState === "failed" ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>{saveState === "saving" ? "Saving…" : saveState === "failed" ? "Save failed" : "Saved"}</span>{ready && <Button disabled={exporting} onClick={() => void exportProject()}><Download />{exporting ? "Queueing" : "Export project"}</Button>}</div></div>
    {error && <p className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    {!ready ? <section className="rounded-lg border p-6"><h2 className="font-semibold">Preparing project stems</h2><p className="mt-1 text-sm text-muted-foreground">You’ll be able to edit as soon as every track has been separated.</p><Progress className="mt-5" value={project.progress * 100} /><div className="mt-5 space-y-2">{project.tracks.map((track) => <div key={track.id} className="flex items-center justify-between text-sm"><span>{track.asset.filename}</span><span className="text-muted-foreground">{track.status} · {Math.round(track.progress * 100)}%</span></div>)}</div>{["failed", "expansion_failed"].includes(project.status) && <Button className="mt-5" onClick={() => void retry()}><RotateCcw />Retry preparation</Button>}</section> : <div className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)_320px]">
    <aside className="rounded-lg border p-3"><p className="mb-3 px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Tracks</p>{project.tracks.map((track) => <button key={track.id} onClick={() => setSelectedTrack(track.id)} className={`mb-1 w-full rounded-md px-3 py-2 text-left text-sm ${selectedTrack === track.id ? "bg-accent font-medium" : "hover:bg-muted"}`}>{track.asset.title || track.asset.filename}</button>)}<div className="mt-5 border-t pt-4"><p className="mb-2 px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Prepared stems</p>{visibleStems.map((stem) => <button key={stem} type="button" onClick={() => selectStem(stem)} className={`mb-1 flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm ${selectedStem === stem ? "bg-accent font-medium" : "hover:bg-muted"}`}><span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: stemColors[stem] || "#94a3b8" }} /><span>{stem}</span></button>)}</div>{availableStems.length > 0 && <div className="mt-4 border-t pt-4"><label className="text-xs text-muted-foreground" htmlFor="stem-to-prepare">Prepare another stem</label><p className="mt-1 text-xs text-muted-foreground">Adds a new extraction target. Hidden when all available stems are already prepared.</p><select id="stem-to-prepare" className="mt-2 flex h-9 w-full rounded-md border bg-background px-2 text-sm" value={stemToPrepare} onChange={(event) => setStemToPrepare(event.target.value)}><option value="">Choose stem</option>{availableStems.map((stem) => <option key={stem} value={stem}>{stem}</option>)}</select><Button className="mt-2 w-full" variant="outline" size="sm" disabled={!stemToPrepare} onClick={() => void expand()}><Layers3 />Prepare selected stem</Button></div>}</aside>
      <section className="min-w-0"><SpatialScene stems={scene.stems || {}} colors={stemColors} selectedStem={selectedStem} onSelectStem={selectStem} onChange={(stem, azimuth_deg, elevation_deg) => updateScene(stem, { azimuth_deg, elevation_deg })} /><div className="mt-3 rounded-md border bg-muted/20 p-3"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-medium">Spatial headphone preview</p><p className="text-xs text-muted-foreground">Approximate binaural render — export project for final upmix and mastering.</p></div><div className="flex gap-2"><Button disabled={!preview.supported || !preview.ready || !previewStems.length} onClick={() => void preview.playPause()}><Play />{preview.playing ? "Pause" : preview.ready ? "Play" : "Loading preview"}</Button><Button variant="outline" aria-label="Stop preview" disabled={!preview.supported} onClick={preview.stop}><Square /></Button></div></div>{preview.error && <p className="mt-2 text-xs text-destructive">{preview.error}</p>}<div className="mt-3 grid gap-3 sm:grid-cols-[auto_minmax(0,1fr)_auto_auto]"><span className="font-mono text-xs tabular-nums text-muted-foreground">{timeLabel(preview.currentTime)}</span><input aria-label="Preview position" className="w-full accent-primary" type="range" min={0} max={Math.max(preview.duration, 0)} step={0.01} value={Math.min(preview.currentTime, preview.duration || 0)} disabled={!preview.duration} onPointerDown={preview.beginScrub} onPointerUp={(event) => void preview.commitScrub(Number(event.currentTarget.value))} onPointerCancel={(event) => void preview.commitScrub(Number(event.currentTarget.value))} onBlur={(event) => void preview.commitScrub(Number(event.currentTarget.value))} onKeyDown={preview.beginScrub} onKeyUp={(event) => void preview.commitScrub(Number(event.currentTarget.value))} onChange={(event) => preview.scrubTo(Number(event.target.value))} /><span className="font-mono text-xs tabular-nums text-muted-foreground">{timeLabel(preview.duration)}</span><label className="flex items-center gap-2"><Volume2 className="h-4 w-4 text-muted-foreground" /><input aria-label="Preview volume" className="w-20 accent-primary" type="range" min={0} max={1} step={0.01} value={preview.volume} onChange={(event) => preview.setVolume(Number(event.target.value))} /></label></div></div></section>
      <aside className="rounded-lg border p-4"><div className="mb-4 flex items-center gap-2"><Sparkles className="h-4 w-4" /><h2 className="font-semibold">Stem inspector</h2></div>{selectedStem && manifest ? <StemControls stem={selectedStem} value={scene.stems?.[selectedStem] || {}} gain={manifest.mixing.stem_rebalance[selectedStem] || 0} onChange={(patch) => updateScene(selectedStem, patch)} onGain={(gain) => updateManifest({ ...manifest, mixing: { ...manifest.mixing, stem_rebalance: { ...manifest.mixing.stem_rebalance, [selectedStem]: gain } } })} /> : <p className="text-sm text-muted-foreground">Select a stem in the 3D scene to edit its placement.</p>}<div className="mt-6 border-t pt-4"><p className="text-sm font-medium">Output</p><select className="mt-2 flex h-9 w-full rounded-md border bg-background px-2 text-sm" value={manifest?.mixing.channel_layout || "7.1.4"} onChange={(event) => { if (!manifest) return; updateManifest({ ...manifest, mixing: { ...manifest.mixing, channel_layout: event.target.value } }); }}>{(configuration?.choices.channel_layouts || []).map((layout) => <option key={layout}>{layout}</option>)}</select></div>{manifest && <div className="mt-4 border-t pt-4"><p className="text-sm font-medium">Mastering</p><label className="mt-3 block text-xs text-muted-foreground">Loudness target <span className="float-right">{manifest.mastering.loudness.target} LKFS</span><Slider className="mt-2" min={-30} max={-10} step={0.5} value={[manifest.mastering.loudness.target]} onValueChange={([target]) => updateManifest({ ...manifest, mastering: { ...manifest.mastering, loudness: { ...manifest.mastering.loudness, target } } })} /></label><p className="mt-3 text-xs text-muted-foreground">Loudness, true peak, and reference matching are applied on export.</p></div>}</aside>
    </div>}
    {project.exports.length > 0 && <section className="mt-6 rounded-lg border p-4"><h2 className="font-semibold">Export history</h2><div className="mt-3 space-y-2">{project.exports.map((job) => <div key={job.id} className="flex items-center justify-between text-sm"><span>{job.name}</span><span className="capitalize text-muted-foreground">{job.status}</span></div>)}</div></section>}
  </main>;
}

function StemControls({ stem, value, gain, onChange, onGain }: { stem: string; value: { enabled?: boolean; azimuth_deg?: number; elevation_deg?: number }; gain: number; onChange: (patch: Record<string, unknown>) => void; onGain: (gain: number) => void }) {
  return <div className="space-y-4"><div className="flex items-center justify-between"><span className="text-sm">{stem}</span><button className="text-xs text-primary" onClick={() => onChange({ enabled: value.enabled === false })}>{value.enabled === false ? "Enable" : "Mute"}</button></div><label className="block text-xs text-muted-foreground">Gain <span className="float-right">{gain.toFixed(1)} dB</span><Slider className="mt-2" min={-6} max={6} step={0.1} value={[gain]} onValueChange={([nextGain]) => onGain(nextGain)} /></label><label className="block text-xs text-muted-foreground">Azimuth <span className="float-right">{Math.round(value.azimuth_deg || 0)}°</span><Slider className="mt-2" min={-180} max={180} step={1} value={[value.azimuth_deg || 0]} onValueChange={([azimuth_deg]) => onChange({ azimuth_deg })} /></label><label className="block text-xs text-muted-foreground">Elevation <span className="float-right">{Math.round(value.elevation_deg || 0)}°</span><Slider className="mt-2" min={0} max={60} step={1} value={[value.elevation_deg || 0]} onValueChange={([elevation_deg]) => onChange({ elevation_deg })} /></label><Button className="w-full" variant="outline" size="sm" onClick={() => onChange({ azimuth_deg: 0, elevation_deg: 0 })}><Save />Center source</Button></div>;
}
