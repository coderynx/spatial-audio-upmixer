import * as React from "react";
import { ChevronDown, ChevronRight, Download, Plus, RotateCcw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { api } from "@/api";
import type { Configuration, Project, ProjectStem, ProjectTrack } from "@/api";
import { formatBytes, formatDuration } from "@/lib/format";
import { downloadWithProgress } from "@/lib/download";
import { getStemColor, getStemIcon } from "@/lib/stems";
import { normalizeManifest } from "@/lib/manifest";
import { deliveryTargetLabel } from "../deliveryTargets";
import { useRuntime } from "@/runtime";
import { ProjectTitle } from "../ProjectTitle";
import { ReprepareStemsDialog, type ReprepareSettings } from "./ReprepareStemsDialog";

import { DeleteDeliveryTargetDialog, DeliveryTargetDialog, type DeliveryTargetSettings } from "./DeliveryTargetDialog";

function statusVariant(status: string) {
  if (status === "ready") return "success" as const;
  if (status === "failed") return "destructive" as const;
  return "secondary" as const;
}

const IN_FLIGHT_STATUSES = new Set(["preparing", "expanding", "queued", "deleting"]);

/** Project → Track → Stem (→ Zone, for a multichannel source) tree of every
 * prepared track, mirroring `StemsSection`'s parent/child expand rows. */
export function PreparedTrackTree({
  project,
  configuration,
  onOpenTrack,
  onReprepare,
  onProjectUpdate,
  onRenameTrack,
}: {
  project: Project;
  configuration: Configuration | null;
  onOpenTrack: (trackId: string) => void;
  onReprepare: (settings: ReprepareSettings) => Promise<void>;
  onProjectUpdate: (project: Project) => void;
  onRenameTrack: (trackId: string, name: string) => void;
}) {
  const [collapsed, setCollapsed] = React.useState<Record<string, boolean>>({});
  const [reprepareOpen, setReprepareOpen] = React.useState(false);
  const canReprepare = !IN_FLIGHT_STATUSES.has(project.status);
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <header className="flex h-8 items-center gap-2 border-b px-3">
        <span className="text-[11px] font-semibold uppercase tracking-[.08em] text-muted-foreground">
          Tracks · {project.tracks.length}
        </span>
        <div className="min-w-0 flex-1" />
        {canReprepare && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setReprepareOpen(true)}
            title="Re-run stem separation for every track — e.g. after a separation model update leaves prepared stems stale"
          >
            <RotateCcw />
            Re-prepare stems
          </Button>
        )}
      </header>
      <div className="divide-y">
        {project.tracks.map((track) => (
          <TrackRow
            key={track.id}
            projectId={project.id}
            track={track}
            configuration={configuration}
            open={!collapsed[track.id]}
            onToggle={() => setCollapsed((current) => ({ ...current, [track.id]: !current[track.id] }))}
            onOpenTrack={() => onOpenTrack(track.id)}
            onProjectUpdate={onProjectUpdate}
            onRename={(name) => onRenameTrack(track.id, name)}
          />
        ))}
      </div>
      <ReprepareStemsDialog
        open={reprepareOpen}
        project={project}
        configuration={configuration}
        onOpenChange={setReprepareOpen}
        onReprepare={onReprepare}
      />
    </div>
  );
}

