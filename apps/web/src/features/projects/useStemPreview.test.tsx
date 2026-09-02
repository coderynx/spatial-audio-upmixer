import * as React from "react";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectStem } from "@/api";
import { applyTruePeakCeiling, useStemPreview } from "./useStemPreview";
import { TEST_ENGINE_CONSTANTS } from "./engineConstants.fixture";

// The preview's DSP now runs in the shared Rust core inside the worklet, so
// there is no node graph left to inspect here. What this file covers is the
// binding: that the hook resolves the project's mix into the core's parameter
// block, drives the transport, and surfaces what comes back. The DSP itself is
// covered by packages/dsp; the parameter mapping by engineParams.test.ts; the
// ABI by dspWasm.test.ts.

const sentParams: Record<string, unknown>[] = [];
const transportCalls: { playing?: boolean; loop?: boolean }[] = [];
const seekCalls: number[] = [];
const addedStems: number[] = [];
const measureCalls: number[] = [];
const measureRequestIds: number[] = [];
const measureWeights: number[][] = [];
const contextCalls: string[] = [];
const clientChannels: number[] = [];
// Order matters, not just counts: a measurement forks the engine as it stands,
// so the profile's filter set has to be in it first.
const callOrder: string[] = [];
let capturedCallbacks: Record<string, ((...args: never[]) => void) | undefined> = {};
// Lets a test hold `measure()` open to assert playback stays gated while
// calibration is still in flight; null (the default) resolves immediately.
let measureGate: Promise<void> | null = null;
let measureResult: { lkfs: number; dbtp: number; monitorLkfs?: number; monitorDbtp?: number } = {
  lkfs: -18,
  dbtp: -2,
};

vi.mock("./wasmEngine/engineClient", () => ({
  DspEngineClient: {
    create: vi.fn(async (_ctx: unknown, channels: number, callbacks: Record<string, never>) => {
      clientChannels.push(channels);
      capturedCallbacks = callbacks;
      return {
        node: { connect: (target: unknown) => target, disconnect: () => {} },
        ready: Promise.resolve("0.1.0"),
        setParams: (params: Record<string, unknown>) => sentParams.push(params),
        updateParams: (params: Record<string, unknown>) => sentParams.push(params),
        setTransport: (state: { playing?: boolean; loop?: boolean }) => transportCalls.push(state),
        seek: (frame: number) => seekCalls.push(frame),
        start: (frame: number) => {
          seekCalls.push(frame);
          transportCalls.push({ playing: true });
        },
        measure: async (weights: number[], requestId: number) => {
          measureCalls.push(measureCalls.length);
          measureRequestIds.push(requestId);
          measureWeights.push(weights);
          callOrder.push("measure");
          if (measureGate) await measureGate;
          return measureResult;
        },
        setDecodeTaps: () => callOrder.push("decodeTaps"),
        setXtcTaps: () => callOrder.push("xtcTaps"),
        addStem: (left: Float32Array) => {
          addedStems.push(left.length);
          callOrder.push("stem");
        },
        dispose: () => {},
      };
    }),
  },
}));

vi.mock("./wasmEngine/filterAssets", () => ({
  loadDecodeTaps: vi.fn(async () => new Float64Array(32)),
  loadXtcTaps: vi.fn(async () => new Float64Array(8)),
  loadFirTaps: vi.fn(async () => new Float64Array(4)),
}));

class FakeGain {
  gain = { value: 1, setTargetAtTime: (v: number) => { this.gain.value = v; } };
  connect(target: unknown) {
    return target;
  }
  disconnect() {}
}

class FakeAudioContext {
  state = "running";
  currentTime = 0;
  destination = { maxChannelCount: 12 };
  audioWorklet = { addModule: async () => {} };
  createGain() {
    return new FakeGain();
  }
  async resume() {
    contextCalls.push("resume");
    this.state = "running";
  }
  async suspend() {
    contextCalls.push("suspend");
    this.state = "suspended";
  }
  async close() {
    contextCalls.push("close");
  }
  async decodeAudioData() {
    return {
      duration: 2,
      numberOfChannels: 2,
      getChannelData: () => new Float32Array(96000),
    };
  }
}

const STEMS: ProjectStem[] = [
  { id: "a", stem_key: "Vocals", preview_url: "/stems/a.wav", channels: 2 } as ProjectStem,
  { id: "b", stem_key: "Bass", preview_url: "/stems/b.wav", channels: 1 } as ProjectStem,
];

