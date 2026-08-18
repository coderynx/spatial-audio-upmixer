import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { TEST_ENGINE_CONSTANTS, TEST_SERVED_CONSTANTS } from "../engineConstants.fixture";
import { resolveBassParams } from "../masteringProfiles";
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
  it("prefers the track's send values over the served defaults", () => {
    const served = buildEngineParams(input()).sends as Record<string, number>;
    expect(served.stem_transient_duck).toBe(constants.stemTransientDuck);
    expect(served.height_directional_band_gain).toBe(constants.heightShaping.directionalBandGain);

    // A track that carries its own values must preview with them, or the
    // export ducks where the preview did not.
    const overridden = buildEngineParams(
      input({ sendOverrides: { stemTransientDuck: 0.7, heightDirectionalBandGain: 1.6 } }),
    ).sends as Record<string, number>;
    expect(overridden.stem_transient_duck).toBe(0.7);
    expect(overridden.height_directional_band_gain).toBe(1.6);
  });

  it("keeps the served default when the track sets no value", () => {
    const partial = buildEngineParams(input({ sendOverrides: {} })).sends as Record<string, number>;
    expect(partial.stem_transient_duck).toBe(constants.stemTransientDuck);
    expect(partial.height_directional_band_gain).toBe(constants.heightShaping.directionalBandGain);
  });

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

  it("carries a speaker mute as its own flag, leaving the group gain alone", () => {
    // Folding the mute into `group_gain` took the channel out of the shared
    // bass bus and the linked compressor, so muting one speaker changed
    // every other one — see the core's monitor mute in `PreviewEngine::render`.
    const params = buildEngineParams({ ...input(), speakerEnabled: { C: false } });
    const speakers = params.speakers as { name: string; group_gain: number; muted: boolean }[];
    const by = (name: string) => speakers.find((s) => s.name === name)!;

    expect(by("C").muted).toBe(true);
    expect(by("C").group_gain).toBe(constants.channelGains.center);
    expect(by("SL").muted).toBe(false);
  });

  it("applies channel group gains, folds heights into the downmix, excludes LFE", () => {
    const params = buildEngineParams(input());
    const speakers = params.speakers as { name: string; group_gain: number; downmix: unknown }[];
    const by = (name: string) => speakers.find((s) => s.name === name)!;

    expect(by("FL").group_gain).toBe(1);
    expect(by("C").group_gain).toBe(constants.channelGains.center);
    expect(by("SL").group_gain).toBe(constants.channelGains.surround);
    expect(by("TFL").group_gain).toBe(constants.channelGains.height);

    expect(by("C").downmix).toEqual([constants.ituCenterCoeff, constants.ituCenterCoeff]);
    expect(by("TFL").downmix).toEqual([constants.heightDownmixCoeff, 0]);
    expect(by("TBR").downmix).toEqual([
      0,
      constants.heightDownmixCoeff * constants.surroundDownmixCoeff,
    ]);
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

  it("spreads the low end only over channels the layout actually has", () => {
    const bass = { ...constants.bassProfiles.deep, lfe_mode: "off" as const, lfe_send: 0 };
    const wide = buildEngineParams(input({ master: { bass } })).master as {
      lf_targets: [number, number][];
    };
    // FL/FR/C/SL/SR — but no BL/BR in this layout.
    expect(wide.lf_targets).toHaveLength(5);
    const total = wide.lf_targets.reduce((sum, [, weight]) => sum + weight, 0);
    expect(total).toBeCloseTo(1, 12);

    const narrow = buildEngineParams(input({ layoutChannels: ["FL", "FR", "LFE"], master: { bass } }))
      .master as { lf_targets: [number, number][] };
    expect(narrow.lf_targets).toEqual([
      [0, 0.5],
      [1, 0.5],
    ]);
  });

  it("folds the LFE authoring gain into a split send", () => {
    const bass = { ...constants.bassProfiles.cinema };
    const params = buildEngineParams(input({ master: { bass } })).master as {
      lf_targets: [number, number][];
    };
    const lfeIndex = 3;
    const lfe = params.lf_targets.find(([i]) => i === lfeIndex);
    expect(lfe?.[1]).toBeCloseTo(bass.lfe_send * constants.lfeGain, 12);
    // Mains keep what the LFE did not take, so the coherent level survives
    // playback's +10 dB replay gain.
    const bed = params.lf_targets.filter(([i]) => i !== lfeIndex);
    const bedTotal = bed.reduce((sum, [, weight]) => sum + weight, 0);
    expect(bedTotal).toBeCloseTo(1 - bass.lfe_send, 12);
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
      input({
        master: { comp: constants.compProfiles.glue, bass: constants.bassProfiles.enhance },
      }),
    ).master as {
      compressor: { ratio: number };
      bass: { excite_drive: number; punch: number; unify_hz: number };
    };

    expect(params.compressor.ratio).toBe(constants.compProfiles.glue.ratio);
    expect(params.bass.excite_drive).toBe(constants.exciteDrive);
    expect(params.bass.punch).toBe(constants.bassProfiles.enhance.punch);
    expect(params.bass.unify_hz).toBe(constants.bassProfiles.enhance.unify_hz);
  });

  it("forwards per-field overrides, not just the profile preset", () => {
    // The preview used to send the bare preset, so a moved pot changed the
    // export and not what the user heard (ledger D30).
    const overridden = resolveBassParams(
      { profile: "enhance", punch: -0.4, unify_hz: 55 },
      constants.bassProfiles,
    )!;
    const params = buildEngineParams(input({ master: { bass: overridden } })).master as {
      bass: { punch: number; unify_hz: number; excite: boolean };
    };

    expect(params.bass.punch).toBe(-0.4);
    expect(params.bass.unify_hz).toBe(55);
    // Untouched fields still come from the profile.
    expect(params.bass.excite).toBe(constants.bassProfiles.enhance.excite);
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
