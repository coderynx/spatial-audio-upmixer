import { describe, expect, it } from "vitest";
import { defaultManifest, normalizeManifest } from "./manifest";

describe("normalizeManifest", () => {
  it("fills missing nested defaults without losing supplied values", () => {
    const manifest = normalizeManifest({
      engine: { mode: "realtime" },
      mixing: { spatial: { intensity: 0.35 } },
    });
    expect(manifest.engine.mode).toBe("realtime");
    expect(manifest.engine.stems).toEqual(defaultManifest.engine.stems);
    expect(manifest.mixing.spatial).toEqual({
      profile: "auto",
      intensity: 0.35,
      preanalyze: true,
    });
  });
});
