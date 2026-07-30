import * as React from "react";
import { Download, HardDrive } from "lucide-react";
import type { Job, Project } from "@/api";
import { EmptyState } from "@/app/EmptyState";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { InspectorGroup, InspectorRow } from "@/app/InspectorRow";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Toolbar, ToolbarSpacer } from "@/app/Toolbar";
import { Workspace, WorkspaceScroll } from "@/app/Workspace";
import { Button } from "@/components/ui/button";
import { formatBytes, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

type Category = "sources" | "stems" | "exports";

type Item = {
  id: string;
  name: string;
  owner: string;
  category: Category;
  size: number;
  updatedAt: string;
  downloadUrl: string | null;
};

const CATEGORY_LABEL: Record<Category, string> = {
  sources: "Source audio",
  stems: "Separated stems",
  exports: "Rendered exports",
};

const CATEGORY_COLOR: Record<Category, string> = {
  sources: "bg-primary",
  stems: "bg-success",
  exports: "bg-warning",
};

export function StoragePage({
  projects,
  jobs,
  loading,
  error,
}: {
  projects: Project[];
  jobs: Job[];
  loading: boolean;
  error: string | null;
}) {
  const [categoryFilter, setCategoryFilter] = React.useState<Category | null>(null);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  useHeaderTitle(React.useMemo(() => <span className="text-[13px] font-semibold">Storage</span>, []));

  const items = React.useMemo(() => collectItems(projects, jobs), [projects, jobs]);
  const totals = React.useMemo(() => {
    const byCategory: Record<Category, number> = { sources: 0, stems: 0, exports: 0 };
    for (const item of items) byCategory[item.category] += item.size;
    return byCategory;
  }, [items]);
  const total = totals.sources + totals.stems + totals.exports;
  const visible = React.useMemo(
    () => (categoryFilter ? items.filter((item) => item.category === categoryFilter) : items),
    [items, categoryFilter],
  );
  const selected = visible.find((item) => item.id === selectedId) || visible[0] || null;

  return (
    <Workspace
      toolbar={
        <Toolbar>
          <span className="text-[13px] font-medium">Largest items</span>
          <span className="text-[11px] text-muted-foreground">Ranked by size across every project and job.</span>
          <ToolbarSpacer />
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {visible.length} of {items.length}
          </span>
        </Toolbar>
      }
      rail={
        <WorkspaceScroll className="p-2">
          <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground">Category</p>
          <FacetRow
            label="All"
            size={total}
            active={categoryFilter === null}
            onClick={() => setCategoryFilter(null)}
          />
          {(Object.keys(CATEGORY_LABEL) as Category[]).map((category) => (
            <FacetRow
              key={category}
              label={CATEGORY_LABEL[category]}
              size={totals[category]}
              active={categoryFilter === category}
              onClick={() => setCategoryFilter(categoryFilter === category ? null : category)}
            />
          ))}
          <div className="mt-3 px-2">
            <p className="pb-1.5 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground">Breakdown</p>
            <div className="flex h-2 gap-px overflow-hidden rounded-full bg-secondary">
              {(Object.keys(CATEGORY_LABEL) as Category[]).map((category) => (
                <span
                  key={category}
                  className={CATEGORY_COLOR[category]}
                  style={{ width: total ? `${(totals[category] / total) * 100}%` : "0%" }}
                  title={`${CATEGORY_LABEL[category]} — ${formatBytes(totals[category])}`}
                />
              ))}
            </div>
            <div className="mt-2 space-y-1">
              {(Object.keys(CATEGORY_LABEL) as Category[]).map((category) => (
                <div key={category} className="flex items-center gap-1.5 text-[11px]">
                  <span className={cn("h-2 w-2 shrink-0 rounded-[2px]", CATEGORY_COLOR[category])} />
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">{CATEGORY_LABEL[category]}</span>
                  <span className="shrink-0 tabular-nums">{formatBytes(totals[category])}</span>
                </div>
              ))}
            </div>
          </div>
        </WorkspaceScroll>
      }
      inspector={
        selected ? (
          <>
            <WorkspaceScroll>
              <InspectorGroup title="Item">
                <p className="mb-1.5 truncate text-[13px] font-semibold">{selected.name}</p>
                <InspectorRow label="Category" value={CATEGORY_LABEL[selected.category]} />
                <InspectorRow label="Owner" value={selected.owner} />
                <InspectorRow label="Size" value={formatBytes(selected.size)} />
                <InspectorRow
                  label="Share of total"
                  value={total ? `${((selected.size / total) * 100).toFixed(1)}%` : "—"}
                />
                <InspectorRow label="Updated" value={formatDate(selected.updatedAt)} />
              </InspectorGroup>
            </WorkspaceScroll>
            {selected.downloadUrl && (
              <div className="shrink-0 border-t p-2">
                <Button className="w-full" variant="outline" asChild>
                  <a href={selected.downloadUrl}>
                    <Download />
                    Download
                  </a>
                </Button>
              </div>
            )}
          </>
        ) : (
          <EmptyState icon={HardDrive} title="No item selected" description="Pick a stored item to see where it comes from." />
        )
      }
      status={
        <StatusBar>
          <StatusCell label="Total" value={formatBytes(total)} />
          <StatusSeparator />
          <StatusCell label="Sources" value={formatBytes(totals.sources)} />
          <StatusSeparator />
          <StatusCell label="Stems" value={formatBytes(totals.stems)} />
          <StatusSeparator />
          <StatusCell label="Exports" value={formatBytes(totals.exports)} />
          <StatusSpacer />
          {error && <span className="truncate text-destructive">{error}</span>}
        </StatusBar>
      }
    >
      {loading ? (
        <div className="space-y-2 p-3">
          {[0, 1, 2, 3, 4].map((item) => (
            <div key={item} className="h-9 animate-pulse rounded-md border bg-muted/40" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={HardDrive}
          title={items.length ? "Nothing in this category" : "Nothing stored yet"}
          description={
            items.length
              ? "Clear the category filter to see everything on disk."
              : "Imported audio, separated stems, and rendered exports are listed here."
          }
        />
      ) : (
        <WorkspaceScroll>
          <table className="w-full min-w-[620px] text-left text-[13px]">
            <thead className="sticky top-0 z-10 border-b bg-card text-[11px] font-semibold uppercase tracking-[.06em] text-muted-foreground">
              <tr>
                <th className="px-3 py-1.5 font-semibold">Item</th>
                <th className="px-3 py-1.5 font-semibold">Category</th>
                <th className="min-w-40 px-3 py-1.5 font-semibold">Share</th>
                <th className="px-3 py-1.5 font-semibold">Size</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => (
                <tr
                  key={item.id}
                  onClick={() => setSelectedId(item.id)}
                  className={cn(
                    "cursor-pointer border-b last:border-0",
                    item.id === selected?.id ? "bg-primary/10" : "hover:bg-accent/50",
                  )}
                >
                  <td className="max-w-xs px-3 py-1.5">
                    <p className="truncate font-medium">{item.name}</p>
                    <p className="truncate text-[11px] text-muted-foreground">{item.owner}</p>
                  </td>
                  <td className="px-3 py-1.5 text-[11px] text-muted-foreground">{CATEGORY_LABEL[item.category]}</td>
                  <td className="px-3 py-1.5">
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
                      <span
                        className={cn("block h-full", CATEGORY_COLOR[item.category])}
                        style={{ width: total ? `${(item.size / total) * 100}%` : "0%" }}
                      />
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5 tabular-nums text-muted-foreground">
                    {formatBytes(item.size)}
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

function collectItems(projects: Project[], jobs: Job[]): Item[] {
  const items: Item[] = [];
  for (const project of projects) {
    for (const track of project.tracks) {
      items.push({
        id: `asset-${track.asset.id}`,
        name: track.asset.title || track.asset.filename,
        owner: project.name,
        category: "sources",
        size: track.asset.size_bytes,
        updatedAt: project.updated_at,
        downloadUrl: track.asset.audio_url,
      });
      for (const stem of track.stems) {
        items.push({
          id: `stem-${stem.id}`,
          name: `${stem.stem_key} — ${track.asset.title || track.asset.filename}`,
          owner: project.name,
          category: "stems",
          size: stem.size_bytes,
          updatedAt: project.updated_at,
          downloadUrl: stem.audio_url,
        });
      }
    }
  }
  for (const job of jobs) {
    for (const artifact of [...job.artifacts, ...job.tracks.flatMap((track) => track.artifacts)]) {
      items.push({
        id: `artifact-${artifact.id}`,
        name: artifact.filename,
        owner: job.name,
        category: "exports",
        size: artifact.size_bytes,
        updatedAt: job.updated_at,
        downloadUrl: artifact.download_url,
      });
    }
  }
  return items.sort((a, b) => b.size - a.size);
}

function FacetRow({
  label,
  size,
  active,
  onClick,
}: {
  label: string;
  size: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`Category: ${label} (${formatBytes(size)})`}
      onClick={onClick}
      className={cn(
        "mb-0.5 flex h-7 w-full items-center justify-between gap-2 rounded-md px-2 text-left text-[13px] transition-colors",
        active ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      <span className="truncate">{label}</span>
      <span className="shrink-0 text-[11px] tabular-nums">{formatBytes(size)}</span>
    </button>
  );
}
