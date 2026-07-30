import { describe, expect, it } from "vitest";
import { parseTrackPeaks } from "./useTrackPeaks";

function envelope(blocks: number[][][], bins: number) {
  const bytes = new Int8Array(blocks.length * bins * 2);
  blocks.forEach((block, index) => {
    for (let bin = 0; bin < bins; bin += 1) {
      bytes[index * bins * 2 + bin * 2] = block[bin][0];
      bytes[index * bins * 2 + bin * 2 + 1] = block[bin][1];
    }
  });
  return bytes.buffer;
}

describe("parseTrackPeaks", () => {
  it("slices one block per stem in the declared order", () => {
    const buffer = envelope([
      [[-10, 20], [-30, 40]],
      [[-1, 2], [-3, 4]],
    ], 2);

    const peaks = parseTrackPeaks(buffer, ["Vocals", "Drums"], 2, 90);

    expect(peaks.bins).toBe(2);
    expect(peaks.duration).toBe(90);
    expect([...peaks.stems.keys()]).toEqual(["Vocals", "Drums"]);
    expect([...peaks.stems.get("Vocals")!.min]).toEqual([-10, -30]);
    expect([...peaks.stems.get("Vocals")!.max]).toEqual([20, 40]);
    expect([...peaks.stems.get("Drums")!.min]).toEqual([-1, -3]);
  });

  it("keys stems by their base name so variant stems match the routing convention", () => {
    const peaks = parseTrackPeaks(envelope([[[-5, 5]]], 1), ["Vocals@htdemucs"], 1, 10);

    expect([...peaks.stems.keys()]).toEqual(["Vocals"]);
  });

  it("stops at the last complete block rather than reading past the payload", () => {
    const peaks = parseTrackPeaks(envelope([[[-5, 5]]], 1), ["Vocals", "Drums"], 1, 10);

    expect(peaks.stems.size).toBe(1);
    expect(peaks.stems.has("Drums")).toBe(false);
  });

  it("preserves stem names containing spaces", () => {
    const peaks = parseTrackPeaks(envelope([[[-5, 5]]], 1), ["Backing Vocals"], 1, 10);

    expect([...peaks.stems.keys()]).toEqual(["Backing Vocals"]);
  });
});
