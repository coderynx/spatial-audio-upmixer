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
const SEEK_FRAMES = 1024;
const DEADLINE_MS = (QUANTUM / SR) * 1000;
const SECONDS = 5;
const RUNS = Number.parseInt(process.env.BENCH_RUNS ?? "3", 10);

// Worst case we ship: a full 7.1.4 bed, every stem a separation can produce,
// order-3 binaural decode, and the whole mastering chain lit up.
const CHANNELS = ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"];
const SHAPES = [
  "left", "right", "mono", "mono",
  "surround_left", "surround_right", "surround_left", "surround_right",
  "height_left", "height_right", "height_left", "height_right",
];
const DECODE_TAPS = 6128;
const STEMS = 13;
const PRODUCTION_FIR_TAPS = 8700;

// A render must stay well inside the deadline on average; the mono-maker's
// stride makes one block in every few noticeably dearer, so the tail is
// budgeted separately. The first render fills both look-ahead queues from
// cold and has its own wall-time budget because it is paid once per play or
// seek.
const BUDGET = { mean: 0.4, p99: 1.0, worst: 1.5, cold: 200 };

// A paused measurement is *meant* to spend the quantum the render is not
// using, so only the overrun limits apply to it.
const IDLE_BUDGET = { ...BUDGET, mean: 0.9, p99: 1.0, worst: 2 };

// The fast excerpt pass's idle slice is deliberately sized past a quantum's
// budget: the node is the *source* and its output is already zero-filled
// while paused, so overrunning here costs a dropped silent callback, not a
// glitch — see MEASURE_FRAMES_FAST_IDLE's comment in public/dsp.worklet.js.
// Its budget is looser accordingly; only a regression far past what that
// slice size implies should trip it.
const FAST_IDLE_BUDGET = { ...BUDGET, mean: 5, p99: 6, worst: 8 };

// A seek deliberately spends several silent callbacks warming state quickly
// so audio resumes promptly instead of waiting through its whole preroll.
const SEEK_BUDGET = { ...BUDGET, mean: 1, p99: 2.5, worst: 3 };

// Must match MEASURE_FRAMES_IDLE / _FAST_IDLE and the FAST_EXCERPT_*
// constants in public/dsp.worklet.js.
const MEASURE_FRAMES_IDLE = 384;
const MEASURE_FRAMES_FAST_IDLE = 2048;
const FAST_EXCERPT_COUNT = 3;
const FAST_EXCERPT_SECONDS = 1;
const FAST_EXCERPT_PREROLL_SECONDS = 0.25;

const CASES = {
  binaural: { mode: "binaural", decode: true, label: "binaural (order-3 decode)" },
  transaural: { mode: "transaural", decode: true, label: "transaural" },
  native: { mode: "native", decode: false, label: "native 7.1.4 + limiter" },
  stereo: { mode: "stereo", decode: false, label: "stereo downmix" },
  // The exact whole-programme pass advances only while paused, so its slice
  // has the quantum to itself rather than sharing one with a render.
  measuring: {
    mode: "binaural",
    decode: true,
    kind: "idle-exact",
    budget: IDLE_BUDGET,
    label: "measuring (exact, paused)",
  },
  // The fast excerpt pass, same idle-only shape as the exact pass above, but
  // sized to finish in a handful of calls instead of minutes.
  measuringFast: {
    mode: "binaural",
    decode: true,
    kind: "idle-fast",
    budget: FAST_IDLE_BUDGET,
    label: "measuring (fast excerpt, paused)",
  },
  // A mix edit (mute/solo, a fader, a mastering toggle) lands via
  // `dsp_engine_set_params` while playback continues. Regression guard for
  // the bug this script didn't catch: `update_params` used to end with a
  // seek, re-rendering a 500 ms preroll synchronously on this same call —
  // budgeted at the render's own deadline since a slow update can starve
  // the very next quantum just as surely as a slow render can. Uses the
  // heaviest configuration (full mastering chain + limiter) so a retune
  // that isn't as narrow as it should be shows up here first.
  // Every stem splitting its ambient half out and feeding it to both the
  // surrounds and the heights: the heaviest per-stem work the engine has,
  // an STFT pair per stem per hop on top of the normal routing.
  ambientNative: {
    mode: "native",
    decode: false,
    ambient: true,
    label: "native 7.1.4 + ambient sends on every stem",
  },
  mixEditPlaying: {
    mode: "native",
    decode: false,
    kind: "playing-update",
    productionFirs: true,
    label: "mix edit (mute + compressor, playing)",
  },
  seek: {
    mode: "binaural",
    decode: true,
    kind: "seek",
    budget: SEEK_BUDGET,
    label: "seek preroll",
  },
  objectNative: {
    mode: "native",
    decode: false,
    objectMode: true,
    label: "native 7.1.4 + object placement",
  },
  downmixLock: {
    mode: "native",
    decode: false,
    downmixLock: true,
    label: "native 7.1.4 + downmix lock",
  },
  silenceTail: {
    mode: "native",
    decode: false,
    silenceTail: true,
    label: "native decay to silence",
  },
};

