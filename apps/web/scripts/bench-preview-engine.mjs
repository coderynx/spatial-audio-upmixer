#!/usr/bin/env node
// Realtime budget for the preview engine.
//
// The worklet renders on the audio thread, so every 128-frame quantum has
// 2.67 ms to complete at 48 kHz. Miss it and the output starves — which is
// silence, not a glitch, because this node is the source. That failure is
// invisible to every correctness test we have, so it gets its own check.
//
// Run: `npm run bench:engine` from apps/web/, after `npm run build:wasm`.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import path from "node:path";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SR = 48000;
const QUANTUM = 128;
const DEADLINE_MS = (QUANTUM / SR) * 1000;
const SECONDS = 5;

// Worst case we ship: a full 7.1.4 bed, every stem a separation can produce,
// order-3 binaural decode, and the whole mastering chain lit up.
const CHANNELS = ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"];
const SHAPES = [
  "left", "right", "mono", "mono",
  "surround_left", "surround_right", "surround_left", "surround_right",
  "height_left", "height_right", "height_left", "height_right",
];
const DECODE_TAPS = 6128;
const STEMS = 9;

// A render must stay well inside the deadline on average; the mono-maker's
// stride makes one block in every few noticeably dearer, so the tail is
// budgeted separately. The first render fills both look-ahead queues from
// cold and is reported but not budgeted — it is paid once per play or seek.
const BUDGET = { mean: 0.4, p99: 1.0, worst: 1.5 };

const CASES = {
  binaural: { mode: "binaural", decode: true, label: "binaural (order-3 decode)" },
  transaural: { mode: "transaural", decode: true, label: "transaural" },
  native: { mode: "native", decode: false, label: "native 7.1.4 + limiter" },
  stereo: { mode: "stereo", decode: false, label: "stereo downmix" },
};

function instantiate() {
  const bytes = readFileSync(path.join(webRoot, "public/wasm/upmixer_dsp.wasm"));
  return new WebAssembly.Instance(new WebAssembly.Module(bytes)).exports;
}

function params(mode, decodeTaps) {
  return {
    speakers: CHANNELS.map((name, i) => ({
      name,
      azimuth_rad: i * 0.5 - 1,
      elevation_rad: i > 7 ? 0.5 : 0,
      group_gain: 0.7,
      downmix: i < 2 ? [1 - i, i] : null,
    })),
    lfe_index: CHANNELS.indexOf("LFE"),
    shapes: SHAPES,
    sends: {
      surround_bass_cutoff_hz: 250, surround_haas_ms: [31, 37], height_haas_ms: [23, 29],
      diffuse_blend: 0.55, height_low_rolloff_hz: 150, height_low_rolloff_gain: 0.15,
      height_crossover_hz: 3000, height_high_shelf_gain: 1.5,
      lfe_cutoff_hz: 120, lfe_filter_order: 4, lfe_gain: 0.316,
    },
    stems: Array.from({ length: STEMS }, () => ({
      routing: CHANNELS.map((name) => [name, 0.3]),
      rebalance_db: 0, enabled: true, eq_fir: [], route_scale: 1,
    })),
    master: {
      reference_gain: 1, reference_fir: [],
      eq_fir: Array.from({ length: 1023 }, (_, i) => (i === 511 ? 1 : Math.sin(i * 0.01) * 1e-3)),
      eq_strength: 1,
      compressor: { threshold_db: -18, ratio: 2, attack_ms: 20, release_ms: 200, knee_db: 6, makeup_db: 0 },
      bass: {
        sub_gain_db: 1, mid_gain_db: 0.5, mono_cutoff_hz: 120, excite: true, lfe_gain_db: 0,
        sub_cutoff_hz: 80, mid_cutoff_hz: 200, excite_blend: 0.3, excite_drive: 2,
      },
      limiter: mode === "native"
        ? { ceiling_dbtp: -1, lookahead_ms: 5, release_ms: 50, safety_margin_db: 0.3 }
        : null,
      stereo_pairs: [[0, 1], [4, 5], [6, 7], [8, 9], [10, 11]],
      output_gain: 1,
    },
    output_mode: mode,
    decode_taps: decodeTaps,
    xtc_taps: [],
    voicing: {
      crossfeed_amount: 0.25, crossfeed_cutoff_hz: 700, bass_shelf_hz: 120, bass_shelf_gain_db: 1.5,
      air_shelf_hz: 9000, air_shelf_gain_db: 1.5, presence_hz: 2500, presence_gain_db: 1,
      presence_q: 1.2, stereo_widen: 0.15,
    },
    soft_limit_threshold: mode === "native" ? 0 : 0.95,
    bypass_mastering: false,
  };
}

