import { describe, expect, it } from "vitest";
import { panWeights, routingFromAzimuthElevation, stemPan } from "./spatial";

describe("routingFromAzimuthElevation", () => {
  it("routes a rear-center stem to both rear channels", () => {
    const routing = routingFromAzimuthElevation(180, 0);
    expect(routing.BL).toBeGreaterThan(0);
    expect(routing.BR).toBeGreaterThan(0);
  });

  it("is left/right symmetric", () => {
    const left = routingFromAzimuthElevation(175, 0);
    const right = routingFromAzimuthElevation(-175, 0);
    expect(left.BL ?? 0).toBeCloseTo(right.BR ?? 0, 9);
    expect(left.BR ?? 0).toBeCloseTo(right.BL ?? 0, 9);
    expect(left.TBL ?? 0).toBeCloseTo(right.TBR ?? 0, 9);
    expect(left.TBR ?? 0).toBeCloseTo(right.TBL ?? 0, 9);
  });

  it("is constant-power at every azimuth", () => {
    for (let elevation = 0; elevation <= 35; elevation += 35) {
      for (let azimuth = -180; azimuth <= 180; azimuth += 15) {
        const routing = routingFromAzimuthElevation(azimuth, elevation);
        const power = Math.sqrt(Object.values(routing).reduce((sum, weight) => sum + weight * weight, 0));
        expect(power).toBeCloseTo(1, 6);
      }
    }
  });

  // Values produced by `placement_route(StemPlacement(az, el, 0, 60), 7.1.4)` in
  // packages/core/src/separation/stem_placement.py — the preview must weight a
  // scene position exactly as the export does.
  it("matches the core panner", () => {
    expect(routingFromAzimuthElevation(0, 0)).toEqual({
      C: expect.closeTo(0.706059, 6),
      FL: expect.closeTo(0.50074, 6),
      FR: expect.closeTo(0.50074, 6),
    });
    expect(routingFromAzimuthElevation(30, 0)).toEqual({
      C: expect.closeTo(0.575368, 6),
      FL: expect.closeTo(0.813689, 6),
      FR: expect.closeTo(0.002417, 6),
      TFL: expect.closeTo(0.082801, 6),
    });
    expect(routingFromAzimuthElevation(0, 35)).toEqual({
      C: expect.closeTo(0.122762, 6),
      TFL: expect.closeTo(0.701758, 6),
      TFR: expect.closeTo(0.701758, 6),
    });
  });

  it("wraps an out-of-range azimuth the same as its normalized form", () => {
    const wrapped = routingFromAzimuthElevation(225, 0);
    const reference = routingFromAzimuthElevation(-135, 0);
    expect(wrapped).toEqual(reference);
  });
});

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
