import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { TEST_ENGINE_CONSTANTS, TEST_SERVED_CONSTANTS } from "../engineConstants.fixture";
import { buildEngineParams, type BuildEngineParamsInput } from "./engineParams";

const constants = TEST_ENGINE_CONSTANTS;

function input(overrides: Partial<BuildEngineParamsInput> = {}): BuildEngineParamsInput {
  return {
    constants,
    layoutChannels: ["FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR", "TBL", "TBR"],
    stems: [{ id: "Vocals", routing: { FL: 0.7, FR: 0.7, C: 0.5, LFE: 0.1 } }],
    outputMode: "binaural",
    spatialProfile: "studio",
    transauralProfile: "stereo",
    ...overrides,
  };
}

describe("buildEngineParams", () => {
  it("orders speakers as the layout does and marks LFE", () => {
    const params = buildEngineParams(input());
    const speakers = params.speakers as { name: string }[];
    expect(speakers.map((s) => s.name)).toEqual([
      "FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR", "TBL", "TBR",
    ]);
    expect(params.lfe_index).toBe(3);
  });

  it("takes ambisonic angles from the server rather than deriving them", () => {
    const params = buildEngineParams(input());
    const speakers = params.speakers as { name: string; azimuth_rad: number }[];
    const fl = speakers.find((s) => s.name === "FL");
    expect(fl?.azimuth_rad).toBe(TEST_SERVED_CONSTANTS.speaker_directions.FL.azimuth_rad);
  });

  it("applies channel group gains and excludes height and LFE from the downmix", () => {
    const params = buildEngineParams(input());
    const speakers = params.speakers as { name: string; group_gain: number; downmix: unknown }[];
    const by = (name: string) => speakers.find((s) => s.name === name)!;

    expect(by("FL").group_gain).toBe(1);
    expect(by("C").group_gain).toBe(constants.channelGains.center);
    expect(by("SL").group_gain).toBe(constants.channelGains.surround);
    expect(by("TFL").group_gain).toBe(constants.channelGains.height);

    expect(by("C").downmix).toEqual([constants.ituCenterCoeff, constants.ituCenterCoeff]);
    expect(by("TFL").downmix).toBeNull();
    expect(by("LFE").downmix).toBeNull();
  });

  it("keeps LFE routing weights even though LFE has no shaped send", () => {
    const params = buildEngineParams(input());
    const stems = params.stems as { routing: [string, number][] }[];
    expect(stems[0].routing).toContainEqual(["LFE", 0.1]);
  });

  it("drops routing to channels the layout does not have", () => {
    const params = buildEngineParams(
      input({ layoutChannels: ["FL", "FR", "LFE"], stems: [{ id: "V", routing: { FL: 1, TBR: 0.5 } }] }),
    );
    const stems = params.stems as { routing: [string, number][] }[];
    expect(stems[0].routing.map(([name]) => name)).toEqual(["FL"]);
  });

  it("pairs only the stereo pairs the layout actually has", () => {
    const wide = buildEngineParams(input()).master as { stereo_pairs: number[][] };
    // FL/FR, SL/SR, TFL/TFR, TBL/TBR — but no BL/BR in this layout.
    expect(wide.stereo_pairs).toHaveLength(4);

    const narrow = buildEngineParams(input({ layoutChannels: ["FL", "FR", "LFE"] })).master as {
      stereo_pairs: number[][];
    };
    expect(narrow.stereo_pairs).toEqual([[0, 1]]);
  });

  it("arms the look-ahead limiter only on the native path", () => {
    const native = buildEngineParams(input({ outputMode: "native" })).master as {
      limiter: unknown;
    };
    expect(native.limiter).not.toBeNull();
    expect(buildEngineParams(input()).soft_limit_threshold).toBe(constants.softLimitThreshold);
    expect(buildEngineParams(input({ outputMode: "native" })).soft_limit_threshold).toBe(0);
  });

  it("selects the transaural voicing chain for the transaural mode", () => {
    const binaural = buildEngineParams(input()).voicing as { stereo_widen: number };
    const transaural = buildEngineParams(input({ outputMode: "transaural" })).voicing as {
      stereo_widen: number;
    };
    expect(binaural.stereo_widen).toBe(constants.voicingParams.studio.stereoWiden);
    expect(transaural.stereo_widen).toBe(constants.transauralVoicingParams.stereo.stereoWiden);
  });

  it("resolves mastering profiles into the core's parameter shape", () => {
    const params = buildEngineParams(
      input({ master: { compProfile: "glue", bassProfile: "enhance" } }),
    ).master as { compressor: { ratio: number }; bass: { excite_drive: number } };

    expect(params.compressor.ratio).toBe(constants.compProfiles.glue.ratio);
    expect(params.bass.excite_drive).toBe(constants.exciteDrive);
  });

  it("produces a block the core accepts", () => {
    const wasmPath = resolve(process.cwd(), "public/wasm/upmixer_dsp.wasm");
    const wasm = new WebAssembly.Instance(new WebAssembly.Module(readFileSync(wasmPath)))
      .exports as unknown as {
      memory: WebAssembly.Memory;
      dsp_alloc: (bytes: number) => number;
      dsp_free: (ptr: number, bytes: number) => void;
      dsp_engine_new: (sr: number, ptr: number, len: number) => number;
      dsp_engine_free: (engine: number) => void;
    };

    const json = JSON.stringify(buildEngineParams(input()));
    const encoded = new TextEncoder().encode(json);
    const ptr = wasm.dsp_alloc(encoded.length);
    new Uint8Array(wasm.memory.buffer, ptr, encoded.length).set(encoded);
    const engine = wasm.dsp_engine_new(48000, ptr, encoded.length);
    wasm.dsp_free(ptr, encoded.length);

    expect(engine).not.toBe(0);
    wasm.dsp_engine_free(engine);
  });
});
