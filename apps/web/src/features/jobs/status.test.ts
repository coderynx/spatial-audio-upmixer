import { describe, expect, it } from "vitest";
import type { Job, JobTrack } from "@/api";
import { jobDelivery } from "./status";

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
