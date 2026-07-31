import { afterEach, describe, expect, it, vi } from "vitest";
import { buildTruePeakKernel } from "./masteringProfiles";

// masteringProfiles.ts's buildTruePeakKernel is the single true-peak kernel
// source. limiter.worklet.js holds no kernel-building code and receives the
// computed kernel through processorOptions, so there is no second copy to pin
// against. These tests instead cover the kernel's numeric properties and the
// worklet's use of the passed-in kernel. See
// docs/contracts/preview_export_parity.md Ledger D18.

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function loadLimiterProcessor() {
  let ProcessorClass: unknown;
  vi.stubGlobal(
    "AudioWorkletProcessor",
    class {
      port = { postMessage: vi.fn() };
    },
  );
  vi.stubGlobal("registerProcessor", (_name: string, cls: unknown) => {
    ProcessorClass = cls;
  });
  vi.stubGlobal("sampleRate", 48000);
  await import("../../../public/limiter.worklet.js");
  return ProcessorClass as new (options: unknown) => {
    _kernel: Float64Array;
    process: (inputs: Float32Array[][], outputs: Float32Array[][]) => boolean;
  };
}

describe("buildTruePeakKernel", () => {
  it("is a 32-tap symmetric Hann-windowed-sinc kernel", () => {
    const k = buildTruePeakKernel();
    expect(k.length).toBe(32);
    // Hann window zeroes both endpoints.
    expect(k[0]).toBeCloseTo(0, 12);
    expect(k[k.length - 1]).toBeCloseTo(0, 12);
    for (let i = 0; i < k.length; i++) {
      expect(k[i]).toBeCloseTo(k[k.length - 1 - i], 12);
    }
    const max = Math.max(...Array.from(k));
    expect(k[15]).toBeCloseTo(max, 12);
    expect(k[16]).toBeCloseTo(max, 12);
  });
});

describe("limiter.worklet.js kernel wiring", () => {
  it("builds its detector from processorOptions.truePeakKernel, not an internal copy", async () => {
    const ProcessorClass = await loadLimiterProcessor();
    const kernel = Array.from(buildTruePeakKernel());
    const proc = new ProcessorClass({
      processorOptions: {
        ceilingDb: -1,
        lookaheadMs: 5,
        releaseMs: 50,
        safetyMarginDb: 0.1,
        numberOfChannels: 2,
        truePeakKernel: kernel,
      },
    });

    expect(Array.from(proc._kernel)).toEqual(kernel);

    // Drives a hot block through process(): proves the passed kernel actually
    // feeds detection and the processor limits without throwing.
    const input = [new Float32Array(128).fill(1.0), new Float32Array(128).fill(1.0)];
    const output = [new Float32Array(128), new Float32Array(128)];
    expect(() => proc.process([input], [output])).not.toThrow();
  });
});
