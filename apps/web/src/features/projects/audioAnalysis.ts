export function loudnessGainFor(measuredLkfs: number, targetLkfs: number, maxGainDb: number): number {
  if (measuredLkfs <= -70) return 1;
  const gainDb = Math.min(targetLkfs - measuredLkfs, maxGainDb);
  return 10 ** (gainDb / 20);
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

/** One programme's BS.1770 measurement: integrated loudness and true peak. */
export type Measured = {
  lkfs: number;
  dbtp: number;
  monitorLkfs?: number;
  monitorDbtp?: number;
};
export type DeliveryTarget = { target_lkfs: number; max_tp_dbtp: number };

/** The correction gain a measurement asks for: the loudness move, then the
 * ceiling's veto over it. Unity when normalization is off. */
export function correctionGain(
  measured: Measured,
  delivery: DeliveryTarget,
  maxGainDb: number,
  normalize: boolean,
): number {
  if (!normalize) return 1;
  const gain = loudnessGainFor(measured.lkfs, delivery.target_lkfs, maxGainDb);
  return applyTruePeakCeiling(measured.dbtp, gain, delivery.max_tp_dbtp);
}

/**
 * Monitor gain, in dB, that puts a bypassed programme at the mastered one's
 * delivered loudness, so the A/B compares tone and dynamics rather than
 * level.
 *
 * Both sides are normalized as far as their own true-peak ceiling allows —
 * the unmastered side has no limiter, so its ceiling clamp usually bites
 * first and leaves it quieter. What is left over is the difference between
 * the two *delivered* loudnesses, which is what this closes. Zero until both
 * measurements exist, so a caller with only one shows its calibrating state
 * rather than monitoring an unmatched pair.
 */
export function bypassMatchDb(
  mastered: Measured | undefined,
  bypassed: Measured | undefined,
  delivery: DeliveryTarget,
  maxGainDb: number,
  normalize: boolean,
): number {
  if (!mastered || !bypassed) return 0;
  const delivered = (m: Measured) =>
    m.lkfs + 20 * Math.log10(correctionGain(m, delivery, maxGainDb, normalize));
  return delivered(mastered) - delivered(bypassed);
}
