import * as React from "react";
import {
  columnStorageKey,
  readStoredColumnExtra,
} from "./projectDetailLayout";

/** Spatial-view/Meters/Loudness row sizing for `ProjectDetailPage`. */
export function useColumnLayout(projectId: string | undefined) {
  const [elevationExtra, setElevationExtra] = React.useState(() => readStoredColumnExtra(projectId, "elevation"));
  const [loudnessExtra, setLoudnessExtra] = React.useState(() => readStoredColumnExtra(projectId, "loudness"));
  React.useEffect(() => {
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
  return {
    elevationExtra,
    setElevationExtra,
    loudnessExtra,
    setLoudnessExtra,
    commitColumnExtra,
  };
}
