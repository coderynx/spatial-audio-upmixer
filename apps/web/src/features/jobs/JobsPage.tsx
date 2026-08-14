import * as React from "react";
import { Activity, Archive, Box, Headphones, Package } from "lucide-react";
import type { Job } from "@/api";
import { EmptyState } from "@/app/EmptyState";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { InspectorGroup, InspectorRow } from "@/app/InspectorRow";
import { MetricStrip } from "@/app/MetricStrip";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Toolbar, ToolbarSpacer } from "@/app/Toolbar";
import { Workspace, WorkspaceScroll } from "@/app/Workspace";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatBytes, formatDate, formatDuration } from "@/lib/format";
import { jobArtifactSize } from "@/lib/projectStats";
import { cn } from "@/lib/utils";
import { JobActions } from "./JobActions";
import { JobTable } from "./JobTable";
import { jobDetails, statusLabel, statusVariant } from "./status";
import type { JobAction } from "./useJobs";

const ACTIVE_STATUSES = ["running", "queued", "pause_requested"];

export function JobsPage({
  jobs,
  loading,
  error,
  onAction,
  onRemix,
  onCreate,
}: {
  jobs: Job[];
  loading: boolean;
  error: string | null;
  onAction: (action: JobAction, job: Job) => void;
  onRemix: (job: Job) => void;
  onCreate: () => void;
}) {
  const [statusFilter, setStatusFilter] = React.useState<string | null>(null);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  useHeaderTitle(React.useMemo(() => <span className="text-[13px] font-semibold">Jobs</span>, []));

  const statusFacets = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const job of jobs) counts.set(job.status, (counts.get(job.status) || 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [jobs]);
  const visible = React.useMemo(
    () => (statusFilter ? jobs.filter((job) => job.status === statusFilter) : jobs),
    [jobs, statusFilter],
  );
  const selected = visible.find((job) => job.id === selectedId) || visible[0] || null;

  const running = jobs.filter((job) => ACTIVE_STATUSES.includes(job.status)).length;
  const complete = jobs.filter((job) => job.status === "completed").length;
  const outputs = jobs.reduce(
    (total, job) => total + job.artifacts.filter((artifact) => artifact.kind === "upmix").length,
    0,
  );
  const totalBytes = jobs.reduce((total, job) => total + jobArtifactSize(job), 0);

  const rail = (
    <WorkspaceScroll className="p-2">
      <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground">Status</p>
      <FacetRow group="Status" label="All" count={jobs.length} active={statusFilter === null} onClick={() => setStatusFilter(null)} />
      {statusFacets.map(([value, count]) => (
        <FacetRow
          key={value}
          group="Status"
          label={statusLabel(value)}
          count={count}
          active={statusFilter === value}
          onClick={() => setStatusFilter(statusFilter === value ? null : value)}
        />
      ))}
      <div className="mt-3">
        <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground">Queue</p>
        <MetricStrip
          orientation="vertical"
          metrics={[
            { label: "Active jobs", value: running, note: "Queued and processing", icon: Activity },
            { label: "Masters ready", value: complete, note: `${outputs} downloadable outputs`, icon: Headphones },
            { label: "Cache policy", value: "Shared", note: "Content-addressed stems", icon: Box },
          ]}
        />
      </div>
    </WorkspaceScroll>
  );

  const inspector = selected ? (
    <>
      <WorkspaceScroll>
        <InspectorGroup title="Job">
          <p className="mb-1.5 truncate text-[13px] font-semibold">{selected.name}</p>
          <InspectorRow label="Status" value={<Badge variant={statusVariant(selected.status)}>{statusLabel(selected.status)}</Badge>} />
          <InspectorRow label="Progress" value={`${Math.round(selected.progress * 100)}%`} />
          <InspectorRow label="Render" value={jobDetails(selected).layout} />
          <InspectorRow label="Mode" value={jobDetails(selected).mode} />
          <InspectorRow label="Created" value={formatDate(selected.created_at)} />
          <InspectorRow label="Started" value={formatDate(selected.started_at)} />
          <InspectorRow label="Finished" value={formatDate(selected.finished_at)} />
          <InspectorRow label="Artifacts" value={formatBytes(jobArtifactSize(selected))} />
        </InspectorGroup>
        {selected.error && (
          <InspectorGroup title="Error">
            <p className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-[11px] leading-relaxed text-destructive">
              {selected.error}
            </p>
          </InspectorGroup>
        )}
        <InspectorGroup title={`Tracks · ${selected.tracks.length}`}>
          {selected.tracks.length === 0 ? (
            <p className="text-xs text-muted-foreground">No per-track detail reported.</p>
          ) : (
            selected.tracks.map((track) => (
              <div key={track.id} className="border-b py-1.5 last:border-0">
                <p className="truncate text-xs font-medium">{track.asset.title || track.asset.filename}</p>
                <p className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">
                  {statusLabel(track.status)} · {Math.round(track.progress * 100)}% ·{" "}
                  {formatDuration(track.asset.duration_seconds)}
                </p>
              </div>
            ))
          )}
        </InspectorGroup>
        <InspectorGroup title="Outputs">
          {selected.artifacts.length === 0 ? (
            <p className="text-xs text-muted-foreground">Nothing rendered yet.</p>
          ) : (
            selected.artifacts.map((artifact) => (
              <a
                key={artifact.id}
                href={artifact.download_url}
                className="flex items-center justify-between gap-2 border-b py-1.5 text-[11px] text-primary last:border-0 hover:underline"
              >
                <span className="truncate">{artifact.filename}</span>
                <span className="shrink-0 tabular-nums text-muted-foreground">{formatBytes(artifact.size_bytes)}</span>
              </a>
            ))
          )}
        </InspectorGroup>
      </WorkspaceScroll>
      <div className="shrink-0 border-t p-2">
        <JobActions job={selected} onAction={onAction} onRemix={onRemix} />
      </div>
    </>
  ) : (
    <EmptyState icon={Package} title="No job selected" description="Pick a job to inspect its tracks, outputs, and timings." />
  );

  return (
    <Workspace
      toolbar={
        <Toolbar>
          <span className="text-[13px] font-medium">Processing queue</span>
          <span className="text-[11px] text-muted-foreground">Jobs persist until deleted.</span>
          <ToolbarSpacer />
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {visible.length} of {jobs.length}
          </span>
        </Toolbar>
      }
      rail={rail}
      inspector={inspector}
      status={
        <StatusBar>
          <StatusCell label="Total" value={jobs.length} />
          <StatusSeparator />
          <StatusCell label="Active" value={running} />
          <StatusSeparator />
          <StatusCell label="Complete" value={complete} />
          <StatusSeparator />
          <StatusCell label="Artifacts" value={formatBytes(totalBytes)} />
          <StatusSpacer />
          {error && <span className="truncate text-destructive">{error}</span>}
        </StatusBar>
      }
    >
      {loading ? (
        <div className="space-y-2 p-3">
          {[0, 1, 2, 3, 4].map((item) => (
            <div key={item} className="h-10 animate-pulse rounded-md border bg-muted/40" />
          ))}
        </div>
      ) : jobs.length === 0 ? (
        <EmptyState
          icon={Archive}
          title="No jobs"
          description="Create an upmix job from a track, album folder, or ZIP archive."
          action={
            <Button size="sm" onClick={onCreate}>
              Create job
            </Button>
          }
        />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={Archive}
          title="No jobs match this status"
          action={
            <Button variant="outline" size="sm" onClick={() => setStatusFilter(null)}>
              Clear filter
            </Button>
          }
        />
      ) : (
        <WorkspaceScroll>
          <JobTable
            jobs={visible}
            selectedId={selected?.id ?? null}
            onSelect={setSelectedId}
            onAction={onAction}
            onRemix={onRemix}
          />
        </WorkspaceScroll>
      )}
    </Workspace>
  );
}

function FacetRow({
  group,
  label,
  count,
  active,
  onClick,
}: {
  group: string;
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`${group}: ${label} (${count})`}
      onClick={onClick}
      className={cn(
        "mb-0.5 flex h-7 w-full items-center justify-between gap-2 rounded-md px-2 text-left text-[13px] capitalize transition-colors",
        active ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      <span className="truncate">{label}</span>
      <span className="shrink-0 text-[11px] tabular-nums">{count}</span>
    </button>
  );
}
