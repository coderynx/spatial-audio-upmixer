import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { Configuration, Project, ProjectTrack } from "@/api";
import { formatBytes, formatDuration } from "@/lib/format";
import { getStemColor, getStemIcon } from "@/lib/stems";

function statusVariant(status: string) {
  if (status === "ready") return "success" as const;
  if (status === "failed") return "destructive" as const;
  return "secondary" as const;
}

/** Project → Track → Stem (→ Zone, for a multichannel source) tree of every
 * prepared track, mirroring `StemsSection`'s parent/child expand rows. */
export function PreparedTrackTree({
  project,
  configuration,
  onOpenTrack,
}: {
  project: Project;
  configuration: Configuration | null;
  onOpenTrack: (trackId: string) => void;
}) {
  const [collapsed, setCollapsed] = React.useState<Record<string, boolean>>({});
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <header className="flex h-8 items-center border-b px-3">
        <span className="text-[11px] font-semibold uppercase tracking-[.08em] text-muted-foreground">
          Tracks · {project.tracks.length}
        </span>
      </header>
      <div className="divide-y">
        {project.tracks.map((track) => (
          <TrackRow
            key={track.id}
            track={track}
            configuration={configuration}
            open={!collapsed[track.id]}
            onToggle={() => setCollapsed((current) => ({ ...current, [track.id]: !current[track.id] }))}
            onOpenTrack={() => onOpenTrack(track.id)}
          />
        ))}
      </div>
    </div>
  );
}

function TrackRow({
  track,
  configuration,
  open,
  onToggle,
  onOpenTrack,
}: {
  track: ProjectTrack;
  configuration: Configuration | null;
  open: boolean;
  onToggle: () => void;
  onOpenTrack: () => void;
}) {
  const overrides = track.manifest_overrides as { mixing?: { channel_layout?: string } };
  const layout = overrides?.mixing?.channel_layout;
  const channelNames = layout ? configuration?.choices.layout_channels?.[layout] : undefined;
  const busy = track.status !== "ready" && track.status !== "failed";
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
          <p className="truncate text-[13px] font-medium">{track.asset.title || track.asset.filename}</p>
          <p className="truncate text-[11px] text-muted-foreground">
            {formatDuration(track.asset.duration_seconds)} · {track.asset.channels ?? "—"} ch ·{" "}
            {track.asset.sample_rate ? `${track.asset.sample_rate / 1000} kHz` : "—"} · {formatBytes(track.asset.size_bytes)}
            {layout ? ` · ${layout}` : ""}
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
        ) : (
          <Button size="sm" variant="outline" disabled={track.status !== "ready"} onClick={onOpenTrack}>
            Open in mixer
          </Button>
        )}
      </div>
      {track.error && (
        <p className="border-t bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive">{track.error}</p>
      )}
      {open && track.stems.length > 0 && (
        <div className="space-y-1 border-t bg-muted/10 py-1.5 pl-11 pr-3">
          {track.stems.map((stem) => (
            <StemRow
              key={stem.id}
              stemKey={stem.stem_key}
              channels={stem.channels}
              sampleRate={stem.sample_rate}
              channelNames={channelNames}
            />
          ))}
        </div>
      )}
      {open && track.stems.length === 0 && !busy && (
        <p className="border-t bg-muted/10 px-3 py-2 pl-11 text-[11px] text-muted-foreground">No stems catalogued.</p>
      )}
    </div>
  );
}

function StemRow({
  stemKey,
  channels,
  sampleRate,
  channelNames,
}: {
  stemKey: string;
  channels: number;
  sampleRate: number;
  channelNames?: string[];
}) {
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
