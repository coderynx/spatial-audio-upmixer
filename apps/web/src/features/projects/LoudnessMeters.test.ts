import { describe, expect, it } from "vitest";

import { formatLkfs, formatLu, peakToLoudness, peakToShortTerm } from "./LoudnessMeters";

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

describe("crest metrics", () => {
  it("PLR is true peak over integrated loudness", () => {
    expect(peakToLoudness(-1, -14)).toBeCloseTo(13, 6);
  });

  it("PSR is the short-term window's peak over its loudness", () => {
    expect(peakToShortTerm(-2.5, -12.5)).toBeCloseTo(10, 6);
  });
});
