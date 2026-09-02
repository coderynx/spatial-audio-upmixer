import { describe, expect, it } from "vitest";

import {
  collapseModeLabel,
  formatLkfs,
  formatLu,
  peakToLoudness,
  peakToShortTerm,
} from "./LoudnessMeters";

describe("loudness readout formatting", () => {
  it("floors silence at -∞ rather than printing the gate", () => {
    expect(formatLkfs(-70)).toBe("-∞");
    expect(formatLkfs(-84)).toBe("-∞");
  });

  it("prints one decimal, sign included", () => {
    expect(formatLkfs(-14.23)).toBe("-14.2");
    expect(formatLkfs(-0.04)).toBe("-0.0");
  });

  it("shows a dash where a crest metric has no loudness to divide by", () => {
    expect(formatLu(peakToLoudness(-1, -70))).toBe("—");
    expect(formatLu(peakToShortTerm(-3, -70))).toBe("—");
  });
});

describe("collapse-mode label", () => {
  it("names the collapse the transport is auditioning", () => {
    expect(collapseModeLabel("stereo", 12)).toBe("Stereo fold");
    expect(collapseModeLabel("binaural", 12)).toBe("Binaural");
    expect(collapseModeLabel("transaural", 12)).toBe("Transaural");
  });

  it("labels Apple Spatial loudness as pre-PHASE", () => {
    expect(collapseModeLabel("apple_spatial", 12)).toBe("Apple Spatial · pre-PHASE 5.1");
    expect(collapseModeLabel("apple_spatial", 6)).toBe("Apple Spatial · pre-PHASE bed");
  });

  it("distinguishes a native bed from the 5.1 re-render it is measured on", () => {
    expect(collapseModeLabel("native", 12)).toBe("5.1 re-render");
    expect(collapseModeLabel("native", 8)).toBe("5.1 re-render");
    expect(collapseModeLabel("native", 6)).toBe("Native bed");
    expect(collapseModeLabel("native", 2)).toBe("Native bed");
  });
});

describe("crest metrics", () => {
  it("PLR is true peak over integrated loudness", () => {
    expect(peakToLoudness(-1, -14)).toBeCloseTo(13, 6);
  });

  it("PSR is the short-term window's peak over its loudness", () => {
    expect(peakToShortTerm(-2.5, -12.5)).toBeCloseTo(10, 6);
  });
});
