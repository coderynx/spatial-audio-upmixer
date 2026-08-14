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
  dsp_engine_set_decode_taps: (engine: number, ptr: number, nTaps: number) => void;
  dsp_engine_set_xtc_taps: (engine: number, ptr: number, nTaps: number) => void;
  dsp_engine_render: (engine: number, out: number, channels: number, frames: number) => number;
  dsp_engine_total_frames: (engine: number) => number;
  dsp_engine_rewind: (engine: number) => void;
  dsp_engine_free: (engine: number) => void;
  dsp_engine_set_params: (engine: number, ptr: number, len: number) => number;
  dsp_engine_seek: (engine: number, frame: number) => void;
  dsp_engine_position: (engine: number) => number;
  dsp_engine_output_channels: (engine: number) => number;
  dsp_engine_meters: (engine: number, out: number, capacity: number) => number;
  dsp_engine_stem_spectrum: (engine: number, out: number, capacity: number) => number;
  dsp_measure_begin: (engine: number, weights: number, channels: number) => number;
  dsp_measure_begin_excerpts: (
    engine: number,
    weights: number,
    channels: number,
    count: number,
    excerptFrames: number,
    prerollFrames: number,
  ) => number;
  dsp_measure_advance: (pass: number, frames: number, out: number) => number;
  dsp_measure_progress: (pass: number) => number;
  dsp_measure_free: (pass: number) => void;
  dsp_integrated_loudness: (
    ptr: number,
    weights: number,
    channels: number,
    frames: number,
    sampleRate: number,
  ) => number;
  dsp_true_peak_dbtp: (ptr: number, channels: number, frames: number) => number;
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

