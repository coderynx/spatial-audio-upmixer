import * as React from "react";
import {
  PANE_HEADROOM,
  PANE_MAX_HEIGHT,
  PANE_MIN_HEIGHT,
  paneStorageKey,
  readStoredPane,
  readStoredPaneHeight,
  type PaneView,
} from "./projectDetailLayout";

/** Bottom-pane view state, height, and drag/keyboard resize handlers for
 * `ProjectDetailPage`. Persists per project via `projectDetailLayout.ts`. */
export function usePaneLayout(projectId: string | undefined) {
  const [paneView, setPaneView] = React.useState<PaneView>(() => readStoredPane(projectId));
  const [paneHeight, setPaneHeight] = React.useState(() => readStoredPaneHeight(projectId));
  const previewColumn = React.useRef<HTMLElement>(null);
  const paneDrag = React.useRef<{ startY: number; startHeight: number } | null>(null);
  React.useEffect(() => {
    setPaneView(readStoredPane(projectId));
    setPaneHeight(readStoredPaneHeight(projectId));
  }, [projectId]);
  const changePane = React.useCallback((next: PaneView) => {
    setPaneView(next);
    try {
      window.localStorage.setItem(paneStorageKey(projectId), next ?? "off");
    } catch {
      // Storage being unavailable only costs the preference, not the view.
    }
  }, [projectId]);
  // Divider drag. Height is clamped against the live column height so the
  // spatial displays above always keep `PANE_HEADROOM`, which is what stops a
  // drag to the top from collapsing them and forcing the page to scroll —
  // and separately against `PANE_MAX_HEIGHT`, an absolute roof so a big
  // enough window can't still drag the pane large enough to squeeze that
  // row thin even though headroom alone would technically allow it.
  const resizePaneTo = React.useCallback((height: number) => {
    const available = previewColumn.current?.clientHeight ?? 0;
    const ceiling = Math.min(PANE_MAX_HEIGHT, Math.max(PANE_MIN_HEIGHT, available - PANE_HEADROOM));
    setPaneHeight(Math.round(Math.min(ceiling, Math.max(PANE_MIN_HEIGHT, height))));
  }, []);
  const beginPaneResize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    paneDrag.current = { startY: event.clientY, startHeight: paneHeight };
  };
  const movePaneResize = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = paneDrag.current;
    if (!drag) return;
    resizePaneTo(drag.startHeight + (drag.startY - event.clientY));
  };
  const endPaneResize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!paneDrag.current) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    paneDrag.current = null;
    try {
      window.localStorage.setItem(`${paneStorageKey(projectId)}.height`, String(paneHeight));
    } catch {
      // See `changePane`.
    }
  };
  const paneResizeKeys = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, number> = { ArrowUp: 16, ArrowDown: -16, PageUp: 64, PageDown: -64 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    resizePaneTo(paneHeight + moves[event.key]);
  };

  return {
    paneView,
    paneHeight,
    previewColumn,
    changePane,
    resizePaneTo,
    beginPaneResize,
    movePaneResize,
    endPaneResize,
    paneResizeKeys,
  };
}
