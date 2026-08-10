import { describe, expect, it } from "vitest";
import { routingFromAzimuthElevation } from "./spatial";

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

  it("wraps an out-of-range azimuth the same as its normalized form", () => {
    const wrapped = routingFromAzimuthElevation(225, 0);
    const reference = routingFromAzimuthElevation(-135, 0);
    expect(wrapped).toEqual(reference);
  });
});