// A single unity tap per (acn, ear) — [acn][ear][tap] flattened. Not a
// well-formed decode filter, just enough to prove the wiring: silent when
// unset, audible once set, and unaffected by a later `set_params` call.
function decodeTapsFlat(): Float64Array {
  const N_ACN = 16;
  return new Float64Array(N_ACN * 2).fill(1);
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

  it("reports levels for each stem, bed channel, and the output pair", () => {
    const wasm = instantiate();
    const engine = createEngine(wasm, PARAMS);
    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);

    const outPtr = wasm.dsp_alloc(CHANNELS * 512 * 4);
    wasm.dsp_engine_render(engine, outPtr, CHANNELS, 512);

    const meterPtr = wasm.dsp_alloc(256 * 4);
    const written = wasm.dsp_engine_meters(engine, meterPtr, 256);
    // One stem (left/right pair), three bed channels, one output pair — two
    // floats each.
    expect(written).toBe(2 * (2 + CHANNELS + 2));

    const meters = new Float32Array(wasm.memory.buffer, meterPtr, written);
    expect(meters[1]).toBeGreaterThan(0);
    expect(wasm.dsp_engine_position(engine)).toBe(512);
  });

  it("reports a [level, centroid] pair for the haze/elevation displays", () => {
    const wasm = instantiate();
    const engine = createEngine(wasm, PARAMS);
    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);

    const outPtr = wasm.dsp_alloc(CHANNELS * 4096 * 4);
    wasm.dsp_engine_render(engine, outPtr, CHANNELS, 4096);

    const spectrumPtr = wasm.dsp_alloc(256 * 4);
    const written = wasm.dsp_engine_stem_spectrum(engine, spectrumPtr, 256);
    expect(written).toBe(2);

    const spectrum = new Float32Array(wasm.memory.buffer, spectrumPtr, written);
    expect(spectrum[0]).toBeGreaterThan(0);
    expect(spectrum[1]).toBeGreaterThanOrEqual(0);
    expect(spectrum[1]).toBeLessThanOrEqual(1);
  });

  it("swaps parameters in place without dropping the stems or playhead", () => {
    const wasm = instantiate();
    const engine = createEngine(wasm, PARAMS);
    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);

    const outPtr = wasm.dsp_alloc(CHANNELS * 512 * 4);
    wasm.dsp_engine_render(engine, outPtr, CHANNELS, 512);
    const position = wasm.dsp_engine_position(engine);
    const steadyStatePeak = new Float32Array(wasm.memory.buffer, outPtr, CHANNELS * 512).reduce(
      (m, v) => Math.max(m, Math.abs(v)),
      0,
    );

    const muted = { ...PARAMS, stems: [{ ...PARAMS.stems[0], enabled: false }] };
    const encoded = new TextEncoder().encode(JSON.stringify(muted));
    const ptr = wasm.dsp_alloc(encoded.length);
    new Uint8Array(wasm.memory.buffer, ptr, encoded.length).set(encoded);
    expect(wasm.dsp_engine_set_params(engine, ptr, encoded.length)).toBe(1);
    wasm.dsp_free(ptr, encoded.length);

    expect(wasm.dsp_engine_total_frames(engine)).toBe(FRAMES);
    expect(wasm.dsp_engine_position(engine)).toBe(position);

    // The mute ramps rather than snapping — the quantum right after the
    // swap must not come back short or silent, which is what a reload (or
    // the old seek-based `update_params`) would have produced instead.
    wasm.dsp_engine_render(engine, outPtr, CHANNELS, 512);
    let view = new Float32Array(wasm.memory.buffer, outPtr, CHANNELS * 512);
    const rampedPeak = view.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    expect(rampedPeak).toBeGreaterThan(0);
    expect(rampedPeak).toBeLessThan(steadyStatePeak);

    // Well past the ramp's time constant, the mute has fully landed. The
    // smoother is a one-pole, so it only ever approaches zero asymptotically
    // rather than hitting it exactly — inaudible, but not bit-exact silence.
    for (let i = 0; i < 8; i += 1) {
      wasm.dsp_engine_render(engine, outPtr, CHANNELS, 512);
    }
    view = new Float32Array(wasm.memory.buffer, outPtr, CHANNELS * 512);
    expect(view.reduce((m, v) => Math.max(m, Math.abs(v)), 0)).toBeLessThan(1e-4);
  });

  it("sets the decode bank on its own channel, surviving a later set_params", () => {
    const wasm = instantiate();
    // No LFE here (unlike PARAMS): the binaural collapse sums LFE straight
    // into both ears ahead of the decode stage, which would make the
    // "silent before taps arrive" baseline below untrue for the wrong
    // reason.
    const binaural = {
      ...PARAMS,
      output_mode: "binaural",
      lfe_index: null,
      stems: [{ routing: [["FL", 0.9], ["FR", 0.9]], enabled: true }],
    };
    const engine = createEngine(wasm, binaural);
    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);

    const outPtr = wasm.dsp_alloc(2 * 512 * 4);
    const peak = () => {
      wasm.dsp_engine_rewind(engine);
      wasm.dsp_engine_render(engine, outPtr, 2, 512);
      const view = new Float32Array(wasm.memory.buffer, outPtr, 2 * 512);
      return view.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    };

    // No `decode_taps` in the JSON block and no override set yet: the
    // ambisonic decode convolvers are empty, so the binaural collapse is
    // silent — this is the state a fresh engine renders into before its
    // profile's HRIR bank has been fetched.
    expect(peak()).toBe(0);

    const taps = decodeTapsFlat();
    const tapsPtr = wasm.dsp_alloc(taps.length * 8);
    new Float64Array(wasm.memory.buffer, tapsPtr, taps.length).set(taps);
    wasm.dsp_engine_set_decode_taps(engine, tapsPtr, taps.length);
    wasm.dsp_free(tapsPtr, taps.length * 8);

    expect(peak()).toBeGreaterThan(0);

    // A mix edit through `set_params` never carries `decode_taps` of its
    // own (the web client stopped sending it once it had its own channel) —
    // the override must outlive that call rather than reverting to silence.
    const encoded = new TextEncoder().encode(JSON.stringify(binaural));
    const ptr = wasm.dsp_alloc(encoded.length);
    new Uint8Array(wasm.memory.buffer, ptr, encoded.length).set(encoded);
    expect(wasm.dsp_engine_set_params(engine, ptr, encoded.length)).toBe(1);
    wasm.dsp_free(ptr, encoded.length);

    expect(peak()).toBeGreaterThan(0);

    wasm.dsp_engine_free(engine);
  });

  it("reports the collapse channel count per output mode", () => {
    const wasm = instantiate();
    const native = createEngine(wasm, PARAMS);
    expect(wasm.dsp_engine_output_channels(native)).toBe(CHANNELS);
    const stereo = createEngine(wasm, { ...PARAMS, output_mode: "stereo" });
    expect(wasm.dsp_engine_output_channels(stereo)).toBe(2);
  });

  // The worklet cannot afford to measure the programme in one call, so it
  // advances a pass from `process`. This drives that ABI the same way and
  // checks the answer against measuring the rendered output directly.
  it("measures the programme in slices, matching a direct measurement", () => {
    const wasm = instantiate();
    const stereo = { ...PARAMS, output_mode: "stereo" };
    const engine = createEngine(wasm, stereo);
    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);

    // Direct: render the whole collapse, then measure the result.
    const outPtr = wasm.dsp_alloc(2 * FRAMES * 8);
    const collapsed: number[][] = [[], []];
    const block = wasm.dsp_alloc(2 * 512 * 4);
    for (;;) {
      const written = wasm.dsp_engine_render(engine, block, 2, 512);
      if (written === 0) break;
      const view = new Float32Array(wasm.memory.buffer, block, 2 * 512);
      for (let ch = 0; ch < 2; ch += 1) {
        for (let i = 0; i < written; i += 1) collapsed[ch].push(view[ch * 512 + i]);
      }
    }
    const frames = collapsed[0].length;
    const flat = new Float64Array(wasm.memory.buffer, outPtr, 2 * frames);
    flat.set(collapsed[0], 0);
    flat.set(collapsed[1], frames);
    const weightPtr = wasm.dsp_alloc(16);
    new Float64Array(wasm.memory.buffer, weightPtr, 2).set([1, 1]);
    const wantLkfs = wasm.dsp_integrated_loudness(outPtr, weightPtr, 2, frames, SAMPLE_RATE);
    const wantDbtp = wasm.dsp_true_peak_dbtp(outPtr, 2, frames);

    // Sliced: begin a pass and advance it in render quanta.
    wasm.dsp_engine_rewind(engine);
    const pass = wasm.dsp_measure_begin(engine, weightPtr, 2);
    expect(pass).not.toBe(0);
    const resultPtr = wasm.dsp_alloc(16);
    let slices = 0;
    while (wasm.dsp_measure_advance(pass, 128, resultPtr) === 0) {
      slices += 1;
      expect(slices).toBeLessThan(10_000);
    }
    expect(slices).toBeGreaterThan(1);
    expect(wasm.dsp_measure_progress(pass)).toBe(1);
    const got = new Float64Array(wasm.memory.buffer, resultPtr, 2);

    // Rendering to f32 for the direct pass costs a little precision; the
    // sliced meters work in f64 throughout.
    expect(got[0]).toBeCloseTo(wantLkfs, 4);
    expect(got[1]).toBeCloseTo(wantDbtp, 4);

    wasm.dsp_measure_free(pass);
    wasm.dsp_engine_free(engine);
  });

  // The fast excerpt pass (audioEngine.ts's two-stage measurement) drives
  // this ABI instead. A plan that spans the whole programme is the fallback
  // path `MeasurementPass::new_excerpts` takes on a short track, and should
  // match a plain `dsp_measure_begin` pass exactly, not just approximately.
  it("an excerpt pass covering the whole programme matches a direct pass", () => {
    const wasm = instantiate();
    const engine = createEngine(wasm, { ...PARAMS, output_mode: "stereo" });
    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);

    const weightPtr = wasm.dsp_alloc(16);
    new Float64Array(wasm.memory.buffer, weightPtr, 2).set([1, 1]);
    const resultPtr = wasm.dsp_alloc(16);

    const direct = wasm.dsp_measure_begin(engine, weightPtr, 2);
    while (wasm.dsp_measure_advance(direct, 512, resultPtr) === 0) {
      /* advance to completion */
    }
    const want = new Float64Array(wasm.memory.buffer.slice(resultPtr, resultPtr + 16));
    wasm.dsp_measure_free(direct);

    const excerpts = wasm.dsp_measure_begin_excerpts(engine, weightPtr, 2, 1, FRAMES, 0);
    expect(excerpts).not.toBe(0);
    let slices = 0;
    while (wasm.dsp_measure_advance(excerpts, 512, resultPtr) === 0) {
      slices += 1;
      expect(slices).toBeLessThan(10_000);
    }
    expect(wasm.dsp_measure_progress(excerpts)).toBe(1);
    const got = new Float64Array(wasm.memory.buffer, resultPtr, 2);
    expect(got[0]).toBeCloseTo(want[0], 9);
    expect(got[1]).toBeCloseTo(want[1], 9);

    wasm.dsp_measure_free(excerpts);
    wasm.dsp_engine_free(engine);
  });

  it("a measurement leaves the transport where it found it", () => {
    const wasm = instantiate();
    const engine = createEngine(wasm, { ...PARAMS, output_mode: "stereo" });
    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);

    const outPtr = wasm.dsp_alloc(2 * 512 * 4);
    wasm.dsp_engine_render(engine, outPtr, 2, 512);
    const position = wasm.dsp_engine_position(engine);

    const weightPtr = wasm.dsp_alloc(16);
    new Float64Array(wasm.memory.buffer, weightPtr, 2).set([1, 1]);
    const resultPtr = wasm.dsp_alloc(16);
    const pass = wasm.dsp_measure_begin(engine, weightPtr, 2);
    while (wasm.dsp_measure_advance(pass, 4096, resultPtr) === 0) {
      /* advance to completion */
    }
    wasm.dsp_measure_free(pass);

    expect(wasm.dsp_engine_position(engine)).toBe(position);
    wasm.dsp_engine_free(engine);
  });

  it("seeking moves the playhead", () => {
    const wasm = instantiate();
    const engine = createEngine(wasm, PARAMS);
    const left = writeStem(wasm, tone(FRAMES));
    const right = writeStem(wasm, tone(FRAMES));
    wasm.dsp_engine_add_stem(engine, left.ptr, right.ptr, FRAMES);

    wasm.dsp_engine_seek(engine, 2400);
    expect(wasm.dsp_engine_position(engine)).toBe(2400);
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