function decodeBank() {
  const bank = new Array(16 * 2 * DECODE_TAPS);
  for (let i = 0; i < bank.length; i += 1) bank[i] = Math.sin(i * 0.001) / (1 + (i % DECODE_TAPS));
  return bank;
}

function run(label, mode, decodeTaps) {
  const wasm = instantiate();
  const encoded = new TextEncoder().encode(JSON.stringify(params(mode, decodeTaps)));
  const ptr = wasm.dsp_alloc(encoded.length);
  new Uint8Array(wasm.memory.buffer, ptr, encoded.length).set(encoded);
  const engine = wasm.dsp_engine_new(SR, ptr, encoded.length);
  wasm.dsp_free(ptr, encoded.length);
  if (!engine) throw new Error(`${label}: engine rejected its parameters`);

  const frames = SR * SECONDS;
  const tone = new Float32Array(frames);
  for (let i = 0; i < frames; i += 1) tone[i] = 0.3 * Math.sin((2 * Math.PI * 220 * i) / SR);
  for (let s = 0; s < STEMS; s += 1) {
    const left = wasm.dsp_alloc(frames * 4);
    new Float32Array(wasm.memory.buffer, left, frames).set(tone);
    const right = wasm.dsp_alloc(frames * 4);
    new Float32Array(wasm.memory.buffer, right, frames).set(tone);
    wasm.dsp_engine_add_stem(engine, left, right, frames);
    wasm.dsp_free(left, frames * 4);
    wasm.dsp_free(right, frames * 4);
  }

  const out = wasm.dsp_alloc(CHANNELS.length * QUANTUM * 4);
  const times = [];
  for (;;) {
    const started = performance.now();
    const written = wasm.dsp_engine_render(engine, out, CHANNELS.length, QUANTUM);
    const elapsed = performance.now() - started;
    if (written === 0) break;
    times.push(elapsed);
  }
  wasm.dsp_free(out, CHANNELS.length * QUANTUM * 4);
  wasm.dsp_engine_free(engine);

  const cold = times[0];
  const steady = times.slice(1);
  const mean = steady.reduce((a, b) => a + b, 0) / steady.length;
  const sorted = [...steady].sort((a, b) => a - b);
  return {
    label,
    blocks: times.length,
    cold,
    mean,
    p99: sorted[Math.floor(sorted.length * 0.99)],
    worst: sorted[sorted.length - 1],
  };
}

// One engine per process: four instances in one runtime measure each other's
// garbage collection, not the DSP.
const requested = process.argv[2];
if (requested) {
  const { mode, decode, label } = CASES[requested];
  process.stdout.write(JSON.stringify(run(label, mode, decode ? decodeBank() : [])));
} else {
  const self = fileURLToPath(import.meta.url);
  console.log(`deadline ${DEADLINE_MS.toFixed(2)} ms per ${QUANTUM}-frame quantum at ${SR} Hz\n`);
  let failed = false;
  for (const name of Object.keys(CASES)) {
    const r = JSON.parse(execFileSync(process.execPath, [self, name], { encoding: "utf8" }));
    const fraction = (ms) => `${(ms / DEADLINE_MS).toFixed(2)}x`;
    const bad =
      r.mean / DEADLINE_MS > BUDGET.mean ||
      r.p99 / DEADLINE_MS > BUDGET.p99 ||
      r.worst / DEADLINE_MS > BUDGET.worst;
    failed = failed || bad;
    console.log(
      `${bad ? "FAIL" : "ok  "} ${r.label.padEnd(26)} ` +
        `mean ${r.mean.toFixed(3)}ms (${fraction(r.mean)})  ` +
        `p99 ${r.p99.toFixed(3)}ms (${fraction(r.p99)})  ` +
        `worst ${r.worst.toFixed(3)}ms (${fraction(r.worst)})  ` +
        `cold ${r.cold.toFixed(1)}ms`,
    );
  }
  console.log(
    `\nbudget: mean <= ${BUDGET.mean}x, p99 <= ${BUDGET.p99}x, worst <= ${BUDGET.worst}x of the deadline`,
  );
  if (failed) {
    console.error("\nOver budget. The audio thread starves at these numbers, which is silence, not a glitch.");
    process.exit(1);
  }
}
