/** The bottom pane's two views, or `null` for collapsed. Persisted per
 * project so a session comes back to the surface the user was working in. */
export type PaneView = "timeline" | "mixer" | null;

export const PANE_MIN_HEIGHT = 140;
export const PANE_DEFAULT_HEIGHT = 260;
/** Left for the spatial displays above the pane, so dragging the divider to
 * the top of its travel can never squeeze them out of existence. */
export const PANE_HEADROOM = 220;
/** Absolute roof on the pane's own height, independent of how much headroom
 * a large window would otherwise allow — on a tall display `available -
 * PANE_HEADROOM` alone permits a pane so large the spatial row above it
 * shrinks to an unusably thin strip well before that dynamic ceiling is
 * reached. This caps it outright. */
export const PANE_MAX_HEIGHT = 480;

export function paneStorageKey(projectId: string | undefined) {
  return `upmixer.project.${projectId || "unknown"}.pane`;
}

export function readStoredPane(projectId: string | undefined): PaneView {
  try {
    const stored = window.localStorage.getItem(paneStorageKey(projectId));
    if (stored === "mixer" || stored === "timeline") return stored;
    if (stored === "off") return null;
  } catch {
    // Private-mode or blocked storage: fall through to the default.
  }
  return "timeline";
}

export function readStoredPaneHeight(projectId: string | undefined): number {
  try {
    const stored = Number(window.localStorage.getItem(`${paneStorageKey(projectId)}.height`));
    if (Number.isFinite(stored) && stored >= PANE_MIN_HEIGHT) return Math.min(stored, PANE_MAX_HEIGHT);
  } catch {
    // Same fallback as `readStoredPane`.
  }
  return PANE_DEFAULT_HEIGHT;
}

/** Floors (and, for Meters, a ceiling) for the spatial row's three
 * displays — Haze's own drag-resize can't shrink it past `HAZE_MIN_WIDTH`;
 * Elevation is a true `flex-1` with `min-w-[ELEVATION_MIN_WIDTH]`, so it can
 * never be squeezed past that either, no matter what Haze/Meters do; Meters'
 * own drag-resize is bounded by both `METERS_MIN_WIDTH` and
 * `METERS_MAX_WIDTH` (this used to be `ChannelMeters`' own baked-in
 * `min-w-[180px] max-w-[480px]`, moved out to the caller alongside the rest
 * of this resize system). */
export const HAZE_MIN_WIDTH = 140;
export const ELEVATION_MIN_WIDTH = 160;
export const METERS_MIN_WIDTH = 180;
export const METERS_MAX_WIDTH = 480;
/** Gap between the three displays (matches the row's own `gap-2`). */
export const ROW_GAP = 8;
/** Meters' default width when neither column has been dragged — Haze takes
 * its own square, Meters takes this (a reasonable point in its own range),
 * and Elevation (`flex-1`) takes whatever's left. */
export const METERS_DEFAULT_SHARE = 320;

export function trackRailStorageKey(projectId: string | undefined) {
  return `upmixer.project.${projectId || "unknown"}.trackRail`;
}

export function readStoredTrackRailCollapsed(projectId: string | undefined): boolean {
  try {
    return window.localStorage.getItem(trackRailStorageKey(projectId)) === "1";
  } catch {
    return false;
  }
}

export function columnStorageKey(projectId: string | undefined) {
  return `upmixer.project.${projectId || "unknown"}.columns`;
}

export function readStoredColumnExtra(projectId: string | undefined, name: "haze" | "elevation"): number {
  try {
    const stored = Number(window.localStorage.getItem(`${columnStorageKey(projectId)}.${name}Extra`));
    if (Number.isFinite(stored)) return stored;
  } catch {
    // Same fallback as the pane helpers above.
  }
  return 0;
}
