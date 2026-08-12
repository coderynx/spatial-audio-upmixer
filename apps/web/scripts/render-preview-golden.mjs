#!/usr/bin/env node
// Golden render — browser side.
//
// The preview and the export now run the *same* code: packages/dsp, reached
// through PyO3 on the export side and through the WebAssembly artifact this
// script loads on the browser side. So this is no longer a diff between two
// implementations of the same DSP (it was, until the Rust port — see
// docs/contracts/preview_export_parity.md). What it checks now is build
// provenance: that public/wasm/upmixer_dsp.wasm computes what the installed
// upmixer_dsp wheel computes. A stale artifact — the easy mistake, since the
// wasm is committed rather than built on install — fails here instead of
// silently shipping a different algorithm to the browser.
//
// Run: `npm run golden:render` from apps/web/, then
// `uv run pytest packages/core/tests/test_preview_export_golden.py`.
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import { execFileSync } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webRoot, "..", "..");
const fixtureDir = path.join(repoRoot, "packages/core/tests/fixtures/preview_export_golden");

const SR = 48000;
const DURATION_S = 5; // must match test_preview_export_golden.py::_DURATION_S

// upmixer/formats.py SURROUND_714 order: back before side, unlike 7.1/7.1.2.
const CHANNELS = ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"];

// upmixer/loudness.py CHANNEL_WEIGHT.
const LOUDNESS_WEIGHT = {
  FL: 1, FR: 1, C: 1, LFE: 0, BL: 1, BR: 1,
  SL: 1.41, SR: 1.41, TFL: 1, TFR: 1, TBL: 1, TBR: 1,
};

const BINAURAL_WEIGHT = { FL: 1, FR: 1 };

/** Same formula as test_preview_export_golden.py::_deterministic_bed. */
function deterministicBed(sr, durationS) {
  const n = Math.floor(sr * durationS);
  const channels = {};
  CHANNELS.forEach((name, i) => {
    const baseFreq = 110.0 * (i + 1);
    const data = new Float64Array(n);
    for (let s = 0; s < n; s++) {
      const t = s / sr;
      data[s] =
        0.2 * Math.sin(2 * Math.PI * baseFreq * t) +
        0.05 * Math.sin(2 * Math.PI * baseFreq * 2.37 * t + 0.7) +
        0.03 * Math.sin(2 * Math.PI * baseFreq * 5.11 * t + 1.3) +
        0.02 * Math.sin(2 * Math.PI * baseFreq * 11.03 * t + 2.1);
    }
    channels[name] = data;
  });
  return { n, channels };
}

function instantiate() {
  const bytes = fs.readFileSync(path.join(webRoot, "public/wasm/upmixer_dsp.wasm"));
  return new WebAssembly.Instance(new WebAssembly.Module(bytes)).exports;
}

class Heap {
  constructor(wasm) {
    this.wasm = wasm;
    this.blocks = [];
  }

  f64(values) {
    const bytes = values.length * 8;
    const ptr = this.wasm.dsp_alloc(bytes);
    new Float64Array(this.wasm.memory.buffer, ptr, values.length).set(values);
    this.blocks.push([ptr, bytes]);
    return ptr;
  }

  bytes(count) {
    const ptr = this.wasm.dsp_alloc(count);
    this.blocks.push([ptr, count]);
    return ptr;
  }

  json(value) {
    const encoded = new TextEncoder().encode(JSON.stringify(value));
    const ptr = this.bytes(encoded.length);
    new Uint8Array(this.wasm.memory.buffer, ptr, encoded.length).set(encoded);
    return { ptr, len: encoded.length };
  }

  free() {
    for (const [ptr, bytes] of this.blocks) this.wasm.dsp_free(ptr, bytes);
    this.blocks = [];
  }
}

/** Flatten a channel map into the core's channel-major layout. */
function flatten(channels, names, n) {
  const flat = new Float64Array(names.length * n);
  names.forEach((name, i) => flat.set(channels[name].subarray(0, n), i * n));
  return flat;
}

function unflatten(wasm, ptr, names, n) {
  const view = new Float64Array(wasm.memory.buffer, ptr, names.length * n);
  const out = {};
  names.forEach((name, i) => {
    out[name] = Float64Array.from(view.subarray(i * n, (i + 1) * n));
  });
  return out;
}