function Harness(props: Record<string, unknown>) {
  const preview = useStemPreview(
    (props.stems as ProjectStem[]) ?? STEMS,
    { stems: {} },
    (props.noManifest ? undefined : props.mix ?? {}) as never,
    null,
    props.mastering as never,
    (props.layoutChannels as string[]) ?? ["FL", "FR", "C", "LFE", "SL", "SR"],
    (props.outputMode as never) ?? "binaural",
    (props.spatialProfile as never) ?? "studio",
    (props.transauralProfile as never) ?? "stereo",
    (props.constants ?? TEST_ENGINE_CONSTANTS) as never,
  );
  (globalThis as Record<string, unknown>).preview = preview;
  return null;
}

async function renderPreview(props: Record<string, unknown> = {}) {
  const result = render(<Harness {...props} />);
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  return result;
}

beforeEach(() => {
  sentParams.length = 0;
  transportCalls.length = 0;
  seekCalls.length = 0;
  addedStems.length = 0;
  measureCalls.length = 0;
  measureRequestIds.length = 0;
  measureWeights.length = 0;
  contextCalls.length = 0;
  clientChannels.length = 0;
  callOrder.length = 0;
  measureGate = null;
  measureResult = { lkfs: -18, dbtp: -2 };
  (window as unknown as { AudioContext: unknown }).AudioContext = FakeAudioContext;
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (globalThis as Record<string, unknown>).preview;
});

describe("useStemPreview parameter binding", () => {
  it("sends one speaker entry per layout channel, LFE included", async () => {
    await renderPreview();
    const params = sentParams.at(-1) as { speakers: { name: string }[]; lfe_index: number };
    expect(params.speakers.map((s) => s.name)).toEqual(["FL", "FR", "C", "LFE", "SL", "SR"]);
    expect(params.lfe_index).toBe(3);
  });

  it("carries each stem's routing and rebalance through to the core", async () => {
    await renderPreview({
      mix: {
        stem_routing: { Vocals: { FL: 0.8, FR: 0.8 }, Bass: { C: 1 } },
        stem_rebalance: { Vocals: -3 },
      },
    });
    const params = sentParams.at(-1) as {
      stems: { routing: [string, number][]; rebalance_db: number }[];
    };
    expect(params.stems[0].routing).toEqual([["FL", 0.8], ["FR", 0.8]]);
    expect(params.stems[0].rebalance_db).toBeCloseTo(-3, 6);
    expect(params.stems[1].routing).toEqual([["C", 1]]);
  });

  it("disables the stems solo excludes rather than zeroing their routing", async () => {
    await renderPreview({ mix: { stem_solo: ["Bass"] } });
    const params = sentParams.at(-1) as { stems: { enabled: boolean }[] };
    expect(params.stems[0].enabled).toBe(false);
    expect(params.stems[1].enabled).toBe(true);
  });

  it("resolves the mastering profiles the manifest names", async () => {
    await renderPreview({
      mastering: { compressor: { profile: "glue" }, bass: { profile: "enhance" } },
    });
    const params = sentParams.at(-1) as {
      master: { compressor: { ratio: number } | null; bass: { excite: boolean } | null };
    };
    expect(params.master.compressor?.ratio).toBe(TEST_ENGINE_CONSTANTS.compProfiles.glue.ratio);
    expect(params.master.bass?.excite).toBe(true);
  });

  it("leaves the mastering stages unset when the manifest names no profiles", async () => {
    await renderPreview();
    const params = sentParams.at(-1) as {
      master: { compressor: unknown; bass: unknown };
    };
    expect(params.master.compressor).toBeNull();
    expect(params.master.bass).toBeNull();
  });

  it("solos multiple speakers without losing the previous mute state", async () => {
    await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      toggleSpeaker: (channel: string) => void;
      soloSpeaker: (channel: string) => void;
      speakerEnabled: Record<string, boolean>;
      speakerSolo: ReadonlySet<string>;
    };
    await act(async () => {
      preview.toggleSpeaker("FR");
    });
    await act(async () => {
      preview.soloSpeaker("FL");
    });
    const speakers = (sentParams.at(-1) as { speakers: { name: string; muted: boolean }[] }).speakers;
    const muted = (channel: string) => speakers.find((speaker) => speaker.name === channel)?.muted;
    expect(muted("FL")).toBe(false);
    expect(muted("FR")).toBe(true);
    expect(muted("C")).toBe(true);
    const soloState = (globalThis as unknown as Record<string, unknown>).preview as typeof preview;
    expect(soloState.speakerEnabled.C).toBe(true);
    expect(soloState.speakerSolo).toEqual(new Set(["FL"]));

    await act(async () => {
      preview.soloSpeaker("C");
    });
    const multiSolo = (sentParams.at(-1) as { speakers: { name: string; muted: boolean }[] }).speakers;
    expect(multiSolo.find((speaker) => speaker.name === "FL")?.muted).toBe(false);
    expect(multiSolo.find((speaker) => speaker.name === "C")?.muted).toBe(false);
    expect(multiSolo.find((speaker) => speaker.name === "FR")?.muted).toBe(true);

    await act(async () => {
      preview.soloSpeaker("FL");
      preview.soloSpeaker("C");
    });
    const restored = (sentParams.at(-1) as { speakers: { name: string; muted: boolean }[] }).speakers;
    expect(restored.find((speaker) => speaker.name === "FR")?.muted).toBe(true);
    expect(restored.find((speaker) => speaker.name === "C")?.muted).toBe(false);
  });

  it("arms the look-ahead limiter before every output path", async () => {
    await renderPreview({ outputMode: "native" });
    const native = sentParams.at(-1) as { master: { limiter: unknown }; soft_limit_threshold: number };
    expect(native.master.limiter).not.toBeNull();
    expect(native.soft_limit_threshold).toBe(0);
    await renderPreview();
    const binaural = sentParams.at(-1) as { master: { limiter: unknown }; soft_limit_threshold: number };
    expect(binaural.master.limiter).not.toBeNull();
    expect(binaural.soft_limit_threshold).toBe(TEST_ENGINE_CONSTANTS.softLimitThreshold);
  });
});

