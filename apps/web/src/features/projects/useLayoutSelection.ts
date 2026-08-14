import * as React from "react";
import type { Project } from "@/api";
import type { LayoutSelection } from "./TrackRail";

function storageKey(projectId: string | undefined) {
  return `upmixer.project.${projectId || "unknown"}.layoutSelection`;
}

function readStored(projectId: string | undefined): LayoutSelection | null {
  try {
    const raw = window.localStorage.getItem(storageKey(projectId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return typeof parsed?.trackId === "string" && typeof parsed?.layout === "string" ? parsed : null;
  } catch {
    return null;
  }
}

/** The (track, layout) pair the Mixing/Mastering/Delivery stages are editing.
 *
 * Persisted per project so reopening a session lands on the layout it was
 * left in, and reconciled against the project on every load: a track or a
 * layout can disappear (deleted track, layout removed in the Prepare tab),
 * and the stages need a valid pair at all times, never a dangling one. */
export function useLayoutSelection(projectId: string | undefined, project: Project | null) {
  const [selection, setSelection] = React.useState<LayoutSelection | null>(() => readStored(projectId));
  React.useEffect(() => setSelection(readStored(projectId)), [projectId]);

  // Resolved rather than corrected through an effect: an effect would render
  // one frame against a stale pair, which for the preview engine is a whole
  // teardown/rebuild cycle against the wrong layout.
  const resolved = React.useMemo<LayoutSelection | null>(() => {
    const tracks = project?.tracks || [];
    if (!tracks.length) return null;
    const track = tracks.find((item) => item.id === selection?.trackId) || tracks[0];
    if (!track.layouts.length) return null;
    const layout = track.layouts.includes(selection?.layout || "") ? selection!.layout : track.layouts[0];
    return { trackId: track.id, layout };
  }, [project, selection]);

  React.useEffect(() => {
    if (!resolved) return;
    try {
      window.localStorage.setItem(storageKey(projectId), JSON.stringify(resolved));
    } catch {
      // Storage being unavailable only costs the preference, not the view.
    }
  }, [projectId, resolved]);

  return { selection: resolved, setSelection };
}
