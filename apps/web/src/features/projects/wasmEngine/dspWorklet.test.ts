import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Drives the shipped worklet against the shipped wasm, for the scheduling it
// does on the audio thread: which background pass gets the quantum the render
// is not using. The DSP those passes run is covered by packages/dsp; what is
// covered here is that neither pass can starve the other.

const SOURCE = readFileSync(resolve(process.cwd(), "public/dsp.worklet.js"), "utf8");
const WASM = readFileSync(resolve(process.cwd(), "public/wasm/upmixer_dsp.wasm"));
const SAMPLE_RATE = 48000;
const QUANTUM = 128;
const SECONDS = 4;
const STEMS = 3;
const CHANNELS = 3;
let workletTime = 0;

Object.defineProperty(globalThis, "currentTime", {
  configurable: true,
  get: () => workletTime,
});

const PARAMS = {
  speakers: [
    { name: "FL", azimuth_rad: 0.5236, elevation_rad: 0, group_gain: 1 },
    { name: "FR", azimuth_rad: -0.5236, elevation_rad: 0, group_gain: 1 },
    { name: "LFE", azimuth_rad: 0, elevation_rad: 0, group_gain: 1 },
  ],
  lfe_index: 2,
  shapes: ["left", "right", "mono"],
  surround_downmix_coeff: 0.7071067811865476,
  height_downmix_coeff: 0.7071067811865476,
  sends: {
    surround_bass_cutoff_hz: 250,
    height_low_rolloff_hz: 150,
    height_low_rolloff_gain: 0.15,
    height_crossover_hz: 3000,
    height_directional_band_hz: 8000,
    height_directional_band_gain: 1,
    height_high_shelf_gain: 1.5,
    stem_transient_duck: 0,
    lfe_cutoff_hz: 120,
    lfe_filter_order: 4,
    lfe_gain: 0.31622776601683794,
  },
  stems: Array.from({ length: STEMS }, () => ({
    routing: [["FL", 0.9], ["FR", 0.9], ["LFE", 0.3]],
    enabled: true,
  })),
  master: {},
  output_mode: "native",
};

type Posted = { type: string; [key: string]: unknown };

/** The worklet as the browser loads it: a bare script that registers a class
 * against three globals an AudioWorkletGlobalScope provides. */
function loadProcessor(): { processor: any; posted: Posted[] } {
  const posted: Posted[] = [];
  class Base {
    port = { postMessage: (message: Posted) => posted.push(message), onmessage: null as any };
  }
  let Processor: any;
  new Function(
    "AudioWorkletProcessor",
    "registerProcessor",
    "sampleRate",
    SOURCE,
  )(Base, (_name: string, cls: unknown) => (Processor = cls), SAMPLE_RATE);

  const processor = new Processor({
    processorOptions: { module: new WebAssembly.Module(WASM), channelCount: CHANNELS },
  });
  processor.port.onmessage({ data: { type: "params", bytes: new TextEncoder().encode(JSON.stringify(PARAMS)) } });
  const frames = SAMPLE_RATE * SECONDS;
  const tone = new Float32Array(frames);
  for (let i = 0; i < frames; i += 1) {
    tone[i] = 0.4 * Math.sin((2 * Math.PI * 220 * i) / SAMPLE_RATE);
  }
  for (let s = 0; s < STEMS; s += 1) {
    processor.port.onmessage({ data: { type: "stem", left: tone, right: tone } });
  }
  return { processor, posted };
}

/** One paused render callback, which is where both background passes are
 * advanced from. */
function quantum(processor: any, elapsed = QUANTUM / SAMPLE_RATE): void {
  workletTime += elapsed;
  processor.process([], [Array.from({ length: CHANNELS }, () => new Float32Array(QUANTUM))]);
}

describe("preview worklet background passes", () => {
  it("drops a primed block when an update changes output channels", () => {
    const { processor } = loadProcessor();
    processor.port.onmessage({ data: { type: "start", frame: 0, loop: false } });
    expect(processor.primedFrames).toBeGreaterThan(0);

    processor.port.onmessage({
      data: { type: "update", bytes: new TextEncoder().encode(JSON.stringify({ ...PARAMS, output_mode: "stereo" })) },
    });
    expect(processor.primedFrames).toBe(0);
  });

  it("reports missed audio deadlines", () => {
    workletTime = 0;
    const { processor, posted } = loadProcessor();
    processor.port.onmessage({ data: { type: "transport", playing: true } });

    quantum(processor, 0);
    quantum(processor, (QUANTUM / SAMPLE_RATE) * 3);
    for (let i = 0; i < 20; i += 1) quantum(processor);

    const frame = posted.filter((message) => message.type === "frame").at(-1);
    expect(frame?.underruns).toBe(2);
  });

  it("slices a seek's discarded run-up across callbacks", () => {
    workletTime = 0;
    const { processor, posted } = loadProcessor();
    processor.port.onmessage({ data: { type: "seek", id: 7, frame: SAMPLE_RATE } });
    expect(posted.some((message) => message.type === "seeked")).toBe(false);

    for (let i = 0; i < 200 && !posted.some((message) => message.type === "seeked"); i += 1) {
      quantum(processor);
    }
    expect(posted).toContainEqual({ type: "seeked", id: 7 });
  });

  it("advances the loudness pass while the route-scale pass is still running", () => {
    workletTime = 0;
    const { processor, posted } = loadProcessor();
    processor.port.onmessage({ data: { type: "measure", weights: [1, 1, 0] } });

    let unmeasuredScales = false;
    for (let i = 0; i < 4000; i += 1) {
      quantum(processor);
      if (posted.some((m) => m.type === "measured")) {
        unmeasuredScales = Boolean(
          processor.wasm.dsp_engine_wants_route_scale(processor.engine),
        );
        break;
      }
    }

    const measured = posted.filter((m) => m.type === "measured");
    expect(measured).toHaveLength(1);
    expect(measured[0].stage).toBe("fast");
    expect(Number(measured[0].lkfs)).toBeLessThan(0);
    // The regression: the loudness pass used to wait for every stem's scale,
    // which is per-stem work that restarts on every routing edit, so the
    // readouts stayed empty for as long as the user kept touching the mix.
    expect(unmeasuredScales).toBe(true);
  });

  it("re-measures loudness once the route-scale pass lands, as a refinement", () => {
    workletTime = 0;
    const { processor, posted } = loadProcessor();
    processor.port.onmessage({ data: { type: "measure", weights: [1, 1, 0] } });

    for (let i = 0; i < 20000; i += 1) {
      quantum(processor);
      if (posted.some((m) => m.type === "measured" && m.stage === "exact")) break;
    }

    const measured = posted.filter((m) => m.type === "measured");
    expect(measured[0].stage).toBe("fast");
    // A re-run travels the refinement channel, not the promise the host's
    // "calibrating loudness" banner already settled on.
    expect(measured.slice(1).every((m) => m.stage === "exact")).toBe(true);
    expect(measured.length).toBeGreaterThan(1);
  });
});
