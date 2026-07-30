import { afterEach, describe, expect, it, vi } from "vitest";
import { buildTruePeakKernel } from "./masteringProfiles";

// AudioWorklet modules run in their own global scope and can't `import`
// masteringProfiles.ts (see limiter.worklet.js's module docstring), so its
// 4x-oversample true-peak kernel is duplicated by hand there rather than
// shared at the source level. This test is the guard that duplication
// relies on: it loads the real worklet file (stubbing only the two worklet
// globals it references at module-evaluation time — `AudioWorkletProcessor`,
// which `class LimiterProcessor extends` evaluates immediately, and
// `registerProcessor`, called unconditionally at module scope) and asserts
// its kernel is bit-for-bit identical to masteringProfiles.ts's. If either
// copy is ever edited without the other, this fails instead of the drift
// only surfacing as a subtle true-peak mismatch between the native limiter
// path and everywhere else this approximation is used.

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("limiter.worklet.js true-peak kernel", () => {
  it("matches masteringProfiles.ts's buildTruePeakKernel bit-for-bit", async () => {
    vi.stubGlobal("AudioWorkletProcessor", class {});
    vi.stubGlobal("registerProcessor", vi.fn());
    vi.stubGlobal("sampleRate", 48000);

    const worklet = await import("../../../public/limiter.worklet.js");
    const expected = buildTruePeakKernel();

    expect(worklet.TAPS).toBe(expected.length);
    expect(worklet.OVERSAMPLE).toBe(4);
    expect(Array.from(worklet.KERNEL)).toEqual(Array.from(expected));
  });
});
