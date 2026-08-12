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
let capturedCallbacks: Record<string, ((...args: never[]) => void) | undefined> = {};
// Lets a test hold `measure()` open to assert playback stays gated while
// calibration is still in flight; null (the default) resolves immediately.
let measureGate: Promise<void> | null = null;
let measureResult = { lkfs: -18, dbtp: -2 };

vi.mock("./wasmEngine/engineClient", () => ({
  DspEngineClient: {
    create: vi.fn(async (_ctx: unknown, _channels: number, callbacks: Record<string, never>) => {
      capturedCallbacks = callbacks;
      return {
        node: { connect: (target: unknown) => target, disconnect: () => {} },
        ready: Promise.resolve("0.1.0"),
        setParams: (params: Record<string, unknown>) => sentParams.push(params),
        updateParams: (params: Record<string, unknown>) => sentParams.push(params),
        setTransport: (state: { playing?: boolean; loop?: boolean }) => transportCalls.push(state),
        seek: (frame: number) => seekCalls.push(frame),
        measure: async () => {
          measureCalls.push(measureCalls.length);
          if (measureGate) await measureGate;
          return measureResult;
        },
        addStem: (left: Float32Array) => addedStems.push(left.length),
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
    this.state = "running";
  }
  async close() {}
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
    props.mix as never,
    null,
    props.mastering as never,
    ["FL", "FR", "C", "LFE", "SL", "SR"],
    (props.outputMode as never) ?? "binaural",
    (props.spatialProfile as never) ?? "studio",
    (props.transauralProfile as never) ?? "stereo",
    TEST_ENGINE_CONSTANTS,
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

  it("arms the look-ahead limiter only on the native path", async () => {
    await renderPreview({ outputMode: "native" });
    const native = sentParams.at(-1) as { master: { limiter: unknown }; soft_limit_threshold: number };
    expect(native.master.limiter).not.toBeNull();
    // Native output has the limiter as its safety net, so it does not also
    // soft-limit; the collapse paths do the reverse.
    expect(native.soft_limit_threshold).toBe(0);
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

describe("useStemPreview metering", () => {
  it("splits the core's level block into stems, channels, and the output pair", async () => {
    await renderPreview();
    const preview = (globalThis as unknown as Record<string, unknown>).preview as {
      stemLevels: { current: Map<string, { rms: number }[]> };
      channelLevels: { current: Map<string, { rms: number }> };
      headphoneLevels: { current: { left: { rms: number }; right: { rms: number } } };
      stemSpectrum: { current: Map<string, { level: number; centroid: number }> };
    };

    // Two stems (each a left/right pair), six channels, one output pair —
    // two floats each.
    const meters = [
      0.1, 0.2, 0.15, 0.25,
      0.3, 0.4, 0.35, 0.45,
      1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
      0.7, 0.8, 0.9, 1.0,
    ];
    // One [level, centroid] pair per stem.
    const spectrum = [0.5, 0.6, 0.2, 0.3];
    act(() => {
      capturedCallbacks.onFrame?.({ position: 48000, meters, spectrum } as never);
    });

    const vocals = preview.stemLevels.current.get("Vocals");
    expect(vocals?.[0].rms).toBeCloseTo(0.1, 6);
    expect(vocals?.[1].rms).toBeCloseTo(0.15, 6);

    const bass = preview.stemLevels.current.get("Bass");
    expect(bass?.[0].rms).toBeCloseTo(0.3, 6);
    expect(bass).toHaveLength(1);

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
    const spectrum = [0.5, 0.6, 0.2, 0.3];
    act(() => {
      capturedCallbacks.onFrame?.({ position: 48000, meters, spectrum } as never);
    });
    await act(async () => {
      await preview.playPause();
      await preview.playPause();
    });

    for (const level of preview.stemLevels.current.get("Vocals") ?? []) expect(level.rms).toBe(0);
    expect(preview.channelLevels.current.get("FL")?.rms).toBe(0);
    expect(preview.headphoneLevels.current.left.rms).toBe(0);
    expect(preview.headphoneLevels.current.right.rms).toBe(0);
    expect(preview.stemSpectrum.current.get("Vocals")?.level).toBe(0);
  });
});

describe("useStemPreview loudness calibration", () => {
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

  it("applies the same loudness-correction budget to native and binaural for an identical measurement", async () => {
    // 22 dB below target: inside the 30 dB native budget, but the old
    // binaural-only cap (6 dB) would have left binaural far short of it.
    measureResult = { lkfs: -40, dbtp: -30 };
    await renderPreview({ outputMode: "native" });
    const nativeGain = (sentParams.at(-1) as { master: { output_gain: number } }).master.output_gain;

    measureResult = { lkfs: -40, dbtp: -30 };
    await renderPreview({ outputMode: "binaural" });
    const binauralGain = (sentParams.at(-1) as { master: { output_gain: number } }).master.output_gain;

    expect(binauralGain).toBeCloseTo(nativeGain, 6);
    expect(20 * Math.log10(nativeGain)).toBeCloseTo(22, 6);
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

  it("pauses playback when a profile switch invalidates the current calibration", async () => {
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

    expect(transportCalls.at(-1)).toMatchObject({ playing: false });
    expect(getPreview().playing).toBe(false);
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