describe("useStemPreview transport", () => {
  it("hands the stems to the core and reports the programme length", async () => {
    await renderPreview();
    expect(addedStems).toHaveLength(2);
    const preview = (globalThis as unknown as Record<string, unknown>).preview as { duration: number };
    expect(preview.duration).toBe(2);
  });

  it("starts and stops through the core rather than rebuilding a graph", async () => {
    await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      playPause: () => Promise<void>;
    };
    await act(async () => {
      await preview.playPause();
    });
    expect(transportCalls.at(-1)).toMatchObject({ playing: true });
    expect(contextCalls).toEqual(["resume"]);
  });

  it("restarts a playing seek in one worklet command", async () => {
    await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      playPause: () => Promise<void>;
      seek: (t: number) => Promise<void>;
    };
    await act(async () => {
      await preview.playPause();
    });
    contextCalls.length = 0;
    await act(async () => {
      await preview.seek(1);
    });

    expect(transportCalls.at(-1)).toMatchObject({ playing: true });
    expect(contextCalls).toEqual([]);
  });

  it("seeks in frames at the pinned 48 kHz context rate", async () => {
    await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      seek: (t: number) => Promise<void>;
    };
    await act(async () => {
      await preview.seek(1);
    });
    expect(seekCalls.at(-1)).toBe(48000);
  });
});

