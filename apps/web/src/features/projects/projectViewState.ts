import * as React from "react";
import { api, type Project } from "@/api";
import type { OutputMode } from "./audioEngine";
import type { SpatialProfile, TransauralProfile } from "./masteringProfiles";

export type ProjectViewState = {
  stemOrder: string[];
  outputMode: OutputMode;
  spatialProfile: SpatialProfile;
  transauralProfile: TransauralProfile;
  masterVolume: number;
  masteringBypassed: boolean;
  hazeIntensity: number;
  elevationIntensity: number;
};

const DEFAULT_VIEW_STATE: ProjectViewState = {
  stemOrder: [],
  outputMode: "binaural",
  spatialProfile: "studio",
  transauralProfile: "stereo",
  masterVolume: 1,
  masteringBypassed: false,
  hazeIntensity: 0.5,
  elevationIntensity: 0.5,
};

const OUTPUT_MODES: OutputMode[] = ["binaural", "transaural", "stereo", "native"];
const SPATIAL_PROFILES: SpatialProfile[] = ["studio", "listening", "flat"];
const TRANSAURAL_PROFILES: TransauralProfile[] = ["stereo", "smart_speaker", "car", "laptop", "phone"];

function clamp01(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : fallback;
}

function pick<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

// The server stores `view_state` as an untyped dict (forward-compatible
// across client versions), so every field is coerced here rather than
// trusted — an unknown profile string or out-of-range number falls back to
// its default instead of reaching a <select> or the audio engine.
function normalizeViewState(raw: Record<string, unknown> | undefined): ProjectViewState {
  const stemOrder = Array.isArray(raw?.stem_order)
    ? raw.stem_order.filter((stem): stem is string => typeof stem === "string")
    : DEFAULT_VIEW_STATE.stemOrder;
  return {
    stemOrder,
    outputMode: pick(raw?.output_mode, OUTPUT_MODES, DEFAULT_VIEW_STATE.outputMode),
    spatialProfile: pick(raw?.spatial_profile, SPATIAL_PROFILES, DEFAULT_VIEW_STATE.spatialProfile),
    transauralProfile: pick(raw?.transaural_profile, TRANSAURAL_PROFILES, DEFAULT_VIEW_STATE.transauralProfile),
    masterVolume: clamp01(raw?.master_volume, DEFAULT_VIEW_STATE.masterVolume),
    masteringBypassed: raw?.mastering_bypassed === true,
    hazeIntensity: clamp01(raw?.haze_intensity, DEFAULT_VIEW_STATE.hazeIntensity),
    elevationIntensity: clamp01(raw?.elevation_intensity, DEFAULT_VIEW_STATE.elevationIntensity),
  };
}

function toPayload(state: ProjectViewState): Record<string, unknown> {
  return {
    stem_order: state.stemOrder,
    output_mode: state.outputMode,
    spatial_profile: state.spatialProfile,
    transaural_profile: state.transauralProfile,
    master_volume: state.masterVolume,
    mastering_bypassed: state.masteringBypassed,
    haze_intensity: state.hazeIntensity,
    elevation_intensity: state.elevationIntensity,
  };
}

/** Timeline/monitoring preferences for a project — stem order, listening
 * profile, master volume, A/B bypass, haze/elevation intensity. Initializes
 * once from `project.view_state` (same `initialized`-ref guard as
 * `ProjectDetailPage`'s manifest, so the 2s poll/SSE stream can never
 * clobber a live edit) and saves back on a 350ms debounce, matching
 * `queueSave`. */
export function useProjectViewState(projectId: string | undefined, project: Project | null) {
  const [state, setState] = React.useState<ProjectViewState>(DEFAULT_VIEW_STATE);
  const [ready, setReady] = React.useState(false);
  const initialized = React.useRef(false);
  const saveTimer = React.useRef<number | null>(null);
  React.useEffect(() => { initialized.current = false; setReady(false); }, [projectId]);
  React.useEffect(() => {
    if (initialized.current || !project) return;
    initialized.current = true;
    setState(normalizeViewState(project.view_state));
    setReady(true);
  }, [project]);
  React.useEffect(() => () => { if (saveTimer.current) window.clearTimeout(saveTimer.current); }, []);
  const patchViewState = React.useCallback((partial: Partial<ProjectViewState>) => {
    if (!projectId) return;
    setState((current) => {
      const next = { ...current, ...partial };
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        void api.saveProjectViewState(projectId, toPayload(next)).catch(() => {
          // A dropped monitoring-preference save costs a stale slider on
          // reload, not the mix itself — not worth surfacing as a project error.
        });
      }, 350);
      return next;
    });
  }, [projectId]);
  return { viewState: state, ready, patchViewState };
}
