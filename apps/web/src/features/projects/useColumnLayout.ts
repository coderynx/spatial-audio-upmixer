import * as React from "react";
import {
  ELEVATION_MIN_WIDTH,
  HAZE_MIN_WIDTH,
  LOUDNESS_DEFAULT_WIDTH,
  LOUDNESS_MAX_WIDTH,
  LOUDNESS_MIN_WIDTH,
  METERS_GROUP_DEFAULT_SHARE,
  METERS_GROUP_MAX_WIDTH,
  METERS_GROUP_MIN_WIDTH,
  METERS_MAX_WIDTH,
  METERS_MIN_WIDTH,
  ROW_GAP,
  columnStorageKey,
  readStoredColumnExtra,
} from "./projectDetailLayout";

/** Haze/Elevation/Meters/Loudness row sizing for `ProjectDetailPage`: live
 * row measurement plus each column's user-dragged extra, persisted per
 * project. */
export function useColumnLayout(projectId: string | undefined) {
  // Callback ref, not useRef + useEffect([]): the row only enters the DOM once
  // `ready` flips true, so a mount-only effect would find rowRef.current still
  // null and never retry.
  const rowObserver = React.useRef<ResizeObserver | null>(null);
  const [rowSize, setRowSize] = React.useState({ width: 0, height: 0 });
  const rowRef = React.useCallback((node: HTMLDivElement | null) => {
    rowObserver.current?.disconnect();
    rowObserver.current = null;
    if (!node) return;
    // Read synchronously on attach — the observer's first callback isn't
    // guaranteed to land promptly (same as HazeView's resize handling).
    setRowSize({ width: node.clientWidth, height: node.clientHeight });
    const observer = new ResizeObserver(([entry]) => {
      setRowSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(node);
    rowObserver.current = observer;
  }, []);
  React.useEffect(() => () => rowObserver.current?.disconnect(), []);
  const [hazeExtra, setHazeExtra] = React.useState(() => readStoredColumnExtra(projectId, "haze"));
  const [elevationExtra, setElevationExtra] = React.useState(() => readStoredColumnExtra(projectId, "elevation"));
  const [loudnessExtra, setLoudnessExtra] = React.useState(() => readStoredColumnExtra(projectId, "loudness"));
  React.useEffect(() => {
    setHazeExtra(readStoredColumnExtra(projectId, "haze"));
    setElevationExtra(readStoredColumnExtra(projectId, "elevation"));
    setLoudnessExtra(readStoredColumnExtra(projectId, "loudness"));
  }, [projectId]);
  const commitColumnExtra = (name: "haze" | "elevation" | "loudness", px: number) => {
    try {
      window.localStorage.setItem(`${columnStorageKey(projectId)}.${name}Extra`, String(px));
    } catch {
      // See usePaneLayout's changePane.
    }
  };
  // Clamped every render against the row's live measured size (not just on drag),
  // so a window resize between drags can't leave a stale width that no longer fits.
  // Two gaps separate the row's three slots (Haze/Elevation/Meters-group); a third,
  // nested gap splits the Meters-group slot into Meters and Loudness below.
  const hazeMaxWidth = Math.max(
    HAZE_MIN_WIDTH,
    rowSize.width - ROW_GAP * 2 - ELEVATION_MIN_WIDTH - METERS_GROUP_MIN_WIDTH,
  );
  const hazeWidth = Math.min(hazeMaxWidth, Math.max(HAZE_MIN_WIDTH, rowSize.height + hazeExtra));
  // Elevation is flex-1 with no stored width; elevationExtra shrinks the Meters-group's
  // combined width instead — Meters and Loudness split that combined width below,
  // independently of Elevation, so dragging either one only ever squashes the other.
  const groupMaxWidth = Math.min(
    METERS_GROUP_MAX_WIDTH,
    Math.max(METERS_GROUP_MIN_WIDTH, rowSize.width - hazeWidth - ROW_GAP * 2 - ELEVATION_MIN_WIDTH),
  );
  const groupWidth = Math.min(groupMaxWidth, Math.max(METERS_GROUP_MIN_WIDTH, METERS_GROUP_DEFAULT_SHARE - elevationExtra));
  // Loudness trades width directly against Meters within the group's now-settled total —
  // floor/ceiling both account for Meters' own min/max so neither can squash the other
  // past its floor, but otherwise the drag is free (mirrors the Elevation/group split above,
  // just one level in).
  const loudnessFloor = Math.max(LOUDNESS_MIN_WIDTH, groupWidth - ROW_GAP - METERS_MAX_WIDTH);
  const loudnessCeil = Math.min(LOUDNESS_MAX_WIDTH, groupWidth - ROW_GAP - METERS_MIN_WIDTH);
  const loudnessWidth = Math.min(loudnessCeil, Math.max(loudnessFloor, LOUDNESS_DEFAULT_WIDTH - loudnessExtra));

  return {
    rowRef,
    rowSize,
    hazeExtra,
    setHazeExtra,
    elevationExtra,
    setElevationExtra,
    loudnessExtra,
    setLoudnessExtra,
    commitColumnExtra,
    hazeMaxWidth,
    hazeWidth,
    groupMaxWidth,
    groupWidth,
    loudnessFloor,
    loudnessCeil,
    loudnessWidth,
  };
}
