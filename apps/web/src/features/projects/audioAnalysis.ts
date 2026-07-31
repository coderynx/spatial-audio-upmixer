// See docs/web_architecture.md "Preview audio graph" — Offline pre-playback analysis.
export const CORRECTION_STEP_MS = 16;

export const ANALYSIS_MAX_SECONDS = 10;
export const ANALYSIS_EXCERPT_COUNT = 5;

export type AnalysisExcerpt = { offlineStart: number; originalOffset: number; duration: number };

export function buildAnalysisExcerpts(durationSeconds: number): { excerpts: AnalysisExcerpt[]; totalSeconds: number } {
  if (durationSeconds <= ANALYSIS_MAX_SECONDS) {
    return { excerpts: [{ offlineStart: 0, originalOffset: 0, duration: durationSeconds }], totalSeconds: durationSeconds };
  }
  const segmentSeconds = ANALYSIS_MAX_SECONDS / ANALYSIS_EXCERPT_COUNT;
  const excerpts: AnalysisExcerpt[] = [];
  for (let i = 0; i < ANALYSIS_EXCERPT_COUNT; i++) {
    const center = (durationSeconds * (i + 0.5)) / ANALYSIS_EXCERPT_COUNT;
    const originalOffset = Math.max(0, Math.min(durationSeconds - segmentSeconds, center - segmentSeconds / 2));
    excerpts.push({ offlineStart: i * segmentSeconds, originalOffset, duration: segmentSeconds });
  }
  return { excerpts, totalSeconds: ANALYSIS_MAX_SECONDS };
}

export function loudnessGainFor(measuredLkfs: number, targetLkfs: number, maxGainDb: number): number {
  if (measuredLkfs <= -70) return 1;
  const gainDb = Math.min(targetLkfs - measuredLkfs, maxGainDb);
  return 10 ** (gainDb / 20);
}

// See docs/web_architecture.md "Preview audio graph" — Clip detection.
export const CLIP_TOLERANCE = 10 ** (0.5 / 20); // +0.5dB
export function isClippedPeak(peak: number): boolean {
  return peak > CLIP_TOLERANCE;
}

/** Second-stage gain reduction mirroring `normalize_loudness`'s
 * `max_tp_dbtp` correction (`upmixer/loudness.py`): given the gain
 * `loudnessGainFor` above already computed, reduce it further if applying
 * it would push the measured pre-gain true peak (`preGainTpDbtp`) over
 * `maxTpDbtp`. Returns the final gain to apply (folds `loudnessGain` in,
 * not just the extra reduction) — a no-op (`loudnessGain` unchanged) when
 * already under the ceiling. Exported (pure, no AudioContext) so this
 * exact formula is unit-testable without a live graph. */
export function applyTruePeakCeiling(preGainTpDbtp: number, loudnessGain: number, maxTpDbtp: number): number {
  const postGainTpDbtp = preGainTpDbtp + 20 * Math.log10(loudnessGain);
  if (postGainTpDbtp <= maxTpDbtp) return loudnessGain;
  return loudnessGain * 10 ** ((maxTpDbtp - postGainTpDbtp) / 20);
}
