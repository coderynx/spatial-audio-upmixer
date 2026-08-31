import * as React from "react";
import {
  PANE_MAX_HEIGHT,
  PANE_MIN_HEIGHT,
  paneStorageKey,
  readStoredPane,
  readStoredPaneHeight,
  type PaneView,
} from "./projectDetailLayout";

/** Bottom-pane view state and persisted height for `ProjectDetailPage`. */
export function usePaneLayout(projectId: string | undefined) {
  const [paneView, setPaneView] = React.useState<PaneView>(() => readStoredPane(projectId));
  const [paneHeight, setPaneHeight] = React.useState(() => readStoredPaneHeight(projectId));
  const previewColumn = React.useRef<HTMLElement>(null);
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
  const commitPaneHeight = React.useCallback((height: number) => {
    const next = Math.round(Math.min(PANE_MAX_HEIGHT, Math.max(PANE_MIN_HEIGHT, height)));
    setPaneHeight(next);
    try {
      window.localStorage.setItem(`${paneStorageKey(projectId)}.height`, String(next));
    } catch {
      // See `changePane`.
    }
  }, [projectId]);

  return {
    paneView,
    paneHeight,
    previewColumn,
    changePane,
    commitPaneHeight,
  };
}
