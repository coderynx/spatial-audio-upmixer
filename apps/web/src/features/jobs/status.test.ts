import { describe, expect, it } from "vitest";
import type { Job, JobTrack } from "@/api";
import { jobDelivery, jobFolds } from "./status";

function fold(overrides: Record<string, unknown> = {}) {
  return {
    lkfs: -18.9,
    tp_dbtp: -7.9,
    plr_db: 11.1,
    lkfs_delta_lu: -1.27,
    tp_compliant: true,
    loudness_divergent: false,
    ...overrides,
  };
}

function makeJob(result: Record<string, unknown> | null): Job {
  const track = { id: "t1", position: 0, status: "completed", progress: 1, result, error: null } as unknown as JobTrack;
  return { tracks: [track] } as unknown as Job;
}

describe("jobDelivery", () => {
  it("reports nothing until a track carries a measurement", () => {
    expect(jobDelivery(makeJob(null))).toBeNull();
    expect(jobDelivery(makeJob({ measured_lkfs: null }))).toBeNull();
  });

  it("passes only when neither the loudness nor the ceiling check failed", () => {
    const base = { measured_lkfs: -23, measured_tp_dbtp: -1.2, target_preset: "ebu-r128" };
    expect(jobDelivery(makeJob({ ...base, loudness_compliant: true, tp_compliant: true }))?.compliant).toBe(true);
    expect(jobDelivery(makeJob({ ...base, loudness_compliant: false, tp_compliant: true }))?.compliant).toBe(false);
    expect(jobDelivery(makeJob({ ...base, loudness_compliant: true, tp_compliant: false }))?.compliant).toBe(false);
  });

  it("claims no pass/fail for a target that publishes no tolerance", () => {
    const delivery = jobDelivery(
      makeJob({ measured_lkfs: -16, measured_tp_dbtp: -1.4, target_preset: "apple-music" }),
    );
    expect(delivery?.compliant).toBeNull();
    expect(delivery?.preset).toBe("apple-music");
  });

  it("marks a fold-referenced measurement", () => {
    expect(jobDelivery(makeJob({ measured_lkfs: -18, fold_referenced: true }))?.foldReferenced).toBe(true);
    expect(jobDelivery(makeJob({ measured_lkfs: -18 }))?.foldReferenced).toBe(false);
  });
});

describe("jobFolds", () => {
  it("reports nothing for a delivery with no fold to measure", () => {
    expect(jobFolds(makeJob(null))).toBeNull();
    expect(jobFolds(makeJob({ measured_lkfs: -18 }))).toBeNull();
    expect(jobFolds(makeJob({ folds: { native_lkfs: -18 } }))).toBeNull();
  });

  it("lists only the folds that were measured, in delivery order", () => {
    const folds = jobFolds(
      makeJob({
        folds: {
          native_lkfs: -17.68,
          stereo: fold(),
          surround_51: fold({ lkfs: -18, lkfs_delta_lu: -0.32 }),
          binaural: null,
        },
      }),
    );
    expect(folds?.rows.map((row) => row.key)).toEqual(["stereo", "surround_51"]);
    expect(folds?.nativeLkfs).toBe(-17.68);
    expect(folds?.flagged).toBe(false);
  });

  it("flags a fold over the ceiling or past the divergence threshold", () => {
    const overCeiling = jobFolds(
      makeJob({ folds: { native_lkfs: -5, stereo: fold({ tp_compliant: false, tp_dbtp: 3.55 }) } }),
    );
    expect(overCeiling?.rows[0].flagged).toBe(true);
    expect(overCeiling?.flagged).toBe(true);

    const divergent = jobFolds(
      makeJob({
        folds: { native_lkfs: -15.65, stereo: fold({ loudness_divergent: true, lkfs_delta_lu: -3.98 }) },
      }),
    );
    expect(divergent?.flagged).toBe(true);
  });
});
