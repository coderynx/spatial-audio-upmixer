import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Drives the shipped wasm through the exact C ABI the worklet uses, so a
// marshalling mistake fails here rather than as silence in the browser. The
// DSP itself is covered by packages/dsp's own suite.

// vitest runs with apps/web as its working directory.
const WASM_PATH = resolve(process.cwd(), "public/wasm/upmixer_dsp.wasm");

type DspExports = {
  memory: WebAssembly.Memory;
  dsp_alloc: (bytes: number) => number;
  dsp_free: (ptr: number, bytes: number) => void;
  dsp_core_version_len: () => number;
  dsp_core_version_ptr: () => number;
  dsp_engine_new: (sampleRate: number, ptr: number, len: number) => number;
  dsp_engine_add_stem: (engine: number, left: number, right: number, frames: number) => void;
  dsp_engine_render: (engine: number, out: number, channels: number, frames: number) => number;
  dsp_engine_total_frames: (engine: number) => number;
  dsp_engine_rewind: (engine: number) => void;
  dsp_engine_free: (engine: number) => void;
};

function instantiate(): DspExports {
  const bytes = readFileSync(WASM_PATH);
  const module = new WebAssembly.Module(bytes);
  return new WebAssembly.Instance(module).exports as unknown as DspExports;
}

const SAMPLE_RATE = 48000;
const FRAMES = 4800;
const CHANNELS = 3;

const PARAMS = {
  speakers: [
    { name: "FL", azimuth_rad: 0.5236, elevation_rad: 0, group_gain: 1 },
    { name: "FR", azimuth_rad: -0.5236, elevation_rad: 0, group_gain: 1 },
    { name: "LFE", azimuth_rad: 0, elevation_rad: 0, group_gain: 1 },
  ],
  lfe_index: 2,
  shapes: ["left", "right", "mono"],
  sends: {
    surround_bass_cutoff_hz: 250,
    surround_haas_ms: [31, 37],
    height_haas_ms: [23, 29],
    diffuse_blend: 0.55,
    height_low_rolloff_hz: 150,
    height_low_rolloff_gain: 0.15,
    height_crossover_hz: 3000,
    height_high_shelf_gain: 1.5,
    lfe_cutoff_hz: 120,
    lfe_filter_order: 4,
    lfe_gain: 0.31622776601683794,
  },
  stems: [{ routing: [["FL", 0.9], ["FR", 0.9], ["LFE", 0.3]], enabled: true }],
  master: {},
  output_mode: "native",
};

function tone(frames: number): Float32Array {
  const out = new Float32Array(frames);
  for (let i = 0; i < frames; i += 1) {
    out[i] = 0.5 * Math.sin((2 * Math.PI * 220 * i) / SAMPLE_RATE);
  }
  return out;
}

function writeStem(wasm: DspExports, values: Float32Array): { ptr: number; bytes: number } {
  const bytes = values.length * 4;
  const ptr = wasm.dsp_alloc(bytes);
  new Float32Array(wasm.memory.buffer, ptr, values.length).set(values);
  return { ptr, bytes };
}

function createEngine(wasm: DspExports, params: unknown): number {
  const encoded = new TextEncoder().encode(JSON.stringify(params));
  const ptr = wasm.dsp_alloc(encoded.length);
  new Uint8Array(wasm.memory.buffer, ptr, encoded.length).set(encoded);
  const engine = wasm.dsp_engine_new(SAMPLE_RATE, ptr, encoded.length);
  wasm.dsp_free(ptr, encoded.length);
  return engine;
}

function renderAll(wasm: DspExports, engine: number, block: number): Float32Array[] {
  const outPtr = wasm.dsp_alloc(CHANNELS * block * 4);
  const channels: number[][] = Array.from({ length: CHANNELS }, () => []);
  for (;;) {
    const written = wasm.dsp_engine_render(engine, outPtr, CHANNELS, block);
    if (written === 0) break;
    const view = new Float32Array(wasm.memory.buffer, outPtr, CHANNELS * block);
    for (let ch = 0; ch < CHANNELS; ch += 1) {
      for (let i = 0; i < written; i += 1) channels[ch].push(view[ch * block + i]);
    }
  }
  wasm.dsp_free(outPtr, CHANNELS * block * 4);
  return channels.map((c) => Float32Array.from(c));
}

describe("shared DSP core (wasm)", () => {
  it("exports a core version string", () => {
    const wasm = instantiate();
    const len = wasm.dsp_core_version_len();
    const ptr = wasm.dsp_core_version_ptr();
    const version = new TextDecoder().decode(new Uint8Array(wasm.memory.buffer, ptr, len));
    expect(version).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("rejects malformed parameters instead of rendering silence", () => {
    const wasm = instantiate();
    expect(createEngine(wasm, { speakers: "not a list" })).toBe(0);
  });

  it("renders a routed bed and reports the programme length", () => {
    const wasm = instantiate();
    const engine = createEngine(wasm, PARAMS);
    expect(engine).not.toBe(0);

    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);
    wasm.dsp_free(left.ptr, left.bytes);
    wasm.dsp_free(right.ptr, right.bytes);

    expect(wasm.dsp_engine_total_frames(engine)).toBe(FRAMES);

    const rendered = renderAll(wasm, engine, 128);
    expect(rendered[0]).toHaveLength(FRAMES);

    const peak = (c: Float32Array) => c.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    expect(peak(rendered[0])).toBeGreaterThan(0.1);
    expect(peak(rendered[1])).toBeGreaterThan(0.1);
    // LFE is low-passed and attenuated by -10 dB, so it stays well under the
    // front channels rather than being silent.
    expect(peak(rendered[2])).toBeGreaterThan(0);
    expect(peak(rendered[2])).toBeLessThan(peak(rendered[0]));

    wasm.dsp_engine_free(engine);
  });

  it("renders the same samples at the Web Audio quantum as in one pass", () => {
    const build = () => {
      const wasm = instantiate();
      const engine = createEngine(wasm, PARAMS);
      const left = writeStem(wasm, tone(FRAMES));
      const right = writeStem(wasm, tone(FRAMES));
      wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);
      return { wasm, engine };
    };

    const quantum = build();
    const single = build();
    const a = renderAll(quantum.wasm, quantum.engine, 128);
    const b = renderAll(single.wasm, single.engine, FRAMES);

    for (let ch = 0; ch < CHANNELS; ch += 1) {
      expect(a[ch].length).toBe(b[ch].length);
      for (let i = 0; i < a[ch].length; i += 1) {
        expect(Math.abs(a[ch][i] - b[ch][i])).toBeLessThan(1e-6);
      }
    }
  });

  it("rewinding replays the programme from the top", () => {
    const wasm = instantiate();
    const engine = createEngine(wasm, PARAMS);
    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);

    const first = renderAll(wasm, engine, 512);
    expect(renderAll(wasm, engine, 512)[0]).toHaveLength(0);
    wasm.dsp_engine_rewind(engine);
    const second = renderAll(wasm, engine, 512);

    expect(second[0].length).toBe(first[0].length);
    for (let i = 0; i < first[0].length; i += 1) {
      expect(second[0][i]).toBeCloseTo(first[0][i], 6);
    }
  });
});