function instantiate() {
  const bytes = readFileSync(path.join(webRoot, "public/wasm/upmixer_dsp.wasm"));
  return new WebAssembly.Instance(new WebAssembly.Module(bytes)).exports;
}

function params(mode, decodeTaps, options = {}) {
  const { ambient = false, objectMode = false, downmixLock = false, productionFirs = false } = options;
  const fir = Array.from(
    { length: productionFirs ? PRODUCTION_FIR_TAPS / 2 : 1023 },
    (_, i) => (i === 511 ? 1 : Math.sin(i * 0.01) * 1e-3),
  );
  return {
    speakers: CHANNELS.map((name, i) => ({
      name,
      azimuth_rad: i * 0.5 - 1,
      elevation_rad: i > 7 ? 0.5 : 0,
      group_gain: 0.7,
    })),
    lfe_index: CHANNELS.indexOf("LFE"),
    shapes: SHAPES,
    surround_downmix_coeff: 0.7071067811865476,
    height_downmix_coeff: 0.7071067811865476,
    spatial_downmix_lock: downmixLock,
    sends: {
      surround_bass_cutoff_hz: 250,
      height_low_rolloff_hz: 150, height_low_rolloff_gain: 0.15,
      height_crossover_hz: 3000, height_high_shelf_gain: 1.5,
      height_directional_band_hz: 8000, height_directional_band_gain: 1,
      lfe_cutoff_hz: 120, lfe_filter_order: 4, lfe_gain: 0.316,
    },
    stems: Array.from({ length: STEMS }, () => ({
      routing: CHANNELS.map((name) => [name, 0.3]),
      rebalance_db: 0, enabled: true, eq_fir: [], route_scale: 1,
      ambient_rear: ambient ? 0.8 : 0, ambient_height: ambient ? 0.8 : 0,
      ambient_height_crossover_hz: 2000,
      object_mode: objectMode ? "linked-stereo" : null,
      object_placement: objectMode
        ? { azimuth_deg: 45, elevation_deg: 20, width_deg: 60, spread_deg: 40 }
        : null,
    })),
    master: {
      // Both default-off stages benched on, per parity contract §4.
      head: { cutoff_hz: 20 },
      clip: { ceiling_dbtp: -1, clip_db: 1, knee: 0.5 },
      reference_gain: 1, reference_fir: productionFirs ? fir : [],
      eq_fir: fir,
      eq_strength: 1,
      // Every band the stage accepts, all of them driven into gain reduction
      // so none of them coasts on the redesign-only-when-the-gain-moves path.
      dynamic_eq: [
        { freq_hz: 3800, q: 2, threshold_db: -60, ratio: 4, attack_ms: 10, release_ms: 150 },
        { freq_hz: 220, q: 1.4, threshold_db: -60, ratio: 3, attack_ms: 30, release_ms: 250 },
        { freq_hz: 900, q: 3, threshold_db: -60, ratio: 2, attack_ms: 5, release_ms: 100 },
        { freq_hz: 8000, q: 4, threshold_db: -60, ratio: 6, attack_ms: 1, release_ms: 60 },
      ],
      compressor: {
        threshold_db: -18, ratio: 2, attack_ms: 20, release_ms: 200, knee_db: 6, makeup_db: 0,
        sidechain_hpf_hz: 100,
      },
      bass: {
        sub_gain_db: 1, mid_gain_db: 0.5, unify_hz: 120, punch: 0.3, excite: true, lfe_gain_db: 0,
        sub_cutoff_hz: 80, mid_cutoff_hz: 200, excite_blend: 0.3, excite_drive: 2,
        punch_fast_ms: 10, punch_slow_ms: 120, punch_max_db: 6,
        // Worst case for the decorrelator too: full depth, so it runs a
        // zero-phase band split plus an 8-section cascade on all 11 non-LFE
        // channels rather than being skipped.
        decorrelate: 1, decorr_low_hz: 100, decorr_high_hz: 300, decorr_sections: 32,
        decorr_max_delay_ms: 30, decorr_fast_ms: 30, decorr_slow_ms: 300,
      },
      limiter: mode === "native"
        ? { ceiling_dbtp: -1, lookahead_ms: 5, release_ms: 50, safety_margin_db: 0.3 }
        : null,
      // Worst case: the `all` spread, so the unifier runs one zero-phase pass
      // per non-LFE channel and every one of them takes a return.
      lf_targets: [
        ...[0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11].map((i) => [i, 1 / 11]),
        [3, 0.3 * 0.31622776601683794],
      ],
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

function run({ label, mode, decode, kind, ambient, objectMode, downmixLock, productionFirs, silenceTail }) {
  const wasm = instantiate();
  const decodeTaps = decode ? decodeBank() : [];
  const options = { ambient, objectMode, downmixLock, productionFirs };
  const encoded = new TextEncoder().encode(JSON.stringify(params(mode, decodeTaps, options)));
  const ptr = wasm.dsp_alloc(encoded.length);
  new Uint8Array(wasm.memory.buffer, ptr, encoded.length).set(encoded);
  const engine = wasm.dsp_engine_new(SR, ptr, encoded.length);
  wasm.dsp_free(ptr, encoded.length);
  if (!engine) throw new Error(`${label}: engine rejected its parameters`);

  const frames = SR * SECONDS;
  const tone = new Float32Array(frames);
  for (let i = 0; i < frames; i += 1) {
    tone[i] = silenceTail && i >= SR ? 0 : 0.3 * Math.sin((2 * Math.PI * 220 * i) / SR);
  }
  for (let s = 0; s < STEMS; s += 1) {
    const left = wasm.dsp_alloc(frames * 4);
    new Float32Array(wasm.memory.buffer, left, frames).set(tone);
    const right = wasm.dsp_alloc(frames * 4);
    new Float32Array(wasm.memory.buffer, right, frames).set(tone);
    wasm.dsp_engine_add_stem(engine, left, right, frames);
    wasm.dsp_free(left, frames * 4);
    wasm.dsp_free(right, frames * 4);
  }

  let pass = 0;
  let weightPtr = 0;
  let resultPtr = 0;
  if (kind === "idle-exact" || kind === "idle-fast") {
    weightPtr = wasm.dsp_alloc(16);
    new Float64Array(wasm.memory.buffer, weightPtr, 2).set([1, 1]);
    resultPtr = wasm.dsp_alloc(16);
    pass = kind === "idle-exact"
      ? wasm.dsp_measure_begin(engine, weightPtr, 2)
      : wasm.dsp_measure_begin_excerpts(
          engine,
          weightPtr,
          2,
          FAST_EXCERPT_COUNT,
          Math.round(FAST_EXCERPT_SECONDS * SR),
          Math.round(FAST_EXCERPT_PREROLL_SECONDS * SR),
        );
    if (!pass) throw new Error(`${label}: measurement could not start`);
  }

  const out = wasm.dsp_alloc(CHANNELS.length * QUANTUM * 4);
  const times = [];
  if (kind === "idle-exact" || kind === "idle-fast") {
    // Paused: the quantum does nothing but advance the measurement.
    const step = kind === "idle-exact" ? MEASURE_FRAMES_IDLE : MEASURE_FRAMES_FAST_IDLE;
    for (;;) {
      const started = performance.now();
      const done = wasm.dsp_measure_advance(pass, step, resultPtr);
      times.push(performance.now() - started);
      if (done) break;
    }
  } else if (kind === "playing-update") {
    // Renders advance the transport but are not what's timed; only
    // `dsp_engine_set_params` itself has to fit the deadline, since that's
    // the call the worklet's `port.onmessage` makes on the audio thread.
    let toggle = false;
    for (;;) {
      const written = wasm.dsp_engine_render(engine, out, CHANNELS.length, QUANTUM);
      if (written === 0) break;
      toggle = !toggle;
      const edited = params(mode, decodeTaps, options);
      edited.stems[0].enabled = toggle;
      edited.master.compressor.threshold_db = toggle ? -20 : -18;
      const encodedEdit = new TextEncoder().encode(JSON.stringify(edited));
      const editPtr = wasm.dsp_alloc(encodedEdit.length);
      new Uint8Array(wasm.memory.buffer, editPtr, encodedEdit.length).set(encodedEdit);
      const started = performance.now();
      wasm.dsp_engine_set_params(engine, editPtr, encodedEdit.length);
      times.push(performance.now() - started);
      wasm.dsp_free(editPtr, encodedEdit.length);
    }
  } else if (kind === "seek") {
    for (let frame = 0; frame < frames; frame += SR / 4) {
      wasm.dsp_engine_begin_seek(engine, frame);
      while (wasm.dsp_engine_is_seeking(engine)) {
        const started = performance.now();
        wasm.dsp_engine_advance_seek(engine, SEEK_FRAMES);
        times.push(performance.now() - started);
      }
    }
  } else {
    for (;;) {
      const started = performance.now();
      const written = wasm.dsp_engine_render(engine, out, CHANNELS.length, QUANTUM);
      const elapsed = performance.now() - started;
      if (written === 0) break;
      times.push(elapsed);
    }
  }
  if (pass) wasm.dsp_measure_free(pass);
  if (weightPtr) wasm.dsp_free(weightPtr, 16);
  if (resultPtr) wasm.dsp_free(resultPtr, 16);
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

if (!Number.isInteger(RUNS) || RUNS < 1) {
  throw new Error("BENCH_RUNS must be a positive integer");
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function summary(results, field) {
  const values = results.map((result) => result[field]);
  return { min: Math.min(...values), median: median(values), max: Math.max(...values) };
}

function reportRange(label, result) {
  const fraction = (ms) => `${(ms / DEADLINE_MS).toFixed(2)}x`;
  return `${label} ${result.min.toFixed(3)}/${result.median.toFixed(3)}/${result.max.toFixed(3)}ms ` +
    `(${fraction(result.median)})`;
}

const requested = process.argv[2];
if (requested) {
  const definition = CASES[requested];
  if (!definition) throw new Error(`unknown benchmark case: ${requested}`);
  process.stdout.write(JSON.stringify(run(definition)));
} else {
  const self = fileURLToPath(import.meta.url);
  console.log(
    `deadline ${DEADLINE_MS.toFixed(2)} ms per ${QUANTUM}-frame quantum at ${SR} Hz; ` +
      `${RUNS} runs, reporting min/median/max\n`,
  );
  let failed = false;
  for (const name of Object.keys(CASES)) {
    const results = Array.from({ length: RUNS }, () => (
      JSON.parse(execFileSync(process.execPath, [self, name], { encoding: "utf8" }))
    ));
    const budget = CASES[name].budget ?? BUDGET;
    const mean = summary(results, "mean");
    const p99 = summary(results, "p99");
    const worst = summary(results, "worst");
    const cold = summary(results, "cold");
    const bad =
      mean.median / DEADLINE_MS > budget.mean ||
      p99.median / DEADLINE_MS > budget.p99 ||
      worst.median / DEADLINE_MS > budget.worst ||
      cold.median > budget.cold;
    failed = failed || bad;
    console.log(
      `${bad ? "FAIL" : "ok  "} ${results[0].label.padEnd(34)} ` +
        `${reportRange("mean", mean)}  ${reportRange("p99", p99)}  ` +
        `${reportRange("worst", worst)}  cold ${cold.min.toFixed(1)}/${cold.median.toFixed(1)}/${cold.max.toFixed(1)}ms`,
    );
  }
  console.log(
    `\nbudget: mean <= ${BUDGET.mean}x, p99 <= ${BUDGET.p99}x, worst <= ${BUDGET.worst}x of the` +
      ` deadline, cold median <= ${BUDGET.cold}ms (a paused measurement may use ${IDLE_BUDGET.mean}x of the mean)`,
  );
  if (failed) {
    console.error("\nOver budget. The audio thread starves at these numbers, which is silence, not a glitch.");
    process.exit(1);
  }
}
