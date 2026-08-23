import { describe, expect, it } from "vitest";

import { decodeMeterFrame } from "./meters";

// One stem (stereo), two bed channels, the output pair, then the master
// block — the layout `Meters::write` produces in the core.
const FRAME = {
  position: 4096,
  meters: [
    0.1, 0.2, 0.15, 0.25,
    0.3, 0.4, 0.35, 0.45,
    0.5, 0.6, 0.55, 0.65,
    -13.5, -15.5, 2.5, 1.25, 4,
  ],
  spectrum: [0.5, 0.25, 1],
};

describe("decodeMeterFrame", () => {
  it("reads the master block after the output pair", () => {
    const decoded = decodeMeterFrame(FRAME, ["Vocals"], [2], ["FL", "FR"]);
    expect(decoded.master).toEqual({
      momentaryLkfs: -13.5,
      shortTermLkfs: -15.5,
    });
    expect(decoded.headphoneLevels.left.rms).toBe(0.5);
    expect(decoded.channelLevels.get("FR")?.peak).toBe(0.45);
  });

  it("falls back to silence when a frame arrives short", () => {
    const decoded = decodeMeterFrame({ position: 0, meters: [], spectrum: [] }, [], [], []);
    expect(decoded.master).toEqual({
      momentaryLkfs: -70,
      shortTermLkfs: -70,
    });
  });
});
