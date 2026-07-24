import * as React from "react";
import { Check, FileAudio, FolderOpen, Play, UploadCloud } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, type Configuration, type ImportPreview } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { defaultProjectManifest, fallbackStems, normalizeManifest, type Manifest } from "@/lib/manifest";

const childStemsByParent: Record<string, string[]> = {
  Vocals: ["Lead Vocals", "Backing Vocals"],
  Drums: ["Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash"],
};

function replaceStemFamily(stems: string[], parent: string, replacement: string[]) {
  const family = [parent, ...(childStemsByParent[parent] || [])];
  const first = stems.findIndex((stem) => family.includes(stem));
  const remaining = stems.filter((stem) => !family.includes(stem));
  const index = first < 0 ? remaining.length : first;
  return [...remaining.slice(0, index), ...replacement, ...remaining.slice(index)];
}

export function ProjectNewPage({ configuration }: { configuration: Configuration | null }) {
  const navigate = useNavigate();
  const [imported, setImported] = React.useState<ImportPreview | null>(null);
  const [name, setName] = React.useState("New spatial project");
  const [manifest, setManifest] = React.useState<Manifest>(normalizeManifest(defaultProjectManifest as unknown as Record<string, unknown>));
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const files = React.useRef<HTMLInputElement>(null);
  const availableStems = configuration?.choices.stems || fallbackStems;
  const toggleStem = (stem: string) => setManifest((current) => {
    const selected = current.engine.stems;
    const children = childStemsByParent[stem];
    if (children) {
      const family = [stem, ...children];
      const next = selected.includes(stem)
        ? selected.filter((item) => !family.includes(item))
        : replaceStemFamily(selected, stem, [stem]);
      return { ...current, engine: { ...current.engine, stems: next } };
    }
    const parent = Object.entries(childStemsByParent).find(([, values]) => values.includes(stem))?.[0];
    const next = selected.includes(stem)
      ? selected.filter((item) => item !== stem)
      : parent
        ? replaceStemFamily(selected, parent, [...selected.filter((item) => childStemsByParent[parent].includes(item)), stem])
        : [...selected, stem];
    return { ...current, engine: { ...current.engine, stems: next } };
  });
  const upload = async (list: FileList | null) => {
    if (!list?.length) return;
    setBusy(true); setError(null);
    try {
      const next = await api.upload(Array.from(list).map((file) => ({ file, path: file.webkitRelativePath || file.name })));
      setImported(next); setName(next.title ? `${next.title} spatial project` : "New spatial project");
    } catch (nextError) { setError((nextError as Error).message); } finally { setBusy(false); }
  };
  const create = async () => {
    if (!imported) return;
    setBusy(true); setError(null);
    try {
      const project = await api.createProject({ import_id: imported.id, name, manifest: { ...manifest, engine: { ...manifest.engine, mode: "stem" } } as unknown as Record<string, unknown>, scene: {} });
      navigate(`/projects/${project.id}`);
    } catch (nextError) { setError((nextError as Error).message); } finally { setBusy(false); }
  };
  const stemUnavailable = configuration?.capabilities.stem_separation.available === false;
  return <main className="mx-auto max-w-3xl p-3 sm:p-5"><h1 className="text-2xl font-semibold">New project</h1><p className="mt-1 text-sm text-muted-foreground">Upload audio once, then edit spatial mix settings without waiting for separation again.</p>
    <section className="mt-5 space-y-3 rounded-lg border p-4">{!imported ? <><div className="grid min-h-48 place-items-center rounded-md border border-dashed p-5 text-center"><div><UploadCloud className="mx-auto mb-3 h-8 w-8 text-muted-foreground" /><p className="font-medium">Upload tracks, an album folder, or a ZIP</p><p className="mt-1 text-sm text-muted-foreground">WAV and FLAC audio are supported.</p><Button className="mt-4" variant="outline" disabled={busy} onClick={() => files.current?.click()}><FileAudio />Choose audio</Button></div></div><input ref={files} className="hidden" type="file" multiple accept="audio/wav,audio/flac,.zip" onChange={(event) => { void upload(event.target.files); event.currentTarget.value = ""; }} /></> : <><div className="rounded-md bg-muted/30 p-3 text-sm"><FolderOpen className="mr-2 inline h-4 w-4" />{imported.assets.length} track{imported.assets.length === 1 ? "" : "s"} imported</div><div className="grid gap-4 sm:grid-cols-2"><div><Label htmlFor="project-name">Project name</Label><Input id="project-name" className="mt-2" value={name} onChange={(event) => setName(event.target.value)} /></div><div><Label htmlFor="layout">Output layout</Label><select id="layout" className="mt-2 flex h-10 w-full rounded-md border bg-background px-3 text-sm" value={manifest.mixing.channel_layout} onChange={(event) => setManifest({ ...manifest, mixing: { ...manifest.mixing, channel_layout: event.target.value } })}>{(configuration?.choices.channel_layouts || ["5.1", "7.1.4"]).map((layout) => <option key={layout}>{layout}</option>)}</select></div></div><div className="rounded-md border p-4"><div className="flex items-start justify-between gap-3"><div><p className="text-sm font-medium">Initial stem separation</p><p className="mt-1 text-xs text-muted-foreground">Choose parent or child stems. Crowd runs first, then feeds crowd-free audio into primary separation.</p></div><span className="shrink-0 rounded-full bg-muted px-2 py-1 text-xs">{manifest.engine.stems.length} selected</span></div><div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{availableStems.map((stem) => { const selected = manifest.engine.stems.includes(stem); const nested = Object.values(childStemsByParent).some((children) => children.includes(stem)); return <button key={stem} type="button" aria-pressed={selected} onClick={() => toggleStem(stem)} className={`flex items-center justify-between rounded-md border px-3 py-2 text-left text-sm ${nested ? "ml-3" : ""} ${selected ? "border-primary bg-primary/10" : "hover:bg-muted"}`}><span>{stem}</span>{selected && <Check className="h-4 w-4" />}</button>; })}</div></div><Button disabled={busy || stemUnavailable || manifest.engine.stems.length === 0} onClick={() => void create()}>{busy ? "Creating project" : <><Play />Create project</>}</Button></>}
      {stemUnavailable && <p className="text-sm text-destructive">{configuration?.capabilities.stem_separation.install_message}</p>}{error && <p className="text-sm text-destructive">{error}</p>}
    </section></main>;
}