function TrackRow({
  projectId,
  track,
  configuration,
  open,
  onToggle,
  onOpenTrack,
  onProjectUpdate,
  onRename,
}: {
  projectId: string;
  track: ProjectTrack;
  configuration: Configuration | null;
  open: boolean;
  onToggle: () => void;
  onOpenTrack: () => void;
  onProjectUpdate: (project: Project) => void;
  onRename: (name: string) => void;
}) {
  const runtime = useRuntime();
  // Stem zones are read against the track's first layout — the stems
  // themselves are layout-independent, this only names their channels.
  const channelNames = configuration?.choices.layout_channels?.[track.layouts[0]];
  const busy = track.status !== "ready" && track.status !== "failed";
  const [downloadProgress, setDownloadProgress] = React.useState<number | null>(null);
  const [downloadError, setDownloadError] = React.useState<string | null>(null);

  async function downloadAllStems() {
    setDownloadError(null);
    setDownloadProgress(0);
    const totalBytes = track.stems.reduce((sum, stem) => sum + stem.size_bytes, 0);
    try {
      await downloadWithProgress(api.trackStemsArchiveUrl(projectId, track.id), totalBytes, setDownloadProgress);
    } catch (reason) {
      setDownloadError((reason as Error).message);
    } finally {
      setDownloadProgress(null);
    }
  }

  return (
    <div>
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          aria-label={open ? `Collapse ${track.asset.filename}` : `Expand ${track.asset.filename}`}
          aria-expanded={open}
          className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={onToggle}
        >
          {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </button>
        <div className="min-w-0 flex-1">
          <ProjectTitle name={track.name || track.asset.title || track.asset.filename} entity="track" isTauri={runtime.isTauri} onRename={onRename} />
          <p className="truncate text-[11px] text-muted-foreground">
            {formatDuration(track.asset.duration_seconds)} · {track.asset.channels ?? "—"} ch ·{" "}
            {track.asset.sample_rate ? `${track.asset.sample_rate / 1000} kHz` : "—"} · {formatBytes(track.asset.size_bytes)}
          </p>
        </div>
        <Badge variant={statusVariant(track.status)} className="shrink-0 capitalize">
          {track.status}
        </Badge>
        {busy ? (
          <div className="flex w-24 shrink-0 items-center gap-1.5">
            <Progress value={track.progress * 100} />
            <span className="w-8 shrink-0 text-right text-[11px] tabular-nums">{Math.round(track.progress * 100)}%</span>
          </div>
        ) : downloadProgress !== null ? (
          <div className="flex w-24 shrink-0 items-center gap-1.5">
            <Progress value={downloadProgress * 100} />
            <span className="w-8 shrink-0 text-right text-[11px] tabular-nums">{Math.round(downloadProgress * 100)}%</span>
          </div>
        ) : (
          <>
            {track.stems.length > 0 && (
              <Button
                size="sm"
                variant="outline"
                onClick={downloadAllStems}
                aria-label={`Download all stems for ${track.asset.title || track.asset.filename}`}
              >
                <Download />
                Download stems
              </Button>
            )}
            <Button size="sm" variant="outline" disabled={track.status !== "ready"} onClick={onOpenTrack}>
              Open in mixer
            </Button>
          </>
        )}
      </div>
      <TrackDeliveryTargets
        projectId={projectId}
        track={track}
        configuration={configuration}
        onProjectUpdate={onProjectUpdate}
      />
      {track.error && (
        <p className="border-t bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive">{track.error}</p>
      )}
      {downloadError && (
        <p className="border-t bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive">{downloadError}</p>
      )}
      {open && track.stems.length > 0 && (
        <div className="space-y-1 border-t bg-muted/10 py-1.5 pl-11 pr-3">
          {track.stems.map((stem) => (
            <StemRow key={stem.id} stem={stem} channelNames={channelNames} />
          ))}
        </div>
      )}
      {open && track.stems.length === 0 && !busy && (
        <p className="border-t bg-muted/10 px-3 py-2 pl-11 text-[11px] text-muted-foreground">No stems catalogued.</p>
      )}
    </div>
  );
}

function TrackDeliveryTargets({
  projectId,
  track,
  configuration,
  onProjectUpdate,
}: {
  projectId: string;
  track: ProjectTrack;
  configuration: Configuration | null;
  onProjectUpdate: (project: Project) => void;
}) {
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [deleteLayout, setDeleteLayout] = React.useState<string | null>(null);

  async function create(settings: DeliveryTargetSettings) {
    setSaving(true);
    setError(null);
    try {
      await api.setTrackLayouts(projectId, track.id, [...track.layouts, settings.mixing.channel_layout]);
      onProjectUpdate(await api.saveProjectTrackLayout(projectId, track.id, settings.mixing.channel_layout, {
        manifest_overrides: settings,
        scene_overrides: track.scene_overrides,
      }));
    } catch (reason) {
      setError((reason as Error).message);
      throw reason;
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!deleteLayout) return;
    setSaving(true);
    setError(null);
    try {
      onProjectUpdate(await api.setTrackLayouts(projectId, track.id, track.layouts.filter((layout) => layout !== deleteLayout)));
    } catch (reason) {
      setError((reason as Error).message);
      throw reason;
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-t bg-muted/10 px-3 py-2 pl-11">
      <span className="mr-1 text-[11px] text-muted-foreground">Delivery targets</span>
      {track.layouts.map((layout) => <span key={layout} className="group inline-flex items-center rounded-md border py-1 pl-2 text-[11px]">{deliveryTargetLabel(track, layout)}
        {track.layouts.length > 1 && <button type="button" aria-label={`Delete ${deliveryTargetLabel(track, layout)}`} className="ml-1 grid h-4 w-4 place-items-center rounded text-muted-foreground opacity-0 hover:bg-destructive/10 hover:text-destructive focus:opacity-100 group-hover:opacity-100" onClick={() => setDeleteLayout(layout)}><X className="h-3 w-3" /></button>}
      </span>)}
      <Button variant="outline" size="sm" disabled={saving} onClick={() => setCreateOpen(true)}><Plus />Create delivery target</Button>
      {error && <span className="text-[11px] text-destructive">{error}</span>}
      <DeliveryTargetDialog open={createOpen} configuration={configuration} initial={normalizeManifest(track.layout_overrides[track.layouts[0]] || {})} onOpenChange={setCreateOpen} onCreate={create} />
      <DeleteDeliveryTargetDialog open={deleteLayout !== null} target={deleteLayout ? deliveryTargetLabel(track, deleteLayout) : null} onOpenChange={(open) => { if (!open) setDeleteLayout(null); }} onConfirm={remove} />
    </div>
  );
}

function StemRow({ stem, channelNames }: { stem: ProjectStem; channelNames?: string[] }) {
  const { stem_key: stemKey, channels, sample_rate: sampleRate } = stem;
  const baseName = stemKey.split("@", 1)[0];
  const StemIcon = getStemIcon(baseName);
  const color = getStemColor(baseName);
  // A stem that kept more than a stereo pair still carries the source's own
  // speaker positions — surface them as read-only "zone" chips, the leaf
  // level the brief asks for under a multichannel source's stems.
  const zones =
    channels > 2
      ? channelNames?.slice(0, channels) || Array.from({ length: channels }, (_, index) => `Channel ${index + 1}`)
      : [];
  return (
    <div className="py-0.5">
      <div className="flex items-center gap-2 py-1">
        <StemIcon className="h-3.5 w-3.5 shrink-0" style={{ color }} aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-[12px]">{stemKey}</span>
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
          {channels} ch · {sampleRate / 1000} kHz
        </span>
        {stem.audio_url && (
          <Button variant="ghost" size="icon" className="h-5 w-5 shrink-0" asChild>
            <a
              href={stem.audio_url}
              download={`${stemKey.replace(/[\\/]/g, "_")}.wav`}
              aria-label={`Download ${stemKey}`}
              title={`Download ${stemKey}`}
            >
              <Download className="h-3 w-3" />
            </a>
          </Button>
        )}
      </div>
      {zones.length > 0 && (
        <div className="ml-5 flex flex-wrap gap-1 pb-1">
          {zones.map((zone) => (
            <span key={zone} className="rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {zone}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
