// Loudness calibration state for the preview: which programme has been
// measured, which pass is in flight, and what every measured programme this
// session came out at.
//
// Framework-free and engine-free by design — it owns no audio, only the
// bookkeeping around `DspEngineClient.measure` — so the rules that decide
// when the transport is allowed to play (and when the A/B has both sides it
// needs to match them) are unit-testable without a graph.

import type { Measured } from "../audioAnalysis";

export type CalibrationHooks = {
  /** Run the measurement pass over whatever the engine currently renders. */
  measure(weights: number[]): Promise<Measured | null>;
  /** Push the parameter block, so the pass measures — and the result lands
   * on — the current mix. */
  apply(): void;
  /** True while the pass is in flight, for the "calibrating" affordance. */
  onMeasuring(measuring: boolean): void;
  onProgress(progress: number): void;
  /** Pause a running transport; returns whether it was actually playing, so
   * the caller knows to resume once the pass lands. */
  pause(): boolean;
  resume(): void;
};

export class LoudnessCalibration {
  /** The measurement the preview is currently correcting against. */
  measured: Measured = { lkfs: -70, dbtp: -70 };
  /** Key `measured` belongs to; playback is gated on it matching the live
   * one, so a mode the user just switched to never plays on the previous
   * mode's correction. */
  measuredKey: string | null = null;
  /** While set, the engine renders uncorrected so the pass sees the raw
   * programme. */
  raw = false;

  /** Every programme measured this session. The A/B toggle comes straight
   * back to a key it already measured, so the second press must not pay for
   * a pass (and its pause) all over again — and matching the two sides needs
   * both measurements at once regardless. */
  private readonly cache = new Map<string, Measured>();
  /** Key the in-flight pass — and the exact whole-programme refinement that
   * follows it — belongs to. */
  private inFlightKey: string | null = null;
  private token = 0;

  constructor(private readonly hooks: CalibrationHooks) {}

  get(key: string): Measured | undefined {
    return this.cache.get(key);
  }

  /** Whether `key` is measured and safe to play. */
  covers(key: string): boolean {
    return this.measuredKey === key;
  }

  /**
   * Bring `key` up to date, measuring it if this session never has.
   *
   * A pass already in flight owns `key`'s measurement too when it is for the
   * same key: while `raw` is set, `measuredKey` still names the programme
   * being replaced, so a switch back to it is not "already calibrated".
   */
  async ensure(key: string, weights: number[]): Promise<void> {
    const covered = this.raw ? this.inFlightKey : this.measuredKey;
    if (covered === key) return;

    const cached = this.cache.get(key);
    if (cached && !this.raw) {
      this.adopt(key, cached);
      this.hooks.apply();
      return;
    }

    const wasPlaying = this.hooks.pause();
    const token = ++this.token;
    this.inFlightKey = key;
    this.hooks.onMeasuring(true);
    this.hooks.onProgress(0);
    this.raw = true;
    this.hooks.apply();

    const result = await this.hooks.measure(weights);
    // A switch mid-measurement supersedes this pass; the newer one owns the
    // measuring state from here.
    if (token !== this.token) return;

    if (result) this.adopt(key, result);
    this.raw = false;
    this.hooks.onMeasuring(false);
    this.hooks.apply();
    if (wasPlaying) this.hooks.resume();
  }

  /**
   * Take the exact whole-programme pass's refinement of the fast excerpt
   * result `ensure` already resolved with. Dropped when a newer fast pass is
   * in flight: that refinement was posted for the programme this one
   * replaces, and its result was already on the wire when the worklet
   * dropped it.
   */
  refine(result: Measured): boolean {
    if (!this.inFlightKey || this.raw) return false;
    this.adopt(this.inFlightKey, result);
    return true;
  }

  private adopt(key: string, result: Measured) {
    this.measured = result;
    this.measuredKey = key;
    this.cache.set(key, result);
  }

  reset() {
    this.measured = { lkfs: -70, dbtp: -70 };
    this.measuredKey = null;
    this.inFlightKey = null;
    this.token += 1;
    this.raw = false;
    this.cache.clear();
  }
}
