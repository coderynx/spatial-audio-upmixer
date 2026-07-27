import * as React from "react";
import {
  FolderPlus,
  LayoutGrid,
  List,
  Music2,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { Link } from "react-router-dom";
import type { Project } from "@/api";
import { EmptyState } from "@/app/EmptyState";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { InspectorGroup, InspectorRow } from "@/app/InspectorRow";
import { SegmentedControl } from "@/app/SegmentedControl";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Toolbar, ToolbarSpacer } from "@/app/Toolbar";
import { Workspace, WorkspaceScroll } from "@/app/Workspace";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { formatBytes, formatDate, formatDuration } from "@/lib/format";
import { projectSize } from "@/lib/projectStats";
import { getStemColor } from "@/lib/stems";
import { cn } from "@/lib/utils";

type View = "grid" | "list";

const VIEWS = [
  { value: "grid" as const, label: "Grid", icon: LayoutGrid },
  { value: "list" as const, label: "List", icon: List },
];

function statusVariant(status: string) {
  if (status === "ready") return "success" as const;
  if (status.includes("failed")) return "destructive" as const;
  return "secondary" as const;
}

function statusLabel(status: string) {
  return status.replace(/_/g, " ");
}

export function ProjectsPage({
  projects,
  loading,
  error,
  onDelete,
}: {
  projects: Project[];
  loading: boolean;
  error: string | null;
  onDelete: (project: Project) => void;
}) {
  const [view, setView] = React.useState<View>("grid");
  const [statusFilter, setStatusFilter] = React.useState<string | null>(null);
  const [layoutFilter, setLayoutFilter] = React.useState<string | null>(null);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  useHeaderTitle(React.useMemo(() => <span className="text-[13px] font-semibold">Projects</span>, []));

  const statusFacets = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const project of projects) counts.set(project.status, (counts.get(project.status) || 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [projects]);
  const layoutFacets = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const project of projects) {
      const layout = projectLayout(project);
      counts.set(layout, (counts.get(layout) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [projects]);

  const visible = React.useMemo(
    () =>
      projects.filter(
        (project) =>
          (!statusFilter || project.status === statusFilter) &&
          (!layoutFilter || projectLayout(project) === layoutFilter),
      ),
    [projects, statusFilter, layoutFilter],
  );
  const selected = visible.find((project) => project.id === selectedId) || visible[0] || null;
  const totalBytes = React.useMemo(
    () => projects.reduce((total, project) => total + projectSize(project).total, 0),
    [projects],
  );

  const rail = (
    <WorkspaceScroll className="p-2">
      <FacetGroup
        title="Status"
        facets={statusFacets}
        active={statusFilter}
        onSelect={setStatusFilter}
        total={projects.length}
        format={statusLabel}
      />
      <FacetGroup
        title="Layout"
        facets={layoutFacets}
        active={layoutFilter}
        onSelect={setLayoutFilter}
        total={projects.length}
      />
    </WorkspaceScroll>
  );

  const inspector = selected ? (
    <>
      <WorkspaceScroll>
        <InspectorGroup title="Project">
          <p className="mb-1.5 truncate text-[13px] font-semibold">{selected.name}</p>
          <InspectorRow label="Status" value={<Badge variant={statusVariant(selected.status)}>{statusLabel(selected.status)}</Badge>} />
          <InspectorRow label="Progress" value={`${Math.round(selected.progress * 100)}%`} />
          <InspectorRow label="Speaker layout" value={projectLayout(selected)} />
          <InspectorRow label="Preview quality" value={selected.preview_quality} />
          <InspectorRow label="Revision" value={selected.revision} />
          <InspectorRow label="Created" value={formatDate(selected.created_at)} />
          <InspectorRow label="Updated" value={formatDate(selected.updated_at)} />
        </InspectorGroup>
        <InspectorGroup title={`Tracks · ${selected.tracks.length}`}>
          {selected.tracks.length === 0 ? (
            <p className="text-xs text-muted-foreground">No tracks imported.</p>
          ) : (
            selected.tracks.map((track) => (
              <div key={track.id} className="border-b py-1.5 last:border-0">
                <p className="truncate text-xs font-medium">{track.asset.title || track.asset.filename}</p>
                <p className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">
                  {formatDuration(track.asset.duration_seconds)} · {track.asset.channels ?? "—"} ch ·{" "}
                  {track.asset.sample_rate ? `${track.asset.sample_rate / 1000} kHz` : "—"} ·{" "}
                  {formatBytes(track.asset.size_bytes)}
                </p>
              </div>
            ))
          )}
        </InspectorGroup>
        <InspectorGroup title="Stems">
          <div className="flex flex-wrap gap-1">
            {(selected.prepared_stems.length ? selected.prepared_stems : selected.requested_stems).map((stem) => (
              <StemChip key={stem} stem={stem} pending={!selected.prepared_stems.includes(stem)} />
            ))}
          </div>
        </InspectorGroup>
        <InspectorGroup title="Storage">
          <StorageRows project={selected} />
        </InspectorGroup>
        <InspectorGroup title={`Exports · ${selected.exports.length}`}>
          {selected.exports.length === 0 ? (
            <p className="text-xs text-muted-foreground">No exports queued yet.</p>
          ) : (
            selected.exports.map((job) => (
              <div key={job.id} className="border-b py-1.5 last:border-0">
                <p className="truncate text-xs font-medium">{job.name}</p>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  {statusLabel(job.status)} · {formatDate(job.updated_at)}
                </p>
                {job.artifacts.map((artifact) => (
                  <a
                    key={artifact.id}
                    href={artifact.download_url}
                    className="mt-1 flex items-center justify-between gap-2 text-[11px] text-primary hover:underline"
                  >
                    <span className="truncate">{artifact.filename}</span>
                    <span className="shrink-0 tabular-nums text-muted-foreground">{formatBytes(artifact.size_bytes)}</span>
                  </a>
                ))}
              </div>
            ))
          )}
        </InspectorGroup>
      </WorkspaceScroll>
      <div className="flex shrink-0 items-center gap-2 border-t p-2">
        <Button className="flex-1" asChild>
          <Link to={`/projects/${selected.id}`}>
            <SlidersHorizontal />
            Open mixer
          </Link>
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Delete ${selected.name}`}
          onClick={() => onDelete(selected)}
        >
          <Trash2 />
        </Button>
      </div>
    </>
  ) : (
    <EmptyState icon={Music2} title="No project selected" description="Pick a project to inspect its tracks, stems, and exports." />
  );

  return (
    <Workspace
      toolbar={
        <Toolbar>
          <SegmentedControl segments={VIEWS} value={view} onChange={setView} aria-label="Project view" />
          <ToolbarSpacer />
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {visible.length} of {projects.length}
          </span>
        </Toolbar>
      }
      rail={rail}
      inspector={inspector}
      status={
        <StatusBar>
          <StatusCell label="Projects" value={projects.length} />
          <StatusSeparator />
          <StatusCell label="Tracks" value={projects.reduce((total, project) => total + project.tracks.length, 0)} />
          <StatusSeparator />
          <StatusCell label="On disk" value={formatBytes(totalBytes)} />
          <StatusSpacer />
          {error && <span className="truncate text-destructive">{error}</span>}
        </StatusBar>
      }
    >
      {loading ? (
        <div className="grid flex-1 gap-2 overflow-hidden p-3 sm:grid-cols-2 2xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((item) => (
            <div key={item} className="h-28 animate-pulse rounded-lg border bg-muted/40" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={FolderPlus}
          title={projects.length ? "No projects match these filters" : "Create your first project"}
          description={
            projects.length
              ? "Clear the status or layout filter to see the rest."
              : "Import tracks once, separate stems in the background, then keep shaping the mix."
          }
          action={
            projects.length ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setStatusFilter(null);
                  setLayoutFilter(null);
                }}
              >
                Clear filters
              </Button>
            ) : (
              <Button size="sm" asChild>
                <Link to="/projects/new">New project</Link>
              </Button>
            )
          }
        />
      ) : view === "grid" ? (
        <WorkspaceScroll className="grid auto-rows-min grid-cols-1 gap-2 p-3 sm:grid-cols-2 2xl:grid-cols-3">
          {visible.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              selected={project.id === selected?.id}
              onSelect={setSelectedId}
            />
          ))}
        </WorkspaceScroll>
      ) : (
        <WorkspaceScroll>
          <table className="w-full text-left text-[13px]">
            <thead className="sticky top-0 z-10 border-b bg-card text-[11px] font-semibold uppercase tracking-[.06em] text-muted-foreground">
              <tr>
                <th className="px-3 py-1.5 font-semibold">Project</th>
                <th className="px-3 py-1.5 font-semibold">Layout</th>
                <th className="px-3 py-1.5 font-semibold">Stems</th>
                <th className="min-w-40 px-3 py-1.5 font-semibold">Progress</th>
                <th className="px-3 py-1.5 font-semibold">Size</th>
                <th className="px-3 py-1.5 font-semibold">Updated</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((project) => (
                <tr
                  key={project.id}
                  onClick={() => setSelectedId(project.id)}
                  className={cn(
                    "cursor-pointer border-b last:border-0",
                    project.id === selected?.id ? "bg-primary/10" : "hover:bg-accent/50",
                  )}
                >
                  <td className="max-w-xs px-3 py-1.5">
                    <Link to={`/projects/${project.id}`} className="block truncate font-medium hover:underline">
                      {project.name}
                    </Link>
                    <p className="truncate text-[11px] text-muted-foreground">{project.status_message}</p>
                  </td>
                  <td className="px-3 py-1.5 tabular-nums">{projectLayout(project)}</td>
                  <td className="px-3 py-1.5 tabular-nums">
                    {project.prepared_stems.length || project.requested_stems.length}
                  </td>
                  <td className="px-3 py-1.5">
                    <div className="flex items-center gap-2">
                      <Progress value={project.progress * 100} />
                      <span className="w-8 shrink-0 text-right text-[11px] tabular-nums">
                        {Math.round(project.progress * 100)}%
                      </span>
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-[11px] tabular-nums text-muted-foreground">
                    {formatBytes(projectSize(project).total)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-[11px] text-muted-foreground">
                    {formatDate(project.updated_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </WorkspaceScroll>
      )}
    </Workspace>
  );
}

function projectLayout(project: Project) {
  const mixing = (project.manifest as { mixing?: { channel_layout?: string } }).mixing;
  return mixing?.channel_layout || "—";
}

function StemChip({ stem, pending }: { stem: string; pending: boolean }) {
  return (
    <span
      className={cn(
        "flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px]",
        pending && "opacity-50",
      )}
    >
      <span className="h-2 w-2 shrink-0 rounded-[2px]" style={{ backgroundColor: getStemColor(stem) }} />
      {stem}
    </span>
  );
}

function StorageRows({ project }: { project: Project }) {
  const size = projectSize(project);
  return (
    <>
      <InspectorRow label="Sources" value={formatBytes(size.sources)} />
      <InspectorRow label="Stems" value={formatBytes(size.stems)} />
      <InspectorRow label="Exports" value={formatBytes(size.exports)} />
      <InspectorRow label="Total" value={formatBytes(size.total)} />
    </>
  );
}

function FacetGroup({
  title,
  facets,
  active,
  onSelect,
  total,
  format = (value: string) => value,
}: {
  title: string;
  facets: [string, number][];
  active: string | null;
  onSelect: (value: string | null) => void;
  total: number;
  format?: (value: string) => string;
}) {
  return (
    <div className="mb-3">
      <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground">{title}</p>
      <FacetRow group={title} label="All" count={total} active={active === null} onClick={() => onSelect(null)} />
      {facets.map(([value, count]) => (
        <FacetRow
          key={value}
          group={title}
          label={format(value)}
          count={count}
          active={active === value}
          onClick={() => onSelect(active === value ? null : value)}
        />
      ))}
    </div>
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

const ProjectCard = React.memo(function ProjectCard({
  project,
  selected,
  onSelect,
}: {
  project: Project;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const stems = project.prepared_stems.length ? project.prepared_stems : project.requested_stems;
  return (
    <div
      onClick={() => onSelect(project.id)}
      className={cn(
        "cursor-pointer rounded-lg border bg-card p-2.5 transition-colors",
        selected ? "border-primary bg-primary/5" : "hover:bg-accent/40",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link to={`/projects/${project.id}`} className="block truncate text-[13px] font-semibold hover:underline">
            {project.name}
          </Link>
          <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
            {project.tracks.length} track{project.tracks.length === 1 ? "" : "s"} · {projectLayout(project)} ·{" "}
            {formatBytes(projectSize(project).total)}
          </p>
        </div>
        <Badge variant={statusVariant(project.status)} className="shrink-0 capitalize">
          {statusLabel(project.status)}
        </Badge>
      </div>
      <div className="mt-2 flex h-1.5 gap-px overflow-hidden rounded-full">
        {stems.map((stem) => (
          <span key={stem} className="flex-1" style={{ backgroundColor: getStemColor(stem) }} title={stem} />
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <Progress value={project.progress * 100} />
        <span className="w-8 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground">
          {Math.round(project.progress * 100)}%
        </span>
      </div>
      <p className="mt-1.5 truncate text-[11px] text-muted-foreground">{project.status_message}</p>
    </div>
  );
});
