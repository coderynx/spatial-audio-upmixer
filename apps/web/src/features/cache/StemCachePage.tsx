import * as React from "react";
import { Database } from "lucide-react";
import type { Project } from "@/api";
import { EmptyState } from "@/app/EmptyState";
import { useHeaderTitle } from "@/app/HeaderSlot";
import { InspectorGroup, InspectorRow } from "@/app/InspectorRow";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Toolbar, ToolbarSpacer } from "@/app/Toolbar";
import { Workspace, WorkspaceScroll } from "@/app/Workspace";
import { formatBytes } from "@/lib/format";
import { stemCacheEntries } from "@/lib/projectStats";
import { getStemColor, getStemIcon } from "@/lib/stems";
import { cn } from "@/lib/utils";

/** Separated stems are content-addressed and shared between projects, so the
 * cache is presented keyed by stem rather than per project. */
export function StemCachePage({
  projects,
  loading,
  error,
}: {
  projects: Project[];
  loading: boolean;
  error: string | null;
}) {
  const [selectedKey, setSelectedKey] = React.useState<string | null>(null);
  const [familyFilter, setFamilyFilter] = React.useState<string | null>(null);
  useHeaderTitle(React.useMemo(() => <span className="text-[13px] font-semibold">Stem cache</span>, []));

  const entries = React.useMemo(() => stemCacheEntries(projects), [projects]);
  const families = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of entries) {
      const family = stemFamily(entry.stemKey);
      counts.set(family, (counts.get(family) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [entries]);
  const visible = React.useMemo(
    () => (familyFilter ? entries.filter((entry) => stemFamily(entry.stemKey) === familyFilter) : entries),
    [entries, familyFilter],
  );
  const selected = visible.find((entry) => entry.stemKey === selectedKey) || visible[0] || null;
  const totalSize = entries.reduce((total, entry) => total + entry.size, 0);
  const totalCount = entries.reduce((total, entry) => total + entry.count, 0);
  const sharedCount = entries.filter((entry) => entry.projects.length > 1).length;

  return (
    <Workspace
      toolbar={
        <Toolbar>
          <span className="text-[13px] font-medium">Separated stem cache</span>
          <span className="text-[11px] text-muted-foreground">Reused across projects that share source audio.</span>
          <ToolbarSpacer />
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {visible.length} of {entries.length}
          </span>
        </Toolbar>
      }
      rail={
        <WorkspaceScroll className="p-2">
          <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-[.1em] text-muted-foreground">Stem family</p>
          <FacetRow group="Stem family" label="All" count={entries.length} active={familyFilter === null} onClick={() => setFamilyFilter(null)} />
          {families.map(([family, count]) => (
            <FacetRow
              key={family}
              group="Stem family"
              label={family}
              count={count}
              active={familyFilter === family}
              onClick={() => setFamilyFilter(familyFilter === family ? null : family)}
            />
          ))}
        </WorkspaceScroll>
      }
      inspector={
        selected ? (
          <WorkspaceScroll>
            <InspectorGroup title="Cache entry">
              <p className="mb-1.5 flex items-center gap-1.5 text-[13px] font-semibold">
                <span className="h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ backgroundColor: getStemColor(selected.stemKey) }} />
                <span className="truncate">{selected.stemKey}</span>
              </p>
              <InspectorRow label="Copies" value={selected.count} />
              <InspectorRow label="Total size" value={formatBytes(selected.size)} />
              <InspectorRow label="Average size" value={formatBytes(Math.round(selected.size / selected.count))} />
              <InspectorRow label="Channels" value={selected.channels} />
              <InspectorRow label="Sample rate" value={`${selected.sampleRate / 1000} kHz`} />
              <InspectorRow label="Projects" value={selected.projects.length} />
            </InspectorGroup>
            <InspectorGroup title={`Used by · ${selected.projects.length}`}>
              {selected.projects.map((project) => (
                <div key={project.id} className="flex items-center justify-between gap-2 border-b py-1.5 last:border-0">
                  <span className="min-w-0 truncate text-xs">{project.name}</span>
                  <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                    {project.count} · {formatBytes(project.size)}
                  </span>
                </div>
              ))}
            </InspectorGroup>
          </WorkspaceScroll>
        ) : (
          <EmptyState icon={Database} title="No entry selected" description="Pick a cached stem to see which projects reuse it." />
        )
      }
      status={
        <StatusBar>
          <StatusCell label="Entries" value={entries.length} />
          <StatusSeparator />
          <StatusCell label="Copies" value={totalCount} />
          <StatusSeparator />
          <StatusCell label="Shared" value={sharedCount} />
          <StatusSeparator />
          <StatusCell label="On disk" value={formatBytes(totalSize)} />
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
          icon={Database}
          title={entries.length ? "No stems in this family" : "Cache is empty"}
          description={
            entries.length
              ? "Clear the family filter to see the rest of the cache."
              : "Separated stems appear here once a project finishes preparation."
          }
        />
      ) : (
        <WorkspaceScroll>
          <table className="w-full min-w-[620px] text-left text-[13px]">
            <thead className="sticky top-0 z-10 border-b bg-card text-[11px] font-semibold uppercase tracking-[.06em] text-muted-foreground">
              <tr>
                <th className="px-3 py-1.5 font-semibold">Stem</th>
                <th className="px-3 py-1.5 font-semibold">Copies</th>
                <th className="px-3 py-1.5 font-semibold">Channels</th>
                <th className="px-3 py-1.5 font-semibold">Rate</th>
                <th className="px-3 py-1.5 font-semibold">Projects</th>
                <th className="px-3 py-1.5 font-semibold">Size</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((entry) => {
                const StemIcon = getStemIcon(entry.stemKey);
                return (
                  <tr
                    key={entry.stemKey}
                    onClick={() => setSelectedKey(entry.stemKey)}
                    className={cn(
                      "cursor-pointer border-b last:border-0",
                      entry.stemKey === selected?.stemKey ? "bg-primary/10" : "hover:bg-accent/50",
                    )}
                  >
                    <td className="px-3 py-1.5">
                      <span className="flex items-center gap-1.5">
                        <StemIcon className="h-3.5 w-3.5 shrink-0" style={{ color: getStemColor(entry.stemKey) }} />
                        <span className="truncate font-medium">{entry.stemKey}</span>
                      </span>
                    </td>
                    <td className="px-3 py-1.5 tabular-nums">{entry.count}</td>
                    <td className="px-3 py-1.5 tabular-nums">{entry.channels}</td>
                    <td className="px-3 py-1.5 tabular-nums">{entry.sampleRate / 1000} kHz</td>
                    <td className="px-3 py-1.5 tabular-nums">{entry.projects.length}</td>
                    <td className="px-3 py-1.5 tabular-nums text-muted-foreground">{formatBytes(entry.size)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </WorkspaceScroll>
      )}
    </Workspace>
  );
}

const CHILD_FAMILIES: Record<string, string> = {
  "lead vocals": "Vocals",
  "backing vocals": "Vocals",
  kick: "Drums",
  snare: "Drums",
  toms: "Drums",
  "hi-hat": "Drums",
  ride: "Drums",
  crash: "Drums",
};

function stemFamily(stemKey: string) {
  return CHILD_FAMILIES[stemKey.toLowerCase()] || stemKey;
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
        "mb-0.5 flex h-7 w-full items-center justify-between gap-2 rounded-md px-2 text-left text-[13px] transition-colors",
        active ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      <span className="truncate">{label}</span>
      <span className="shrink-0 text-[11px] tabular-nums">{count}</span>
    </button>
  );
}
