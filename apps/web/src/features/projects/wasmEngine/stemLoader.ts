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

export type StemLoadHooks = {
  isCurrent: () => boolean;
  onProgress: (fraction: number) => void;
  onDuration: (seconds: number) => void;
};

/** Decode every previewable stem and push it into the engine, in order. */
export async function loadStemsInto(
  context: AudioContext,
  client: DspEngineClient,
  sources: ProjectStem[],
  hooks: StemLoadHooks,
): Promise<number> {
  if (!sources.length) {
    hooks.onProgress(1);
    return 0;
  }

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
  for (let start = 0; start < sources.length; start += STEM_DECODE_CONCURRENCY) {
    const chunk = sources.slice(start, start + STEM_DECODE_CONCURRENCY);
    const buffers = await Promise.all(
      chunk.map((stem) => loadBuffer(context, (stem.preview_url || stem.audio_url)!)),
    );
    if (!hooks.isCurrent()) return duration;

    for (const buffer of buffers) {
      // `.slice()` is a memcpy of the channel view; `Float32Array.from` takes
      // V8's generic per-element iterator path over the same bytes. The copies
      // are transferred, so they leave the main thread with the call below.
      const left = buffer.getChannelData(0).slice();
      const right = buffer.getChannelData(Math.min(1, buffer.numberOfChannels - 1)).slice();
      client.addStem(left, right);
      duration = Math.max(duration, buffer.duration);
      hooks.onDuration(duration);
    }

    decoded += chunk.length;
    scheduleProgressFlush();
  }
  hooks.onProgress(1);
  return duration;
}
