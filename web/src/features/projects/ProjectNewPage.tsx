import * as React from "react";
import { Check, FileAudio, FolderOpen, Layers3, Play, UploadCloud } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, type Configuration, type ImportPreview } from "@/api";
import { EmptyState } from "@/app/EmptyState";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { InspectorGroup, InspectorRow } from "@/app/InspectorRow";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Toolbar, ToolbarSpacer } from "@/app/Toolbar";
import { Workspace, WorkspaceScroll } from "@/app/Workspace";
import { SelectField } from "@/components/forms/fields";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatBytes, formatDuration } from "@/lib/format";
import { defaultProjectManifest, fallbackStems, normalizeManifest, type Manifest } from "@/lib/manifest";
import { getStemColor, getStemIcon } from "@/lib/stems";
import { cn } from "@/lib/utils";

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
  useHeaderTitle(React.useMemo(() => <span className="text-[13px] font-semibold">New project</span>, []));
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
  const totalBytes = imported?.assets.reduce((total, asset) => total + asset.size_bytes, 0) || 0;

  const picker = (
    <>
      <Button className="w-full" variant="outline" size="sm" disabled={busy} onClick={() => files.current?.click()}>
        <FileAudio />
        {imported ? "Replace audio" : "Choose audio"}
      </Button>
      <input
        ref={files}
        className="hidden"
        type="file"
        multiple
        accept="audio/wav,audio/flac,.zip"
        onChange={(event) => { void upload(event.target.files); event.currentTarget.value = ""; }}
      />
    </>
  );

  const rail = (
    <>
      <WorkspaceScroll>
        <InspectorGroup title="Source">
          {imported ? (
            <>
              <p className="mb-2 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <FolderOpen className="h-3.5 w-3.5 shrink-0" />
                {imported.assets.length} track{imported.assets.length === 1 ? "" : "s"} · {formatBytes(totalBytes)}
              </p>
              {picker}
            </>
          ) : (
            <>
              <p className="mb-2 text-[11px] leading-relaxed text-muted-foreground">
                Upload tracks, an album folder, or a ZIP. WAV and FLAC are supported.
              </p>
              {picker}
            </>
          )}
        </InspectorGroup>
        {imported && (
          <InspectorGroup title={`Tracks · ${imported.assets.length}`}>
            {imported.assets.map((asset) => (
              <div key={asset.id} className="border-b py-1.5 last:border-0">
                <p className="truncate text-xs font-medium">{asset.title || asset.filename}</p>
                <p className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">
                  {formatDuration(asset.duration_seconds)} · {asset.channels ?? "—"} ch ·{" "}
                  {asset.sample_rate ? `${asset.sample_rate / 1000} kHz` : "—"} · {formatBytes(asset.size_bytes)}
                </p>
              </div>
            ))}
          </InspectorGroup>
        )}
      </WorkspaceScroll>
    </>
  );

  const inspector = (
    <>
      <WorkspaceScroll>
        <InspectorGroup title="Project">
          <div className="space-y-1.5">
            <Label htmlFor="project-name">Project name</Label>
            <Input id="project-name" value={name} disabled={!imported} onChange={(event) => setName(event.target.value)} />
          </div>
          <div className="mt-3">
            <SelectField
              label="Output layout"
              value={manifest.mixing.channel_layout}
              disabled={!imported}
              onChange={(channel_layout) => setManifest({ ...manifest, mixing: { ...manifest.mixing, channel_layout } })}
              options={(configuration?.choices.channel_layouts || ["5.1", "7.1.4"]).map((value) => ({ value, label: value }))}
            />
          </div>
        </InspectorGroup>
        <InspectorGroup title="Separation">
          <InspectorRow label="Stems selected" value={manifest.engine.stems.length} />
          <InspectorRow label="Engine mode" value="stem" />
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            Crowd runs first, then feeds crowd-free audio into primary separation.
          </p>
        </InspectorGroup>
        {stemUnavailable && (
          <InspectorGroup title="Processing node">
            <p className="rounded-md border border-warning/30 bg-warning/10 p-2 text-[11px] leading-relaxed text-warning">
              {configuration?.capabilities.stem_separation.install_message}
            </p>
          </InspectorGroup>
        )}
      </WorkspaceScroll>
      <div className="shrink-0 border-t p-2">
        <Button
          className="w-full"
          disabled={busy || !imported || stemUnavailable || manifest.engine.stems.length === 0}
          onClick={() => void create()}
        >
          {busy ? "Creating project" : <><Play />Create project</>}
        </Button>
      </div>
    </>
  );

  return (
    <Workspace
      toolbar={
        <Toolbar>
          <span className="text-[13px] font-medium">Initial stem separation</span>
          <span className="text-[11px] text-muted-foreground">Pick parent or child stems.</span>
          <ToolbarSpacer />
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {manifest.engine.stems.length} selected
          </span>
        </Toolbar>
      }
      rail={rail}
      inspector={inspector}
      status={
        <StatusBar>
          <StatusCell label="Tracks" value={imported?.assets.length ?? 0} />
          <StatusSeparator />
          <StatusCell label="Upload" value={formatBytes(totalBytes)} />
          <StatusSeparator />
          <StatusCell label="Stems" value={manifest.engine.stems.length} />
          <StatusSpacer />
          {error && <span className="truncate text-destructive">{error}</span>}
        </StatusBar>
      }
    >
      {!imported ? (
        <EmptyState
          icon={UploadCloud}
          title={busy ? "Uploading…" : "Upload tracks, an album folder, or a ZIP"}
          description="WAV and FLAC audio are supported. Separation runs once; the mix stays editable afterwards."
          action={
            <Button size="sm" variant="outline" disabled={busy} onClick={() => files.current?.click()}>
              <FileAudio />
              Choose audio
            </Button>
          }
        />
      ) : (
        <WorkspaceScroll className="grid auto-rows-min grid-cols-2 gap-1.5 p-3 sm:grid-cols-3 2xl:grid-cols-4">
          {availableStems.map((stem) => (
            <StemPad
              key={stem}
              stem={stem}
              selected={manifest.engine.stems.includes(stem)}
              nested={Object.values(childStemsByParent).some((children) => children.includes(stem))}
              onToggle={toggleStem}
            />
          ))}
        </WorkspaceScroll>
      )}
    </Workspace>
  );
}

function StemPad({
  stem,
  selected,
  nested,
  onToggle,
}: {
  stem: string;
  selected: boolean;
  nested: boolean;
  onToggle: (stem: string) => void;
}) {
  const StemIcon = getStemIcon(stem);
  const color = getStemColor(stem);
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={() => onToggle(stem)}
      className={cn(
        "flex h-16 flex-col justify-between rounded-lg border p-2 text-left transition-colors",
        nested && "h-14",
        selected ? "border-transparent" : "bg-card hover:bg-accent/50",
      )}
      style={selected ? { backgroundColor: `${color}26`, borderColor: color } : undefined}
    >
      <span className="flex w-full items-center justify-between gap-1">
        <StemIcon className="h-4 w-4 shrink-0" style={{ color }} aria-hidden="true" />
        {selected ? <Check className="h-3.5 w-3.5 shrink-0" style={{ color }} /> : <Layers3 className="h-3.5 w-3.5 shrink-0 text-muted-foreground/40" />}
      </span>
      <span className={cn("truncate text-[13px]", selected ? "font-semibold" : "text-muted-foreground")}>{stem}</span>
    </button>
  );
}
