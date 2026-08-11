import { describe, expect, it } from "vitest";
import { monitorMastering } from "./previewGraph";

describe("monitorMastering", () => {
  it("passes the mastering config through unchanged when not bypassed", () => {
    const mastering = { eq: { profile: "spatial-warm" }, loudness: { target: -16 } };
    expect(monitorMastering(mastering, false)).toBe(mastering);
  });

  it("strips every stage but loudness when bypassed", () => {
    const mastering = {
      eq: { profile: "spatial-warm" },
      compressor: { profile: "glue" },
      bass: { profile: "boost" },
      match_reference: { fir_url: "/ref.wav" },
      loudness: { normalize: true, target: -16, max_tp: -1 },
    };
    expect(monitorMastering(mastering, true)).toEqual({ loudness: mastering.loudness });
  });

  it("returns undefined when bypassed and there is no loudness block to preserve", () => {
    expect(monitorMastering({ eq: { profile: "spatial-warm" } }, true)).toBeUndefined();
    expect(monitorMastering(undefined, true)).toBeUndefined();
  });
});
