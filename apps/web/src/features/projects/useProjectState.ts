import * as React from "react";
import { api, type Project, type ProjectTrack } from "@/api";
import { normalizeManifest, type Manifest } from "@/lib/manifest";

const TERMINAL_STATUSES = ["ready", "failed", "expansion_failed"];

function isSettled(project: Project | null): boolean {
  return Boolean(
    project
    && TERMINAL_STATUSES.includes(project.status)
    && !project.reference_match_pending
    && !project.peaks_pending,
  );
}

/**
 * Owns the project snapshot, its manifest, and every write path back to the
 * server (per-track layout overrides and the one-off name/quality/reference
 * saves).
 *
 * Returns `applyProject`, which callers outside this hook must use instead of
 * `setProject` so their snapshot participates in the same ordering guarantee.
 */
export function useProjectState(projectId: string | undefined, onFirstLoad: (project: Project) => void) {
  const [project, setProject] = React.useState<Project | null>(null);
  const [manifest, setManifest] = React.useState<Manifest | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [sseHealthy, setSseHealthy] = React.useState(false);
  const initialized = React.useRef(false);
  const onFirstLoadRef = React.useRef(onFirstLoad);
  onFirstLoadRef.current = onFirstLoad;

  React.useEffect(() => {
    initialized.current = false;
    setSseHealthy(false);
  }, [projectId]);

  // The poll, the SSE stream, and every save each return a full snapshot
  // independently, so a poll issued before a save can resolve after it and
  // clobber the fresh response. Every write stamps a sequence number and
  // drops responses older than the newest already applied.
  const projectRequestSeq = React.useRef(0);
  const appliedProjectSeq = React.useRef(0);
  const shouldApplyProject = React.useCallback((seq: number) => {
    if (seq < appliedProjectSeq.current) return false;
    appliedProjectSeq.current = seq;
    return true;
  }, []);
  const nextSeq = React.useCallback(() => ++projectRequestSeq.current, []);
  const applyProject = React.useCallback((next: Project) => {
    if (shouldApplyProject(nextSeq())) setProject(next);
  }, [nextSeq, shouldApplyProject]);

  const load = React.useCallback(async () => {
    if (!projectId) return;
    const seq = nextSeq();
    try {
      const next = await api.getProject(projectId);
      if (shouldApplyProject(seq)) {
        setProject(next);
        if (!initialized.current) {
          initialized.current = true;
          setManifest(normalizeManifest(next.manifest));
          onFirstLoadRef.current(next);
        }
      }
      setError(null);
    } catch (reason) { setError((reason as Error).message); }
  }, [projectId, nextSeq, shouldApplyProject]);

  React.useEffect(() => {
    void load();
    if (isSettled(project) || sseHealthy) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on status/reference_match_pending/peaks_pending only: a mid-poll `project` update shouldn't restart the interval
  }, [load, project?.status, project?.reference_match_pending, project?.peaks_pending, sseHealthy]);

  React.useEffect(() => {
    if (!projectId || !project || isSettled(project)) return;
    const source = new EventSource(api.projectEventsUrl(projectId));
    source.onopen = () => setSseHealthy(true);
    source.onmessage = (event) => {
      const seq = nextSeq();
      try {
        const next = JSON.parse(event.data);
        if (shouldApplyProject(seq)) setProject(next);
      } catch { /* ignore malformed frame */ }
    };
    source.onerror = () => {
      setSseHealthy(false);
      source.close();
    };
    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on status and the pending flags only, so a mid-stream progress update doesn't reopen the connection
  }, [projectId, project?.status, project?.reference_match_pending, project?.peaks_pending]);

  // Bound to the track and layout it was called for so an undo after
  // switching track or layout still targets what the edit was made on.
  const saveTrack = React.useCallback((track: ProjectTrack, layout: string, next: Manifest) => {
    if (!projectId) return Promise.resolve();
    const seq = nextSeq();
    return api.saveProjectTrackLayout(projectId, track.id, layout, {
      manifest_overrides: {
        engine: { stems: next.engine.stems }, mixing: next.mixing, routing: next.routing,
        mastering: next.mastering, processing: next.processing, format: next.format,
      },
      scene_overrides: track.scene_overrides,
    }).then((updated) => { if (shouldApplyProject(seq)) setProject(updated); })
      .catch((reason) => setError((reason as Error).message));
  }, [projectId, nextSeq, shouldApplyProject]);

  const saveProjectTrackName = React.useCallback((trackId: string, name: string) => {
    if (!projectId) return Promise.resolve();
    const seq = nextSeq();
    return api.renameProjectTrack(projectId, trackId, name)
      .then((updated) => { if (shouldApplyProject(seq)) setProject(updated); })
      .catch((reason) => setError((reason as Error).message));
  }, [projectId, nextSeq, shouldApplyProject]);

  const saveProjectFields = React.useCallback(async (fields: Record<string, unknown>) => {
    if (!projectId || !project || !manifest) return;
    const seq = nextSeq();
    try {
      const updated = await api.saveProject(projectId, {
        ...fields,
        manifest: manifest as unknown as Record<string, unknown>,
        scene: project.scene as Record<string, unknown>,
      });
      if (shouldApplyProject(seq)) setProject(updated);
    } catch (reason) { setError((reason as Error).message); }
  }, [projectId, project, manifest, nextSeq, shouldApplyProject]);

  const retry = React.useCallback(async () => {
    if (!projectId) return;
    const seq = nextSeq();
    const updated = await api.retryProject(projectId);
    if (shouldApplyProject(seq)) setProject(updated);
  }, [projectId, nextSeq, shouldApplyProject]);

  const reprepareStems = React.useCallback(async (stems: string[], stemBleedReduction: boolean, stemEnsemble: boolean) => {
    if (!projectId) return;
    const seq = nextSeq();
    try {
      const updated = await api.reprepareProjectStems(projectId, {
        stems,
        stem_bleed_reduction: stemBleedReduction,
        stem_ensemble: stemEnsemble,
      });
      if (shouldApplyProject(seq)) setProject(updated);
    } catch (reason) {
      setError((reason as Error).message);
      throw reason;
    }
  }, [projectId, nextSeq, shouldApplyProject]);

  return {
    project, manifest, error, setError,
    applyProject, saveTrack, saveProjectTrackName, saveProjectFields, retry, reprepareStems,
  };
}
