import { describe, expect, it } from "vitest";
import { panWeights, stemPan } from "./spatial";

describe("stereo pan law", () => {
  it("is constant power and preserves the pair magnitude", () => {
    const route = { FL: 0.65, FR: 0.65, LFE: 0.75 };
    const magnitude = Math.hypot(0.65, 0.65);
    for (const pan of [0, 0.25, 0.5, 0.75, 1]) {
      const weights = panWeights(route, pan);
      expect(Math.hypot(weights.FL, weights.FR)).toBeCloseTo(magnitude, 10);
    }
    expect(panWeights(route, 0).FR).toBeCloseTo(0, 10);
    expect(panWeights(route, 1).FL).toBeCloseTo(0, 10);
    const centre = panWeights(route, 0.5);
    expect(centre.FL).toBeCloseTo(centre.FR, 10);
  });

  it("round-trips through stemPan", () => {
    for (const pan of [0, 0.25, 0.5, 0.75, 1]) {
      expect(stemPan(panWeights({ FL: 1, FR: 1 }, pan))).toBeCloseTo(pan, 10);
    }
  });

  it("reads a silent pair as centred", () => {
    expect(stemPan({})).toBe(0.5);
    expect(stemPan({ FL: 0, FR: 0 })).toBe(0.5);
  });
});
