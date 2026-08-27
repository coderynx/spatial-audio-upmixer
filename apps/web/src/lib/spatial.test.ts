import { describe, expect, it } from "vitest";
import { speakerCoordinates, stemPan } from "./spatial";

describe("stereo pan readback", () => {
  it("reads a silent pair as centred", () => {
    expect(stemPan({})).toBe(0.5);
    expect(stemPan({ FL: 0, FR: 0 })).toBe(0.5);
  });
});

describe("speaker coordinates", () => {
  it("uses the BS.2051 reference directions", () => {
    const azimuth = (channel: string) => Math.atan2(-speakerCoordinates[channel].x, -speakerCoordinates[channel].z) * 180 / Math.PI;
    const elevation = (channel: string) => Math.asin(speakerCoordinates[channel].y / Math.hypot(...Object.values(speakerCoordinates[channel]))) * 180 / Math.PI;

    expect(azimuth("FL")).toBeCloseTo(30);
    expect(azimuth("SL")).toBeCloseTo(110);
    expect(azimuth("BL")).toBeCloseTo(135);
    expect(azimuth("TFL")).toBeCloseTo(45);
    expect(azimuth("TBL")).toBeCloseTo(135);
    expect(elevation("TFL")).toBeCloseTo(30);
    expect(elevation("TBL")).toBeCloseTo(30);
  });
});
