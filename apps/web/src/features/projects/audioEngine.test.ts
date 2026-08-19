import { describe, expect, it } from "vitest";

import { withReferenceMatchParams } from "./audioEngine";
import { resolveDeliveryTarget } from "./masteringProfiles";
import { TEST_ENGINE_CONSTANTS } from "./engineConstants.fixture";

const TARGETS = TEST_ENGINE_CONSTANTS.deliveryTargets;
const FALLBACK = TEST_ENGINE_CONSTANTS.deliveryDefault;

describe("resolveDeliveryTarget", () => {
  it("uses the served default with no preset named and no override set", () => {
    expect(resolveDeliveryTarget(undefined, TARGETS, FALLBACK)).toEqual(FALLBACK);
    expect(resolveDeliveryTarget({ target_preset: null }, TARGETS, FALLBACK)).toEqual(FALLBACK);
  });

  it("takes both numbers and the tolerance from a named target", () => {
    expect(resolveDeliveryTarget({ target_preset: "ebu-r128" }, TARGETS, FALLBACK)).toEqual({
      target_lkfs: -23,
      max_tp_dbtp: -1,
      tolerance_lu: 0.5,
    });
  });

  it("lets an explicit field override the preset one at a time", () => {
    const resolved = resolveDeliveryTarget(
      { target_preset: "ebu-r128", target: -20 },
      TARGETS,
      FALLBACK,
    );
    expect(resolved.target_lkfs).toBe(-20);
    expect(resolved.max_tp_dbtp).toBe(-1);
    expect(resolved.tolerance_lu).toBe(0.5);
  });

  it("falls back rather than guessing when the preset name is unknown", () => {
    expect(resolveDeliveryTarget({ target_preset: "atmos" }, TARGETS, FALLBACK)).toEqual(
      FALLBACK,
    );
  });
});

describe("withReferenceMatchParams", () => {
  it("appends strength/max_db as the first query params", () => {
    expect(withReferenceMatchParams("/api/v1/projects/1/reference-match/fir", 0.5, 4)).toBe(
      "/api/v1/projects/1/reference-match/fir?strength=0.5&max_db=4",
    );
  });

  it("appends with & when the base url already carries a query param", () => {
    expect(withReferenceMatchParams("/fir?v=2", 1, 6)).toBe("/fir?v=2&strength=1&max_db=6");
  });
});