describe("useStemPreview initialization", () => {
  it("waits for a manifest before creating the preview engine", async () => {
    const { DspEngineClient } = await import("./wasmEngine/engineClient");
    const create = DspEngineClient.create as ReturnType<typeof vi.fn>;
    const calls = create.mock.calls.length;
    await renderPreview({ noManifest: true });
    expect(create).toHaveBeenCalledTimes(calls);
  });

  it("closes the previous context when constants are replaced", async () => {
    const result = await renderPreview();
    await act(async () => {
      result.rerender(<Harness constants={{ ...TEST_ENGINE_CONSTANTS }} />);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(contextCalls).toContain("close");
  });

  it("re-routes a layout change without reloading its stems", async () => {
    const { DspEngineClient } = await import("./wasmEngine/engineClient");
    const result = await renderPreview({ layoutChannels: ["FL", "FR"] });
    const creates = (DspEngineClient.create as ReturnType<typeof vi.fn>).mock.calls.length;
    const loadedStems = addedStems.length;

    await act(async () => {
      result.rerender(<Harness layoutChannels={["FL", "FR", "C", "LFE", "SL", "SR"]} />);
      await Promise.resolve();
    });

    expect((DspEngineClient.create as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(creates);
    expect(addedStems).toHaveLength(loadedStems);
    expect(clientChannels.at(-1)).toBe(12);
    expect((sentParams.at(-1) as { speakers: { name: string }[] }).speakers.map((speaker) => speaker.name))
      .toEqual(["FL", "FR", "C", "LFE", "SL", "SR"]);
  });
});

describe("useStemPreview metering", () => {
  it("splits the core's level block into stems, channels, and the output pair", async () => {
    await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      stemLevels: { current: Map<string, { rms: number }[]> };
      stemDynamics: { current: Map<string, number> };
      channelLevels: { current: Map<string, { rms: number }> };
      headphoneLevels: { current: { left: { rms: number }; right: { rms: number } } };
      stemSpectrum: { current: Map<string, { level: number; centroid: number }> };
      playPause: () => Promise<void>;
    };

    // Two stems (each a left/right pair), their reductions, six channels, one output pair —
    // two floats each.
    const meters = [
      0.1, 0.2, 0.15, 0.25,
      0.3, 0.4, 0.35, 0.45,
      2.5, 1.25,
      1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
      0.7, 0.8, 0.9, 1.0,
    ];
    // One [level, centroid] pair per stem.
    const spectrum = [0.5, 0.6, 0.2, 0.3];
    await act(async () => {
      await preview.playPause();
    });
    act(() => {
      capturedCallbacks.onFrame?.({ position: 48000, meters, spectrum } as never);
    });

    const vocals = preview.stemLevels.current.get("Vocals");
    expect(vocals?.[0].rms).toBeCloseTo(0.1, 6);
    expect(vocals?.[1].rms).toBeCloseTo(0.15, 6);

    const bass = preview.stemLevels.current.get("Bass");
    expect(bass?.[0].rms).toBeCloseTo(0.3, 6);
    expect(bass).toHaveLength(1);
    expect(preview.stemDynamics.current.get("Vocals")).toBe(2.5);

    expect(preview.channelLevels.current.get("FL")?.rms).toBe(1);
    expect(preview.channelLevels.current.get("SR")?.rms).toBe(6);
    expect(preview.headphoneLevels.current.left.rms).toBeCloseTo(0.7, 6);
    expect(preview.headphoneLevels.current.right.rms).toBeCloseTo(0.9, 6);

    expect(preview.stemSpectrum.current.get("Vocals")).toEqual({ level: 0.5, centroid: 0.6 });
    expect(preview.stemSpectrum.current.get("Bass")).toEqual({ level: 0.2, centroid: 0.3 });
  });

  it("zeroes every meter's target on pause, since the worklet stops reporting", async () => {
    await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      stemLevels: { current: Map<string, { rms: number }[]> };
      channelLevels: { current: Map<string, { rms: number }> };
      headphoneLevels: { current: { left: { rms: number }; right: { rms: number } } };
      stemSpectrum: { current: Map<string, { level: number; centroid: number }> };
      playPause: () => Promise<void>;
    };

    const meters = [
      0.1, 0.2, 0.15, 0.25,
      0.3, 0.4, 0.35, 0.45,
      1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
      0.7, 0.8, 0.9, 1.0,
    ];
    const spectrum = [0.5, 0.6, 1, 0.2, 0.3, 0.4];
    await act(async () => {
      await preview.playPause();
    });
    act(() => {
      capturedCallbacks.onFrame?.({ position: 48000, meters, spectrum } as never);
    });
    await act(async () => {
      await preview.playPause();
    });

    for (const level of preview.stemLevels.current.get("Vocals") ?? []) expect(level.rms).toBe(0);
    expect(preview.channelLevels.current.get("FL")?.rms).toBe(0);
    expect(preview.headphoneLevels.current.left.rms).toBe(0);
    expect(preview.headphoneLevels.current.right.rms).toBe(0);
    expect(preview.stemSpectrum.current.get("Vocals")?.level).toBe(0);
  });

  it("ignores a queued frame that arrives after stopping", async () => {
    await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      stemSpectrum: { current: Map<string, { level: number; centroid: number }> };
      playPause: () => Promise<void>;
      stop: () => void;
    };

    await act(async () => {
      await preview.playPause();
    });
    act(() => {
      capturedCallbacks.onFrame?.({
        position: 48000,
        meters: [],
        spectrum: [0.8, 0.4, 0, 0],
      } as never);
    });
    expect(preview.stemSpectrum.current.get("Vocals")?.level).toBe(0.8);

    act(() => preview.stop());
    act(() => {
      capturedCallbacks.onFrame?.({
        position: 49000,
        meters: [],
        spectrum: [0.9, 0.5, 0, 0],
      } as never);
    });

    expect(preview.stemSpectrum.current.get("Vocals")?.level).toBe(0);
  });
});

describe("useStemPreview loudness calibration", () => {
  it("weights a native bed per channel and a collapsed one as a pair", async () => {
    // BS.1770: LFE excluded, side surrounds +1.5 dB. A collapse mode delivers
    // two unity-weighted channels whatever the bed behind it was.
    await renderPreview({ outputMode: "native" });
    expect(measureWeights.at(-1)).toEqual([1, 1, 1, 0, 1.41, 1.41]);

    await renderPreview({ outputMode: "binaural" });
    expect(measureWeights.at(-1)).toEqual([1, 1]);
  });

  it("calibrates to a named delivery target the manifest never spells out", async () => {
    // The manifest carries only the preset name — the numbers live in the
    // served table. Measured -18 LKFS against ebu-r128's -23 is -5 dB of
    // correction; falling back to the -18 default would leave this at unity
    // and the preview would play 5 dB louder than the bounce.
    await renderPreview({
      mastering: { loudness: { normalize: true, target_preset: "ebu-r128" } },
    });
    const params = sentParams.at(-1) as { master: { output_gain: number } };
    expect(params.master.output_gain).toBeCloseTo(10 ** (-5 / 20), 6);
  });

  it("lets an explicit target override the delivery target it sits under", async () => {
    await renderPreview({
      mastering: { loudness: { normalize: true, target_preset: "ebu-r128", target: -18 } },
    });
    const params = sentParams.at(-1) as { master: { output_gain: number } };
    expect(params.master.output_gain).toBeCloseTo(1, 6);
  });

  it("corrects an object renderer after preserving the speaker-master gain", async () => {
    measureResult = { lkfs: -20, dbtp: -6, monitorLkfs: -10, monitorDbtp: -2 };
    await renderPreview({
      mix: {
        stem_object_mode: { Vocals: "linked-stereo" },
        stem_placement: {
          Vocals: { azimuth_deg: 0, elevation_deg: 30, width_deg: 30, object_size: 0 },
        },
      },
      mastering: { loudness: { normalize: true, target: -18 } },
    });
    const params = sentParams.at(-1) as {
      master: { output_gain: number; monitor_output_gain: number };
    };
    expect(20 * Math.log10(params.master.output_gain)).toBeCloseTo(2, 6);
    expect(20 * Math.log10(params.master.monitor_output_gain)).toBeCloseTo(-10, 6);
  });

  it("re-measures when the spatial profile changes, not just the output mode", async () => {
    const result = await renderPreview({ spatialProfile: "studio" });
    expect(measureCalls).toHaveLength(1);

    await act(async () => {
      result.rerender(<Harness spatialProfile="listening" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(measureCalls).toHaveLength(2);
  });

  it("re-measures each downmix-lock state once", async () => {
    const result = await renderPreview({ mix: { spatial_downmix_lock: false } });
    expect(measureCalls).toHaveLength(1);

    await act(async () => {
      result.rerender(<Harness mix={{ spatial_downmix_lock: true }} />);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(measureCalls).toHaveLength(2);

    await act(async () => {
      result.rerender(<Harness mix={{ spatial_downmix_lock: false }} />);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(measureCalls).toHaveLength(2);
  });

  it("loads the new profile's decode filter set before it re-measures", async () => {
    const result = await renderPreview({ spatialProfile: "studio" });
    callOrder.length = 0;

    await act(async () => {
      result.rerender(<Harness spatialProfile="listening" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    // Measuring first would calibrate "listening" against the decode bank
    // "studio" left in the engine.
    expect(callOrder.indexOf("decodeTaps")).toBeGreaterThanOrEqual(0);
    expect(callOrder.indexOf("decodeTaps")).toBeLessThan(callOrder.indexOf("measure"));
  });

  it("does not reinstall spatial banks for an ordinary mix edit", async () => {
    const result = await renderPreview({
      outputMode: "transaural",
      mix: { stem_rebalance: { Vocals: 0 } },
    });
    callOrder.length = 0;

    await act(async () => {
      result.rerender(<Harness outputMode="transaural" mix={{ stem_rebalance: { Vocals: -3 } }} />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(callOrder).not.toContain("decodeTaps");
    expect(callOrder).not.toContain("xtcTaps");
  });

  it("loads the new XTC filter set before it re-measures", async () => {
    const result = await renderPreview({ outputMode: "transaural", transauralProfile: "stereo" });
    callOrder.length = 0;

    await act(async () => {
      result.rerender(<Harness outputMode="transaural" transauralProfile="car" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(callOrder.indexOf("xtcTaps")).toBeGreaterThanOrEqual(0);
    expect(callOrder.indexOf("xtcTaps")).toBeLessThan(callOrder.indexOf("measure"));
  });

  it("uses the flat decode bank for transaural regardless of the binaural profile", async () => {
    await renderPreview({
      outputMode: "transaural",
      spatialProfile: "listening",
      layoutChannels: ["FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR", "TBL", "TBR"],
    });
    const { loadDecodeTaps } = await import("./wasmEngine/filterAssets");
    expect((loadDecodeTaps as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[1]).toBe("flat_o3_decode_5_1_4");
  });

  it.each([
    ["5.1", ["FL", "FR", "C", "LFE", "SL", "SR"]],
    ["7.1", ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR"]],
    ["5.1.2", ["FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR"]],
  ])("loads the measured decode bank for the %s bed", async (_layout, channels) => {
    await renderPreview({ layoutChannels: channels });
    const { loadDecodeTaps } = await import("./wasmEngine/filterAssets");
    expect((loadDecodeTaps as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[1]).toBe(
      `studio_o3_decode_${String(_layout).replace(/\./g, "_")}`,
    );
  });

  it("keeps the profile-only bank as a fallback when a layout is unknown", async () => {
    await renderPreview({ layoutChannels: ["FL", "FR", "UNKNOWN"] });
    const { loadDecodeTaps } = await import("./wasmEngine/filterAssets");
    expect((loadDecodeTaps as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[1]).toBe("studio_o3_decode");
  });

  it("loads the selected profile and layout decode bank", async () => {
    await renderPreview({
      spatialProfile: "listening",
      layoutChannels: ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"],
    });
    const { loadDecodeTaps } = await import("./wasmEngine/filterAssets");
    expect((loadDecodeTaps as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[1]).toBe("listening_o3_decode_7_1_4");
  });

  it("reloads the decode bank when the bed layout changes", async () => {
    const result = await renderPreview({
      layoutChannels: ["FL", "FR", "C", "LFE", "SL", "SR", "TFL", "TFR", "TBL", "TBR"],
    });
    const { loadDecodeTaps } = await import("./wasmEngine/filterAssets");
    const loader = loadDecodeTaps as ReturnType<typeof vi.fn>;
    const loaded = loader.mock.calls.length;

    await act(async () => {
      result.rerender(<Harness layoutChannels={["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"]} />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(loader.mock.calls.length).toBeGreaterThan(loaded);
    expect(loader.mock.calls.at(-1)?.[1]).toBe("studio_o3_decode_7_1_4");
  });

  it("surfaces a required transaural filter failure", async () => {
    const { loadXtcTaps } = await import("./wasmEngine/filterAssets");
    (loadXtcTaps as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("XTC unavailable"));

    await renderPreview({ outputMode: "transaural" });

    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      error: string | null;
      ready: boolean;
    };
    expect(preview.error).toBe("XTC unavailable");
    expect(preview.ready).toBe(false);
    expect(callOrder).not.toContain("decodeTaps");
  });

  it("retries a failed spatial load after a transaural profile change", async () => {
    const { loadXtcTaps } = await import("./wasmEngine/filterAssets");
    const loader = loadXtcTaps as ReturnType<typeof vi.fn>;
    loader.mockRejectedValue(new Error("XTC unavailable"));
    const result = await renderPreview({ outputMode: "transaural", transauralProfile: "stereo" });
    callOrder.length = 0;
    loader.mockResolvedValue(new Float64Array(8));

    await act(async () => {
      result.rerender(<Harness outputMode="transaural" transauralProfile="car" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      error: string | null;
      ready: boolean;
    };
    expect(preview.error).toBeNull();
    expect(preview.ready).toBe(true);
    expect(callOrder).toContain("decodeTaps");
    expect(callOrder).toContain("xtcTaps");
  });

  it("waits for pending stems before recovering a failed initial spatial load", async () => {
    const { loadXtcTaps } = await import("./wasmEngine/filterAssets");
    (loadXtcTaps as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("XTC unavailable"));
    let releaseStems: ((data: ArrayBuffer) => void) | null = null;
    const stemData = new Promise<ArrayBuffer>((resolve) => { releaseStems = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      arrayBuffer: async () => url.startsWith("/stems/") ? stemData : new ArrayBuffer(8),
    })));

    const result = await renderPreview({ outputMode: "transaural", transauralProfile: "stereo" });
    await act(async () => {
      result.rerender(<Harness outputMode="transaural" transauralProfile="car" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    let preview = (globalThis as unknown as Record<string, unknown>).preview as {
      ready: boolean;
    };
    expect(preview.ready).toBe(false);
    expect(measureCalls).toHaveLength(0);

    await act(async () => {
      releaseStems!(new ArrayBuffer(8));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    preview = (globalThis as unknown as Record<string, unknown>).preview as { ready: boolean };
    expect(preview.ready).toBe(true);
    expect(measureCalls).toHaveLength(1);
  });

  it("follows rapid transaural profile changes until the spatial filters stabilize", async () => {
    const { loadXtcTaps } = await import("./wasmEngine/filterAssets");
    const loader = loadXtcTaps as ReturnType<typeof vi.fn>;
    const initialCalls = loader.mock.calls.length;
    let releaseStereo: ((taps: Float64Array) => void) | null = null;
    let releaseCar: ((taps: Float64Array) => void) | null = null;
    const stereoTaps = new Promise<Float64Array>((resolve) => { releaseStereo = resolve; });
    const carTaps = new Promise<Float64Array>((resolve) => { releaseCar = resolve; });
    loader.mockImplementationOnce(() => stereoTaps).mockImplementationOnce(() => carTaps);

    const result = await renderPreview({ outputMode: "transaural", transauralProfile: "stereo" });
    await act(async () => {
      result.rerender(<Harness outputMode="transaural" transauralProfile="car" />);
      releaseStereo!(new Float64Array(8));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(loader.mock.calls).toHaveLength(initialCalls + 2);

    await act(async () => {
      result.rerender(<Harness outputMode="transaural" transauralProfile="laptop" />);
      releaseCar!(new Float64Array(8));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const preview = (globalThis as unknown as Record<string, unknown>).preview as { ready: boolean };
    expect(loader.mock.calls).toHaveLength(initialCalls + 3);
    expect(loader.mock.calls.at(-1)?.[1]).toBe("laptop_xtc");
    expect(preview.ready).toBe(true);
    expect(measureCalls).toHaveLength(1);
  });

  it("re-measures when the transaural profile changes", async () => {
    const result = await renderPreview({ outputMode: "transaural", transauralProfile: "stereo" });
    expect(measureCalls).toHaveLength(1);

    await act(async () => {
      result.rerender(<Harness outputMode="transaural" transauralProfile="car" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(measureCalls).toHaveLength(2);
  });

  it("measures native mode too, instead of skipping calibration for it", async () => {
    await renderPreview({ outputMode: "native" });
    expect(measureCalls).toHaveLength(1);
  });

  it("uses collapse budgets while native Apple rendering keeps the full budget", async () => {
    // 22 dB below target: native and native Apple use the full 30 dB budget,
    // while binaural/transaural and the WASM Apple fallback use the 6 dB cap.
    measureResult = { lkfs: -40, dbtp: -30 };
    await renderPreview({ outputMode: "native" });
    const nativeGain = (sentParams.at(-1) as { master: { output_gain: number } }).master.output_gain;

    measureResult = { lkfs: -40, dbtp: -30 };
    await renderPreview({ outputMode: "binaural" });
    const binauralGain = (sentParams.at(-1) as { master: { output_gain: number } }).master.output_gain;

    measureResult = { lkfs: -40, dbtp: -30 };
    await renderPreview({ outputMode: "transaural" });
    const transauralGain = (sentParams.at(-1) as { master: { output_gain: number } }).master.output_gain;

    measureResult = { lkfs: -40, dbtp: -30 };
    await renderPreview({ outputMode: "apple_spatial" });
    const appleGain = (sentParams.at(-1) as { master: { output_gain: number } }).master.output_gain;

    expect(20 * Math.log10(binauralGain)).toBeCloseTo(6, 6);
    expect(transauralGain).toBeCloseTo(binauralGain, 6);
    expect(20 * Math.log10(nativeGain)).toBeCloseTo(22, 6);
    expect(20 * Math.log10(appleGain)).toBeCloseTo(6, 6);
  });

  it("holds a calibration-driven gain increase until playback stops", async () => {
    const result = await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      playPause: () => Promise<void>;
    };
    await act(async () => {
      await preview.playPause();
    });

    await act(async () => {
      (capturedCallbacks.onMeasured as ((result: { lkfs: number; dbtp: number }, requestId: number) => void))?.(
        { lkfs: -40, dbtp: -30 },
        measureRequestIds.at(-1)!,
      );
    });
    const whilePlaying = sentParams.at(-1) as { master: { output_gain: number } };
    expect(whilePlaying.master.output_gain).toBeCloseTo(1, 6);

    await act(async () => {
      await preview.playPause();
    });
    const whileStopped = sentParams.at(-1) as { master: { output_gain: number } };
    expect(whileStopped.master.output_gain).toBeGreaterThan(1);
    result.unmount();
  });

  it("restores the cached calibration when a profile switches back in flight", async () => {
    const result = await renderPreview({ spatialProfile: "studio" });
    expect(measureCalls).toHaveLength(1);

    let resolveGate: () => void = () => {};
    measureGate = new Promise((resolve) => {
      resolveGate = resolve;
    });

    const rerender = async (profile: string) => {
      await act(async () => {
        result.rerender(<Harness spatialProfile={profile} />);
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
    };

    await rerender("listening");
    expect(measureCalls).toHaveLength(2);

    // A cached studio measurement remains valid; the listening request is
    // superseded and its late result must be ignored.
    await rerender("studio");
    expect(measureCalls).toHaveLength(2);

    await act(async () => {
      resolveGate();
      await Promise.resolve();
      await Promise.resolve();
    });

    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      playPause: () => Promise<void>;
    };
    await act(async () => {
      await preview.playPause();
    });
    expect(transportCalls.at(-1)).toMatchObject({ playing: true });
  });

  it("waits for the stems and filter sets before it calibrates a profile that changed mid-load", async () => {
    // The saved listening profile hydrates a tick after mount, while
    // `initialize()` is still decoding stems — measuring there would
    // calibrate against a half-built engine and stamp the mode as done.
    const result = render(<Harness spatialProfile="studio" />);
    await act(async () => {
      await Promise.resolve();
      result.rerender(<Harness spatialProfile="listening" />);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(callOrder.indexOf("measure")).toBeGreaterThan(callOrder.lastIndexOf("stem"));
    expect(callOrder.indexOf("decodeTaps")).toBeLessThan(callOrder.indexOf("measure"));
  });

  it("blocks playback until the in-flight measurement resolves, then allows it", async () => {
    let resolveGate: () => void = () => {};
    measureGate = new Promise((resolve) => {
      resolveGate = resolve;
    });
    await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      playPause: () => Promise<void>;
    };

    await act(async () => {
      await preview.playPause();
    });
    expect(transportCalls).toHaveLength(0);

    await act(async () => {
      resolveGate();
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => {
      await preview.playPause();
    });
    expect(transportCalls.at(-1)).toMatchObject({ playing: true });
  });

  it("defers recalibration until playback stops", async () => {
    const result = await renderPreview({ spatialProfile: "studio" });
    const getPreview = () =>
      (globalThis as unknown as Record<string, unknown>).preview as {
        playPause: () => Promise<void>;
        playing: boolean;
      };

    await act(async () => {
      await getPreview().playPause();
    });
    expect(transportCalls.at(-1)).toMatchObject({ playing: true });

    await act(async () => {
      result.rerender(<Harness spatialProfile="listening" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(measureCalls).toHaveLength(1);
    expect(transportCalls.at(-1)).toMatchObject({ playing: true });
    expect(getPreview().playing).toBe(true);

    let resolveGate: () => void = () => {};
    measureGate = new Promise((resolve) => {
      resolveGate = resolve;
    });
    await act(async () => {
      await getPreview().playPause();
      await Promise.resolve();
    });
    expect(measureCalls).toHaveLength(2);
    expect(getPreview().playing).toBe(false);

    await act(async () => {
      resolveGate();
      await Promise.resolve();
      await Promise.resolve();
    });
  });
});

describe("applyTruePeakCeiling", () => {
  it("leaves a gain alone when the measured peak clears the ceiling", () => {
    expect(applyTruePeakCeiling(-6, 1.5, -1)).toBe(1.5);
  });

  it("pulls the gain down by exactly the overshoot", () => {
    const gain = applyTruePeakCeiling(0, 1, -1);
    expect(20 * Math.log10(gain)).toBeCloseTo(-1, 6);
  });
});
