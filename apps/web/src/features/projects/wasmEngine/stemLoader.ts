import type { ProjectStem } from "@/api";
import { loadBuffer } from "../audioLoaders";
import type { DspEngineClient } from "./engineClient";

/**
 * Stems decode concurrently but must reach the engine in project order (its
 * stem index is push order — see `push_stem` in stream/engine/), so a stem
 * that finishes decoding out of turn is held in memory until its turn comes.
 * Bounding the batch caps that retained set: a 5-minute 48 kHz stereo stem is
 * ~115 MB decoded, so unbounded parallelism risks holding every stem at once.
 */
const STEM_DECODE_CONCURRENCY = 3;
// ponytail: leading-window preview; add seekable chunks for full-length playback.
const PREVIEW_PCM_BUDGET_BYTES = 512 * 1024 * 1024;

export function previewWindowFrames(
  stemCount: number,
  pcmBudgetBytes = PREVIEW_PCM_BUDGET_BYTES,
): number {
  return Math.max(1, Math.floor(pcmBudgetBytes / Math.max(1, stemCount) / 2 / Float32Array.BYTES_PER_ELEMENT));
}

export type StemLoadHooks = {
  isCurrent: () => boolean;
  onProgress: (fraction: number) => void;
  onDuration: (seconds: number) => void;
  onPreviewLimited: (seconds: number | null) => void;
};

/** Decode every previewable stem and push it into the engine, in order. */
export async function loadStemsInto(
  context: AudioContext,
  client: DspEngineClient,
  sources: ProjectStem[],
  hooks: StemLoadHooks,
  pcmBudgetBytes = PREVIEW_PCM_BUDGET_BYTES,
): Promise<number> {
  if (!sources.length) {
    hooks.onProgress(1);
    return 0;
  }

  const frameLimit = previewWindowFrames(sources.length, pcmBudgetBytes);

  // Stems finish decoding in tight clusters, not evenly spaced — flushing
  // progress straight from each completion would fire a full page re-render
  // per stem in that cluster, right when the main thread is busiest with
  // decode work. Coalesce same-frame completions instead.
  let decoded = 0;
  let progressFlushScheduled = false;
  const scheduleProgressFlush = () => {
    if (progressFlushScheduled) return;
    progressFlushScheduled = true;
    window.requestAnimationFrame(() => {
      progressFlushScheduled = false;
      if (!hooks.isCurrent()) return;
      hooks.onProgress(decoded / sources.length);
    });
  };

  let duration = 0;
  let decodedIndex = 0;
  const pending = new Map<number, Promise<AudioBuffer>>();
  const schedule = () => {
    while (pending.size < STEM_DECODE_CONCURRENCY && decodedIndex < sources.length) {
      const stem = sources[decodedIndex];
      const buffer = loadBuffer(context, (stem.preview_url || stem.audio_url)!);
      void buffer.catch(() => {});
      pending.set(decodedIndex, buffer);
      decodedIndex += 1;
    }
  };
  schedule();

  for (let index = 0; index < sources.length; index += 1) {
    const buffer = await pending.get(index)!;
    pending.delete(index);
    if (!hooks.isCurrent()) return duration;

    const frames = Math.min(frameLimit, buffer.length);
    const left = buffer.getChannelData(0).subarray(0, frames).slice();
    const right = buffer.getChannelData(Math.min(1, buffer.numberOfChannels - 1)).subarray(0, frames).slice();
    client.addStem(left, right);
    duration = Math.max(duration, frames / buffer.sampleRate);
    hooks.onDuration(duration);
    if (buffer.length > frameLimit) hooks.onPreviewLimited(frameLimit / buffer.sampleRate);
    schedule();

    decoded += 1;
    scheduleProgressFlush();
  }
  hooks.onProgress(1);
  return duration;
}
