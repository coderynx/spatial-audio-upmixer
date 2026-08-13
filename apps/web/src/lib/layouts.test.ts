import { describe, expect, it } from "vitest";
import { deliveryTypeForLayout, isStereoLayout, outputModeForLayoutSwitch } from "./layouts";

describe("isStereoLayout", () => {
  it("is true only for the literal stereo layout", () => {
    expect(isStereoLayout("stereo")).toBe(true);
    expect(isStereoLayout("5.1")).toBe(false);
    expect(isStereoLayout(undefined)).toBe(false);
  });
});

describe("deliveryTypeForLayout", () => {
  it("retargets a non-wav delivery type back to wav for a stereo layout", () => {
    expect(deliveryTypeForLayout("stereo", "binaural")).toBe("wav");
  });

  it("leaves the delivery type alone for a multichannel layout", () => {
    expect(deliveryTypeForLayout("5.1", "binaural")).toBe("binaural");
  });
});

describe("outputModeForLayoutSwitch", () => {
  it("picks native when the device supports the switched-to layout's channel count", () => {
    expect(outputModeForLayoutSwitch(true)).toEqual({ outputMode: "native" });
  });

  it("falls back to binaural at the flat profile when the device does not", () => {
    expect(outputModeForLayoutSwitch(false)).toEqual({ outputMode: "binaural", spatialProfile: "flat" });
  });
});
