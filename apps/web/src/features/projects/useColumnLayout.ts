import * as React from "react";
import {
  ELEVATION_MIN_WIDTH,
  HAZE_MIN_WIDTH,
  METERS_DEFAULT_SHARE,
  METERS_MAX_WIDTH,
  METERS_MIN_WIDTH,
  ROW_GAP,
  columnStorageKey,
  readStoredColumnExtra,
} from "./projectDetailLayout";

/** Haze/Elevation/Meters row sizing for `ProjectDetailPage`: live row
 * measurement plus each column's user-dragged extra, persisted per project. */
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
  React.useEffect(() => {
    setHazeExtra(readStoredColumnExtra(projectId, "haze"));
    setElevationExtra(readStoredColumnExtra(projectId, "elevation"));
  }, [projectId]);
  const commitColumnExtra = (name: "haze" | "elevation", px: number) => {
    try {
      window.localStorage.setItem(`${columnStorageKey(projectId)}.${name}Extra`, String(px));
    } catch {
      // See usePaneLayout's changePane.
    }
  };
  // Clamped every render against the row's live measured size (not just on drag),
  // so a window resize between drags can't leave a stale width that no longer fits.
  const hazeMaxWidth = Math.max(HAZE_MIN_WIDTH, rowSize.width - ROW_GAP * 2 - ELEVATION_MIN_WIDTH - METERS_MIN_WIDTH);
  const hazeWidth = Math.min(hazeMaxWidth, Math.max(HAZE_MIN_WIDTH, rowSize.height + hazeExtra));
  // Elevation is flex-1 with no stored width; elevationExtra shrinks Meters' width instead.
  const metersMaxWidth = Math.min(METERS_MAX_WIDTH, Math.max(METERS_MIN_WIDTH, rowSize.width - hazeWidth - ROW_GAP * 2 - ELEVATION_MIN_WIDTH));
  const metersWidth = Math.min(metersMaxWidth, Math.max(METERS_MIN_WIDTH, METERS_DEFAULT_SHARE - elevationExtra));

  return {
    rowRef,
    rowSize,
    hazeExtra,
    setHazeExtra,
    elevationExtra,
    setElevationExtra,
    commitColumnExtra,
    hazeMaxWidth,
    hazeWidth,
    metersMaxWidth,
    metersWidth,
  };
}