function measure(wasm, heap, channels, names, weights) {
  const n = channels[names[0]].length;
  const ptr = heap.f64(flatten(channels, names, n));
  const weightPtr = heap.f64(Float64Array.from(names.map((name) => weights[name] ?? 0)));
  return {
    measured_lkfs: wasm.dsp_integrated_loudness(ptr, weightPtr, names.length, n, SR),
    measured_tp_dbtp: wasm.dsp_true_peak_dbtp(ptr, names.length, n),
    channel_rms: Object.fromEntries(
      names.map((name) => {
        let sum = 0;
        for (const v of channels[name]) sum += v * v;
        return [name, Math.sqrt(sum / channels[name].length)];
      }),
    ),
  };
}

/** Every constant and filter asset, straight from the core that owns them. */
function loadInputs() {
  const script = path.join(webRoot, "scripts/golden-inputs.py");
  const raw = execFileSync("uv", ["run", "python", script], {
    cwd: repoRoot,
    maxBuffer: 512 * 1024 * 1024,
    encoding: "utf8",
  });
  return JSON.parse(raw);
}

/** `_mastering_config()`: spatial-air EQ, glue compression, enhance bass. */
function masterParams(inputs, referenceFir, referenceGain) {
  const lfeIndex = CHANNELS.indexOf("LFE");
  const pairs = [["FL", "FR"], ["SL", "SR"], ["BL", "BR"], ["TFL", "TFR"], ["TBL", "TBR"]];
  return {
    lfe_index: lfeIndex,
    stereo_pairs: pairs
      .map(([l, r]) => [CHANNELS.indexOf(l), CHANNELS.indexOf(r)])
      .filter(([l, r]) => l >= 0 && r >= 0),
    reference_gain: referenceGain ?? 1,
    reference_fir: referenceFir ? Array.from(referenceFir) : [],
    eq_fir: inputs.eq_fir,
    eq_strength: 1,
    compressor: inputs.compressor,
    bass: inputs.bass,
    // The bed stage deliberately stops before loudness and the limiter; both
    // belong to the later collapse stage.
    limiter: null,
  };
}

function masterBed(wasm, heap, bed, n, params) {
  const ptr = heap.f64(flatten(bed, CHANNELS, n));
  const json = heap.json(params);
  wasm.dsp_master_bed(ptr, CHANNELS.length, n, SR, json.ptr, json.len);
  return unflatten(wasm, ptr, CHANNELS, n);
}

function write(name, metrics) {
  fs.mkdirSync(fixtureDir, { recursive: true });
  const file = path.join(fixtureDir, name);
  fs.writeFileSync(file, `${JSON.stringify(metrics, null, 2)}\n`);
  console.log(`Wrote ${path.relative(repoRoot, file)}`);
}

/** Ambisonic encode, HOA decode, LFE before voicing, then voicing. */
function renderBinaural(wasm, heap, bed, n, inputs) {
  const ptr = heap.f64(flatten(bed, CHANNELS, n));
  const json = heap.json(inputs.collapse);
  const outPtr = heap.bytes(2 * n * 8);
  const ok = wasm.dsp_render_binaural(ptr, CHANNELS.length, n, SR, json.ptr, json.len, outPtr);
  if (!ok) throw new Error("binaural collapse rejected its parameters");
  const view = new Float64Array(wasm.memory.buffer, outPtr, 2 * n);
  return {
    FL: Float64Array.from(view.subarray(0, n)),
    FR: Float64Array.from(view.subarray(n, 2 * n)),
  };
}

function main() {
  const inputs = loadInputs();
  const wasm = instantiate();
  const heap = new Heap(wasm);
  const { n, channels } = deterministicBed(SR, DURATION_S);

  const bed = masterBed(wasm, heap, channels, n, masterParams(inputs, null, 1));
  write("web_bed_metrics.json", measure(wasm, heap, bed, CHANNELS, LOUDNESS_WEIGHT));

  if (inputs.reference) {
    const matched = masterBed(
      wasm, heap, channels, n,
      masterParams(inputs, inputs.reference.fir, inputs.reference.gain),
    );
    write("web_reference_match_metrics.json", measure(wasm, heap, matched, CHANNELS, LOUDNESS_WEIGHT));
  } else {
    console.warn(
      "Skipping the reference-match stage: regenerate its fixture with " +
        "REGENERATE_GOLDEN=1 uv run pytest packages/core/tests/test_preview_export_golden.py",
    );
  }

  const collapsed = renderBinaural(wasm, heap, bed, n, inputs);
  write("web_binaural_metrics.json", measure(wasm, heap, collapsed, ["FL", "FR"], BINAURAL_WEIGHT));

  heap.free();
}

main();
