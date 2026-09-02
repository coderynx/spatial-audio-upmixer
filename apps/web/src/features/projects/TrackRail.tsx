import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ProjectTrack } from "@/api";
import { cn } from "@/lib/utils";
import { useRuntime } from "@/runtime";
import { ProjectTitle } from "./ProjectTitle";
import { deliveryTargetLabel } from "./deliveryTargets";

export type LayoutSelection = { trackId: string; layout: string };

/** Left counterpart to the Mixing/Mastering/Delivery right panel — the
 * per-track selector those three stages share, moved out of the top bar
 * (where `TrackSwitcher` used to render it as a cramped dropdown) into a
 * real panel with room for every track's name and state at a glance.
 *
 * A track expands into its speaker layouts, and a *layout* is what the three
 * stages are actually keyed on: each (track, layout) pair carries its own
 * mix, master and delivery, so switching one here re-points the whole right
 * panel and rebuilds the preview engine. The layout set itself is edited in
 * the Prepare tab, not here — this is a selector.
 *
 * Stays mounted at `w-0` when collapsed rather than unmounting (`null`) —
 * `w-56`/`w-0` transition on `width`, clipped by `overflow-hidden`, is what
 * gives the collapse/expand its slide animation. The inner header/nav keep
 * a fixed `w-[300px]` of their own so their content doesn't reflow or wrap as
 * the outer box's width animates; the outer box is what does the clipping.
 * Reopening it is `ProjectDetailPage`'s top-bar "Tracks" toggle — this
 * component carries no collapse control of its own, since a second one
 * living inside a box that's about to shrink to nothing would be
 * redundant with the one guaranteed to survive the collapse. */
export function TrackRail({
  tracks,
  value,
  onChange,
  onRename = () => {},
  collapsed,
}: {
  tracks: ProjectTrack[];
  value: LayoutSelection | null;
  onChange: (selection: LayoutSelection) => void;
  onRename?: (trackId: string, name: string) => void;
  collapsed: boolean;
}) {
  const runtime = useRuntime();
  const [collapsedTracks, setCollapsedTracks] = React.useState<Record<string, boolean>>({});
  return (
    <aside
      aria-hidden={collapsed}
      className={cn(
        "flex min-h-0 flex-col overflow-hidden bg-card transition-[width] duration-150 ease-in-out",
        collapsed ? "w-0" : "w-[300px] border-r",
      )}
    >
      <div className="flex h-8 w-[300px] shrink-0 items-center border-b px-2">
        <p className="truncate text-[11px] font-semibold uppercase tracking-[.08em] text-muted-foreground">
          Tracks
        </p>
      </div>
      <nav className="min-h-0 w-[300px] flex-1 overflow-y-auto p-2">
        {tracks.map((track) => {
          const label = track.asset.title || track.asset.filename;
          const open = !collapsedTracks[track.id];
          const activeTrack = track.id === value?.trackId;
          return (
            <div key={track.id} className="mb-0.5">
              <div
                className={cn(
                  "flex h-8 w-full items-center gap-1 rounded-md pr-2 text-[13px]",
                  activeTrack ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <button
                  type="button"
                  tabIndex={collapsed ? -1 : undefined}
                  aria-label={open ? `Collapse ${label}` : `Expand ${label}`}
                  aria-expanded={open}
                  onClick={() =>
                    setCollapsedTracks((current) => ({ ...current, [track.id]: !current[track.id] }))
                  }
                  className="grid h-6 w-6 shrink-0 place-items-center rounded-md hover:bg-accent hover:text-foreground"
                >
                  {open ? (
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                </button>
                <ProjectTitle
                  name={track.name || label}
                  entity="track"
                  isTauri={runtime.isTauri}
                  onClick={() => onChange({ trackId: track.id, layout: activeTrack && value ? value.layout : track.layouts[0] })}
                  onRename={(name) => onRename(track.id, name)}
                />
              </div>
              {open && (
                <div className="ml-3 border-l pl-1">
                  {track.layouts.map((layout) => {
                    const active = activeTrack && layout === value?.layout;
                    return (
                      <button
                        key={layout}
                        type="button"
                        tabIndex={collapsed ? -1 : undefined}
                        aria-label={layout}
                        aria-current={active ? "true" : undefined}
                        onClick={() => onChange({ trackId: track.id, layout })}
                        className={cn(
                          "mb-0.5 flex h-7 w-full items-center rounded-md px-2 text-left text-[12px] transition-colors",
                          active
                            ? "bg-primary/15 font-medium text-primary"
                            : "text-muted-foreground hover:bg-accent hover:text-foreground",
                        )}
                      >
                        <span className="min-w-0 flex-1 truncate">{deliveryTargetLabel(track, layout)}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
