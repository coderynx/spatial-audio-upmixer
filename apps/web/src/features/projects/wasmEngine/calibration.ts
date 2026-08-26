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
  measure(weights: number[], requestId: number): Promise<Measured | null>;
  /** True while the pass is in flight, for the "calibrating" affordance. */
  onMeasuring(measuring: boolean): void;
  onProgress(progress: number): void;
};

export class LoudnessCalibration {
  /** The measurement the preview is currently correcting against. */
  measured: Measured = { lkfs: -70, dbtp: -70 };
  /** Key `measured` belongs to; playback is gated on it matching the live
   * one, so a mode the user just switched to never plays on the previous
   * mode's correction. */
  measuredKey: string | null = null;
  /** Every programme measured this session. The A/B toggle comes straight
   * back to a key it already measured, so the second press skips another
   * pass — and matching the two sides needs
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
   * same key.
   */
  async ensure(key: string, weights: number[]): Promise<void> {
    if (this.inFlightKey === key || (!this.inFlightKey && this.measuredKey === key)) return;

    const cached = this.cache.get(key);
    const token = ++this.token;
    this.inFlightKey = key;
    if (cached) {
      this.adopt(key, cached);
      this.hooks.onMeasuring(false);
      return;
    }

    this.hooks.onMeasuring(true);
    this.hooks.onProgress(0);

    const result = await this.hooks.measure(weights, token);
    // A switch mid-measurement supersedes this pass; the newer one owns the
    // measuring state from here.
    if (token !== this.token) return;

    if (result) this.adopt(key, result);
    this.hooks.onMeasuring(false);
  }

  /**
   * Take the exact whole-programme pass's refinement of the fast excerpt
   * result `ensure` already resolved with. Dropped when a newer fast pass is
   * in flight: that refinement was posted for the programme this one
   * replaces, and its result was already on the wire when the worklet
   * dropped it.
   */
  refine(result: Measured, requestId: number): boolean {
    if (!this.inFlightKey || requestId !== this.token) return false;
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
    this.cache.clear();
  }
}
