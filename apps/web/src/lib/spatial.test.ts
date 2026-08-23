import { describe, expect, it } from "vitest";
import { stemPan } from "./spatial";

describe("stereo pan readback", () => {
  it("reads a silent pair as centred", () => {
    expect(stemPan({})).toBe(0.5);
    expect(stemPan({ FL: 0, FR: 0 })).toBe(0.5);
  });
});
