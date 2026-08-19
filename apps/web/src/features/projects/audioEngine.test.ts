import { describe, expect, it } from "vitest";

import { withReferenceMatchParams } from "./audioEngine";
import { DEFAULT_DELIVERY_TARGET, resolveDeliveryTarget } from "./masteringProfiles";
import { TEST_ENGINE_CONSTANTS } from "./engineConstants.fixture";

const TARGETS = TEST_ENGINE_CONSTANTS.deliveryTargets;

describe("resolveDeliveryTarget", () => {
  it("falls back to the Atmos Music pair with no preset and no override", () => {
    expect(resolveDeliveryTarget(undefined, TARGETS)).toEqual(DEFAULT_DELIVERY_TARGET);
    expect(resolveDeliveryTarget({ target_preset: null }, TARGETS)).toEqual(
      DEFAULT_DELIVERY_TARGET,
    );
  });

  it("takes both numbers and the tolerance from a named target", () => {
    expect(resolveDeliveryTarget({ target_preset: "ebu-r128" }, TARGETS)).toEqual({
      target_lkfs: -23,
      max_tp_dbtp: -1,
      tolerance_lu: 0.5,
    });
  });

  it("lets an explicit field override the preset one at a time", () => {
    const resolved = resolveDeliveryTarget(
      { target_preset: "ebu-r128", target: -20 },
      TARGETS,
    );
    expect(resolved.target_lkfs).toBe(-20);
    expect(resolved.max_tp_dbtp).toBe(-1);
    expect(resolved.tolerance_lu).toBe(0.5);
  });

  it("falls back rather than guessing when the preset name is unknown", () => {
    expect(resolveDeliveryTarget({ target_preset: "atmos" }, TARGETS)).toEqual(
      DEFAULT_DELIVERY_TARGET,
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
