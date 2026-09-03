import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { Panner } from "./panner";

// Drives the shipped wasm through the same class the mix editor uses, so a
// marshalling mistake fails here rather than as a mis-panned stem in the
// browser. The panning itself is covered by packages/dsp's golden fixtures.

const WASM_PATH = resolve(process.cwd(), "public/wasm/upmixer_dsp.wasm");
const FULL = ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"];

function panner(): Panner {
  const instance = new WebAssembly.Instance(new WebAssembly.Module(readFileSync(WASM_PATH)), {});
  return new Panner(instance.exports as never);
}

describe("wasm panner", () => {
  it("marshals normalized object size", () => {
    const route = panner().placementRoute(
      { azimuth_deg: 45, elevation_deg: 20, width_deg: 60, object_size: 0.5 }, FULL, 0.25,
    );

    expect(route.LFE).toBeCloseTo(0.25, 12);
    expect(Math.hypot(...FULL.filter((channel) => channel !== "LFE").map((channel) => route[channel] ?? 0))).toBeCloseTo(1, 12);
  });

  it("keeps the gains aligned with the channel order it was given", () => {
    const placement = { azimuth_deg: 90, elevation_deg: 0, width_deg: 0, object_size: 0 };
    const forward = panner().placementRoute(placement, FULL);
    const reversed = panner().placementRoute(placement, [...FULL].reverse());

    for (const channel of FULL) {
      expect(reversed[channel] ?? 0).toBeCloseTo(forward[channel] ?? 0, 12);
    }
    expect(forward.SL).toBeGreaterThan(0);
    expect(forward.SR ?? 0).toBe(0);
  });

  it("pans at constant power", () => {
    const instance = panner();
    for (const azimuth of [0, 45, 90, 135, 180, -90]) {
      const route = instance.placementRoute(
        { azimuth_deg: azimuth, elevation_deg: 0, width_deg: 40, object_size: 0.5 }, FULL,
      );
      const power = Math.hypot(...FULL.filter((c) => c !== "LFE").map((c) => route[c] ?? 0));
      expect(power).toBeCloseTo(1, 9);
    }
  });

  it("applies bed diversity and center level in the shared core", () => {
    const instance = panner();
    const placement = {
      azimuth_deg: 0, elevation_deg: 0, width_deg: 0, object_size: 0,
      diversity: 1, center_level_db: -6,
    };
    const route = instance.placementRoute(placement, FULL);
    const gains = FULL.filter((channel) => channel !== "LFE" && channel !== "C")
      .map((channel) => route[channel]);

    expect(new Set(gains.map((gain) => gain.toFixed(12))).size).toBe(1);
    expect(route.C / gains[0]).toBeCloseTo(10 ** (-6 / 20), 12);
  });

  it("serves the preset tables the export pipeline uses", () => {
    const instance = panner();

    expect(instance.presets).toEqual(["balanced", "intimate", "stage", "wide", "immersive", "live"]);
    const balanced = instance.presetTreatments("balanced");
    expect(balanced["Lead Vocals"].placement).toEqual({
      azimuth_deg: 0, elevation_deg: 0, width_deg: 60, object_size: 0.1, diversity: 0, center_level_db: 1.5,
    });
    expect(balanced.Crowd.placement).toMatchObject({ diversity: 0.5, center_level_db: -3 });
    // The `stage` preset is the one that places instruments off-centre.
    expect(instance.presetTreatments("stage").Guitar.placement.azimuth_deg).toBe(50);
    expect(instance.presetTreatments("no-such-preset")).toEqual({});

    expect(balanced.Kick.sends.lfe).toBeCloseTo(0.82, 9);
    expect(balanced["Lead Vocals"].sends).toEqual({
      lfe: 0, rear: 0, height: 0, heightCrossoverHz: 4000,
    });
    expect(balanced.Guitar.sends.heightCrossoverHz).toBe(2000);
    expect(balanced.Crowd.sends.rear).toBeGreaterThan(balanced.Guitar.sends.rear);
    expect(instance.presetTreatments("intimate").Crowd.sends.rear)
      .toBeLessThan(instance.presetTreatments("live").Crowd.sends.rear);
  });

  it("reports the elevation a layout can reach", () => {
    const instance = panner();

    expect(instance.maxElevationDeg(FULL)).toBe(30);
    expect(instance.maxElevationDeg(["FL", "FR", "C", "LFE", "SL", "SR"])).toBe(0);
  });

  it("flattens elevation into width where there is no height pair", () => {
    const instance = panner();
    const placement = { azimuth_deg: 0, elevation_deg: 20, width_deg: 40, object_size: 0.5 };

    expect(instance.project(placement, FULL)).toEqual(placement);
    expect(instance.project(placement, ["FL", "FR", "C", "LFE", "SL", "SR"])).toEqual({
      ...placement, elevation_deg: 0, width_deg: 80,
    });
  });

  it("rejects a channel name the core has no position for", () => {
    expect(() => panner().placementRoute(
      { azimuth_deg: 0, elevation_deg: 0, width_deg: 0, object_size: 0 }, ["FL", "XX"],
    )).toThrow(/Unknown channel/);
  });
});
