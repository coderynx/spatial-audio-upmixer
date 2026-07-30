import { AudioLines } from "lucide-react";
import type { ProjectTrack } from "@/api";
import { cn } from "@/lib/utils";

function statusDot(status: string) {
  if (status === "ready") return null;
  return status === "failed" ? "bg-destructive" : "bg-warning";
}

/** Left counterpart to the Mixing/Mastering/Delivery right panel — the
 * per-track selector those three stages share, moved out of the top bar
 * (where `TrackSwitcher` used to render it as a cramped dropdown) into a
 * real panel with room for every track's name and state at a glance.
 *
 * Stays mounted at `w-0` when collapsed rather than unmounting (`null`) —
 * `w-56`/`w-0` transition on `width`, clipped by `overflow-hidden`, is what
 * gives the collapse/expand its slide animation. The inner header/nav keep
 * a fixed `w-56` of their own so their content doesn't reflow or wrap as
 * the outer box's width animates; the outer box is what does the clipping.
 * Reopening it is `ProjectDetailPage`'s top-bar "Tracks" toggle — this
 * component carries no collapse control of its own, since a second one
 * living inside a box that's about to shrink to nothing would be
 * redundant with the one guaranteed to survive the collapse. */
export function TrackRail({
  tracks,
  value,
  onChange,
  collapsed,
}: {
  tracks: ProjectTrack[];
  value: string | null;
  onChange: (trackId: string) => void;
  collapsed: boolean;
}) {
  return (
    <aside
      aria-hidden={collapsed}
      className={cn(
        "flex min-h-0 flex-col overflow-hidden bg-card transition-[width] duration-150 ease-in-out",
        collapsed ? "w-0" : "w-56 border-r",
      )}
    >
      <div className="flex h-8 w-56 shrink-0 items-center border-b px-2">
        <p className="truncate text-[11px] font-semibold uppercase tracking-[.08em] text-muted-foreground">
          Tracks
        </p>
      </div>
      <nav className="min-h-0 w-56 flex-1 overflow-y-auto p-2">
        {tracks.map((track) => {
          const active = track.id === value;
          const label = track.asset.title || track.asset.filename;
          const dot = statusDot(track.status);
          return (
            <button
              key={track.id}
              type="button"
              tabIndex={collapsed ? -1 : undefined}
              aria-current={active ? "true" : undefined}
              aria-label={dot ? `${label} — ${track.status}` : undefined}
              onClick={() => onChange(track.id)}
              className={cn(
                "mb-0.5 flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[13px] transition-colors",
                active
                  ? "bg-primary/15 font-medium text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <span className="relative shrink-0">
                <AudioLines className="h-4 w-4" aria-hidden="true" />
                {dot && (
                  <span
                    className={cn("absolute -bottom-0.5 -right-0.5 h-1.5 w-1.5 rounded-full ring-1 ring-card", dot)}
                    aria-hidden="true"
                  />
                )}
              </span>
              <span className="min-w-0 flex-1 truncate">{label}</span>
              <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                {track.stems.length || ""}
              </span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
