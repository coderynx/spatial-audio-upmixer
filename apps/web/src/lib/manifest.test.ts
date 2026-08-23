import { describe, expect, it } from "vitest";
import { defaultManifest, normalizeManifest } from "./manifest";

describe("normalizeManifest", () => {
  it("fills missing nested defaults without losing supplied values", () => {
    const manifest = normalizeManifest({
      engine: { mode: "stem" },
      mixing: { stem_source_anchor_strength: 0.35 },
    });
    expect(manifest.engine.mode).toBe("stem");
    expect(manifest.engine.stems).toEqual(defaultManifest.engine.stems);
    expect(manifest.mixing.stem_source_anchor_strength).toBe(0.35);
    expect(manifest.mixing.stem_routing).toEqual(defaultManifest.mixing.stem_routing);
  });
});
