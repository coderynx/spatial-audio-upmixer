// Framework-free playback clock/scheduler for the preview engine
// (audioEngine.ts) — the "audio clock" half of the two-clocks split (see
// Chris Wilson's "A Tale of Two Clocks"): playback position and every
// source's start instant are derived from `AudioContext.currentTime`, never
// from `setTimeout`/wall-clock time, so a stalled main thread (a big React
// re-render, GC) never desyncs the transport from what the listener hears.

export type Timeline = { offset: number; contextTime: number };

// The clock only ever reads `currentTime` off the context — narrowed to this
// structural shape (rather than the full `BaseAudioContext`) so tests can
// exercise the scheduling math against a plain fake clock instead of a full
// Web Audio context stub.
export type AudioClock = { readonly currentTime: number };

// Base lookahead: how far into the future every source in a play/seek pass
// shares a single start instant. Must comfortably exceed how long it takes
// to construct and `.start()` every `AudioBufferSourceNode` in the pass —
// if it doesn't, sources scheduled later in the same pass can find their
// shared instant already elapsed by the time their own `.start()` call
// lands, starting them immediately instead of in step with the ones
// already scheduled. 80ms is generous for the common case (a handful of
// stems).
const BASE_LOOKAHEAD_SECONDS = 0.08;

// Ceiling for the adaptive margin — bounds the worst case (many stems, or
// sustained heavy jank) so a play/seek never feels sluggish to respond.
const MAX_LOOKAHEAD_SECONDS = 0.5;

// Applied to the *previous* pass's measured construction time when picking
// the next lookahead margin: a pass that took Xms under load is evidence
// the next one might too (the same re-render or GC pressure often
// recurs), so pad proportionally instead of trusting a single fixed
// constant forever.
const LOOKAHEAD_SAFETY_FACTOR = 3;

/**
 * Owns the "where is playback right now" clock and the adaptive lookahead
 * margin every stem's `AudioBufferSourceNode.start()` call is scheduled
 * against. Pure bookkeeping — it never touches an `AudioNode` itself, so
 * `audioEngine.ts`'s `startSourcesAt` stays the only place that actually
 * builds/starts sources; this class only decides *when* they should start
 * and reports *where* playback is once they have.
 */
export class TransportClock {
  private timeline: Timeline | null = null;
  private loop = false;
  private duration = 0;
  private lookaheadSeconds = BASE_LOOKAHEAD_SECONDS;

  setLoop(loop: boolean) {
    this.loop = loop;
  }

  setDuration(duration: number) {
    this.duration = duration;
  }

  isRunning(): boolean {
    return this.timeline !== null;
  }

  clear() {
    this.timeline = null;
  }

  // Current playback position derived from the audio clock, given the
  // timeline recorded by the last `commit()`. `fallback` is returned as-is
  // when nothing is currently scheduled (paused/stopped) — the caller's own
  // last-known position.
  position(ctx: AudioClock, fallback: number): number {
    const timeline = this.timeline;
    if (!timeline) return fallback;
    const elapsed = timeline.offset + (ctx.currentTime - timeline.contextTime);
    if (this.loop && this.duration > 0) {
      const wrapped = elapsed % this.duration;
      return wrapped < 0 ? 0 : wrapped;
    }
    return Math.max(0, this.duration > 0 ? Math.min(this.duration, elapsed) : elapsed);
  }

  // Reserves the audio-clock instant every source scheduled in this pass
  // should share. Call once, before constructing/starting any source, and
  // pass the same returned instant to every `source.start(...)` call.
  reserveStart(ctx: AudioClock): number {
    return ctx.currentTime + this.lookaheadSeconds;
  }

  // Records `reservedAt` (the value `reserveStart` returned) as the new
  // timeline origin for `target` (the buffer offset playback started from),
  // and measures how long this pass actually took to construct so the next
  // `reserveStart` can pad for it if construction is trending slow.
  // `passStartedAt` is the `ctx.currentTime` sampled immediately before
  // `reserveStart` was called.
  commit(ctx: AudioClock, target: number, reservedAt: number, passStartedAt: number) {
    this.timeline = { offset: target, contextTime: reservedAt };
    const constructionSeconds = ctx.currentTime - passStartedAt;
    this.lookaheadSeconds = Math.min(
      MAX_LOOKAHEAD_SECONDS,
      Math.max(BASE_LOOKAHEAD_SECONDS, constructionSeconds * LOOKAHEAD_SAFETY_FACTOR),
    );
  }
}

export { BASE_LOOKAHEAD_SECONDS, MAX_LOOKAHEAD_SECONDS };
