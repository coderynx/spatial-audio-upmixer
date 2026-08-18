import { describe, expect, it } from "vitest";
import { duckedFraction, panWeights, stemPan } from "./spatial";

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

describe("ducked fraction", () => {
  it("counts only the surround and height sends the duck touches", () => {
    expect(duckedFraction({ FL: 1, FR: 1, C: 0.5 })).toBe(0);
    expect(duckedFraction({ SL: 0.4, TFL: 0.6 })).toBe(1);
    expect(duckedFraction({ FL: 1, FR: 1, SL: 0.5, TFR: 0.5 })).toBeCloseTo(1 / 3, 10);
  });

  it("ignores LFE, non-positive weights, and an unrouted stem", () => {
    expect(duckedFraction({ FL: 1, LFE: 3, SL: 1 })).toBeCloseTo(0.5, 10);
    expect(duckedFraction({ FL: 1, SL: -1 })).toBe(0);
    expect(duckedFraction({})).toBe(0);
  });
});
