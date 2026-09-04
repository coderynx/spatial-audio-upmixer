import * as React from "react";
import type { ProjectTrack, StemRouting } from "@/api";
import { normalizeManifest, type Manifest } from "@/lib/manifest";
import { useEditHistory } from "./useEditHistory";
import { loadPanner, NEUTRAL_PLACEMENT, type Panner, type StemPlacement } from "./wasmEngine/panner";

const COMMIT_DEBOUNCE_MS = 350;

type Draft = { track: ProjectTrack; layout: string; value: Manifest; failed: boolean };

export function resolveTrackLayoutManifest(
  projectManifest: Manifest | null,
  track: ProjectTrack | null,
  layout: string,
): Manifest | null {
  if (!projectManifest || !track) return projectManifest;
  const overrides = track.layout_overrides[layout] as Partial<Manifest> | undefined;
  return normalizeManifest({
    ...projectManifest,
    ...overrides,
    engine: { ...projectManifest.engine, ...overrides?.engine },
    mixing: { ...projectManifest.mixing, ...overrides?.mixing, channel_layout: layout },
    routing: { ...projectManifest.routing, ...overrides?.routing },
    mastering: { ...projectManifest.mastering, ...overrides?.mastering },
    processing: { ...projectManifest.processing, ...overrides?.processing },
    format: { ...projectManifest.format, ...overrides?.format },
  });
}

export function useTrackLayoutRealization({
  projectId,
  projectManifest,
  track,
  layout,
  channels,
  save,
  onError,
}: {
  projectId: string | undefined;
  projectManifest: Manifest | null;
  track: ProjectTrack | null;
  layout: string;
  channels: string[];
  save: (track: ProjectTrack, layout: string, manifest: Manifest) => Promise<void>;
  onError: (message: string | null) => void;
}) {
  const key = track ? `${projectId}:${track.id}:${layout}` : null;
  const serverManifest = React.useMemo(
    () => resolveTrackLayoutManifest(projectManifest, track, layout),
    [projectManifest, track, layout],
  );
  const [drafts, setDrafts] = React.useState<Record<string, Draft>>({});
  const draftsRef = React.useRef(drafts);
  draftsRef.current = drafts;
  const timers = React.useRef(new Map<string, number>());
  const history = useEditHistory(projectId, key ?? undefined);
  const { record } = history;
  const [panner, setPanner] = React.useState<Panner | null>(null);

  React.useEffect(() => {
    let live = true;
    loadPanner().then((loaded) => { if (live) setPanner(loaded); }).catch((reason) => onError((reason as Error).message));
    return () => { live = false; };
  }, [onError]);

  const replaceDrafts = React.useCallback((next: Record<string, Draft>) => {
    draftsRef.current = next;
    setDrafts(next);
  }, []);
  const updateDraft = React.useCallback((draftKey: string, update: (current: Draft | undefined) => Draft | undefined) => {
    const next = { ...draftsRef.current };
    const value = update(next[draftKey]);
    if (value) next[draftKey] = value;
    else delete next[draftKey];
    replaceDrafts(next);
  }, [replaceDrafts]);

  const commit = React.useCallback(async (draftKey: string) => {
    const draft = draftsRef.current[draftKey];
    if (!draft) return;
    try {
      await save(draft.track, draft.layout, draft.value);
      updateDraft(draftKey, (current) => current?.value === draft.value ? undefined : current);
    } catch (reason) {
      updateDraft(draftKey, (current) => current && { ...current, failed: true });
      onError((reason as Error).message);
    }
  }, [onError, save, updateDraft]);

  const flush = React.useCallback((draftKey: string | null) => {
    if (!draftKey) return;
    const timer = timers.current.get(draftKey);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timers.current.delete(draftKey);
    }
    void commit(draftKey);
  }, [commit]);

  const flushRef = React.useRef(flush);
  flushRef.current = flush;
  React.useEffect(() => () => flushRef.current(key), [key]);

  const manifest = key ? drafts[key]?.value ?? serverManifest : serverManifest;
  const update = React.useCallback((next: Manifest, merge = false) => {
    if (!key || !track || !manifest) return;
    const apply = (value: Manifest) => {
      updateDraft(key, () => ({ track, layout, value, failed: false }));
      const prior = timers.current.get(key);
      if (prior !== undefined) window.clearTimeout(prior);
      timers.current.set(key, window.setTimeout(() => {
        timers.current.delete(key);
        void commit(key);
      }, COMMIT_DEBOUNCE_MS));
    };
    record(manifest, next, apply, merge);
  }, [commit, key, layout, manifest, record, track, updateDraft]);

  const placementFor = React.useCallback((stem: string, preset: string): StemPlacement =>
    (manifest?.mixing.stem_placement[stem] as StemPlacement | undefined)
    ?? panner?.presetTreatments(preset)[stem.split("@", 1)[0]]?.placement
    ?? NEUTRAL_PLACEMENT,
  [manifest, panner]);

  const setPlacement = React.useCallback((stem: string, placement: StemPlacement) => {
    if (!manifest || !panner) return;
    const routing = manifest.mixing.stem_routing as StemRouting;
    update({
      ...manifest,
      mixing: {
        ...manifest.mixing,
        stem_placement: { ...manifest.mixing.stem_placement, [stem]: placement },
        stem_routing: { ...routing, [stem]: panner.placementRoute(placement, channels, routing[stem]?.LFE ?? 0) },
      },
    }, true);
  }, [channels, manifest, panner, update]);

  const applyPreset = React.useCallback((preset: string, stems: string[]) => {
    if (!manifest || !panner || !stems.length) return;
    const treatments = panner.presetTreatments(preset);
    const placements: Record<string, StemPlacement> = {};
    const routing: StemRouting = {};
    const rear = { ...manifest.mixing.stem_ambient_rear };
    const height = { ...manifest.mixing.stem_ambient_height };
    const crossover = { ...manifest.mixing.stem_ambient_height_crossover_hz };
    for (const stem of stems) {
      const treatment = treatments[stem.split("@", 1)[0]];
      if (!treatment) continue;
      placements[stem] = treatment.placement;
      routing[stem] = panner.placementRoute(treatment.placement, channels, treatment.sends.lfe);
      rear[stem] = treatment.sends.rear;
      height[stem] = treatment.sends.height;
      crossover[stem] = treatment.sends.heightCrossoverHz;
    }
    update({ ...manifest, mixing: { ...manifest.mixing, stem_placement: placements, stem_routing: routing, stem_ambient_rear: rear, stem_ambient_height: height, stem_ambient_height_crossover_hz: crossover } });
  }, [channels, manifest, panner, update]);

  const retry = React.useCallback(() => { if (key) void commit(key); }, [commit, key]);
  const discard = React.useCallback(() => {
    if (!key) return;
    const timer = timers.current.get(key);
    if (timer !== undefined) window.clearTimeout(timer);
    timers.current.delete(key);
    updateDraft(key, () => undefined);
    history.clear();
    onError(null);
  }, [history, key, onError, updateDraft]);
  const undo = React.useCallback(() => {
    history.undo();
    flush(key);
  }, [flush, history, key]);
  const redo = React.useCallback(() => {
    history.redo();
    flush(key);
  }, [flush, history, key]);

  return {
    manifest,
    update,
    history: { ...history, undo, redo },
    placementFor,
    setPlacement,
    applyPreset,
    maxElevationDeg: panner && channels.length ? panner.maxElevationDeg(channels) : 0,
    saveFailed: Boolean(key && drafts[key]?.failed),
    hasUncommittedChanges: Boolean(key && drafts[key]),
    retry,
    discard,
  };
}
