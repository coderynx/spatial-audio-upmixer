#!/usr/bin/env node
// Cross-engine golden render diff — web (preview) side.
//
// See docs/contracts/preview_export_parity.md §5 and
// tests/test_preview_export_golden.py (the Python/export side of this same
// comparison). This script:
//   1. Bundles the real, framework-free `previewGraph.ts` (the extraction
//      from `useStemPreview.ts`'s `buildMasteringTopology`, see Ledger
//      item D7) with esbuild, so it runs under plain Node.
//   2. Builds the exact same deterministic multichannel bed
//      `test_preview_export_golden.py::_deterministic_bed` generates (see
//      `deterministicBed` below — same formula, ported by hand since
//      matching a NumPy RNG bitstream in JS isn't practical, but this
//      formula has no RNG to begin with).
//   3. Renders it through `buildMasteringGraph` on a real `OfflineAudioContext`
//      (via `node-web-audio-api`, a spec-compliant Web Audio implementation
//      for Node — not a mock or re-implementation) with the same mastering
//      config `_mastering_config()` uses.
//   4. Measures BS.1770-ish integrated loudness, an approximate true peak,
//      and per-channel RMS, and writes them to
//      tests/fixtures/preview_export_golden/web_bed_metrics.json in the
//      shape `test_preview_export_golden.py::_metrics` produces.
//
// Run: `node web/scripts/render-preview-golden.mjs` (or via
// `npm run golden:render` from `web/`).
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import esbuild from "esbuild";
import { OfflineAudioContext } from "node-web-audio-api";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webRoot, "..");

const SR = 48000;
const DURATION_S = 5; // must match tests/test_preview_export_golden.py::_DURATION_S

// upmixer/formats.py SURROUND_714 channel order: FL, FR, C, LFE, BL, BR,
// SL, SR, TFL, TFR, TBL, TBR (back before side, unlike 7.1/7.1.2).
const CHANNELS = ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"];

// upmixer/loudness.py _CH_WEIGHT: L/R/C/back/height = 1.0, LFE excluded
// (0), ear-level side (SL/SR) = 1.41 (+1.5 dB) per BS.1770-5 Annex 3 Table 5.
const LOUDNESS_WEIGHT = { FL: 1, FR: 1, C: 1, LFE: 0, BL: 1, BR: 1, SL: 1.41, SR: 1.41, TFL: 1, TFR: 1, TBL: 1, TBR: 1 };

/** Same formula as test_preview_export_golden.py::_deterministic_bed — a
 * fixed multi-tone signal (not RNG-based noise, see that function's
 * docstring for why) so both engines process byte-identical input without
 * needing to match a NumPy PCG64 bitstream in JS. */
function deterministicBed(sr, durationS) {
  const n = Math.floor(sr * durationS);
  const channels = {};
  CHANNELS.forEach((name, i) => {
    const baseFreq = 110.0 * (i + 1);
    const data = new Float32Array(n);
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

// --- Minimal BS.1770-flavored loudness + true-peak measurement --------
// Deliberately not a bit-exact port of upmixer/loudness.py (that's Tier 3,
// bounded by this module's tolerance, not Tier 1 — see
// docs/contracts/preview_export_parity.md §3/§5). K-weighting coefficients
// are the published ITU-R BS.1770-4 Annex 1 values (see
// docs/standards/loudness_dsp_bs1770.md), the same public table
// upmixer/loudness.py implements.
const K_STAGE1 = { b0: 1.53512485958697, b1: -2.69169618940638, b2: 1.19839281085285, a1: -1.69065929318241, a2: 0.73248077421585 };
const K_STAGE2 = { b0: 1.0, b1: -2.0, b2: 1.0, a1: -1.99004745483398, a2: 0.99007225036621 };

function biquad(x, c) {
  const y = new Float64Array(x.length);
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) {
    const xi = x[i];
    const yi = c.b0 * xi + c.b1 * x1 + c.b2 * x2 - c.a1 * y1 - c.a2 * y2;
    y[i] = yi;
    x2 = x1; x1 = xi;
    y2 = y1; y1 = yi;
  }
  return y;
}

function kWeight(x) {
  return biquad(biquad(x, K_STAGE1), K_STAGE2);
}

const BLOCK_S = 0.4;
const HOP_S = 0.1;
const ABS_GATE = -70.0;
const REL_GATE_OFFSET = -10.0;

/** BS.1770-style two-pass-gated integrated loudness across the given
 * per-channel arrays, weighted per LOUDNESS_WEIGHT. Mirrors
 * upmixer/loudness.py::measure_integrated_loudness's algorithm shape. */
function measureIntegratedLkfs(channels, sr) {
  const blockLen = Math.floor(BLOCK_S * sr);
  const hopLen = Math.floor(HOP_S * sr);
  const weighted = [];
  for (const name of Object.keys(channels)) {
    const weight = LOUDNESS_WEIGHT[name] ?? 0;
    if (weight === 0) continue;
    weighted.push({ weight, filtered: kWeight(channels[name]) });
  }
  if (weighted.length === 0) return -70.0;

  const n = weighted[0].filtered.length;
  const nBlocks = Math.max(0, Math.floor((n - blockLen) / hopLen) + 1);
  if (nBlocks <= 0) return -70.0;

  const blockPower = new Float64Array(nBlocks);
  for (const { weight, filtered } of weighted) {
    for (let b = 0; b < nBlocks; b++) {
      const start = b * hopLen;
      let sum = 0;
      for (let i = 0; i < blockLen; i++) {
        const v = filtered[start + i];
        sum += v * v;
      }
      blockPower[b] += weight * (sum / blockLen);
    }
  }

  const blockLkfs = new Float64Array(nBlocks);
  for (let b = 0; b < nBlocks; b++) blockLkfs[b] = -0.691 + 10 * Math.log10(Math.max(blockPower[b], 1e-30));

  const absMask = [];
  for (let b = 0; b < nBlocks; b++) if (blockLkfs[b] >= ABS_GATE) absMask.push(b);
  if (absMask.length === 0) return -70.0;

  const meanAbs = absMask.reduce((s, b) => s + blockPower[b], 0) / absMask.length;
  const ungatedLkfs = -0.691 + 10 * Math.log10(Math.max(meanAbs, 1e-30));

  const relMask = absMask.filter((b) => blockLkfs[b] >= ungatedLkfs + REL_GATE_OFFSET);
  const gated = relMask.length > 0 ? relMask : absMask;
  const meanGated = gated.reduce((s, b) => s + blockPower[b], 0) / gated.length;
  return -0.691 + 10 * Math.log10(Math.max(meanGated, 1e-30));
}

// True-peak measurement (4x-oversampled windowed-sinc, Tier-3 approximation)
// is implemented once, in masteringProfiles.ts's `measureBufferTruePeakDbtp`
// — shared with the live preview's own true-peak safety net
// (useStemPreview.ts) so there's exactly one JS implementation of this
// approximation. `measureTruePeakDbtp` here just applies it per channel and
// takes the max; see `main()` below for where the module is loaded.
function measureTruePeakDbtp(channels, measureBufferTruePeakDbtpFn) {
  let maxDbtp = -Infinity;
  for (const data of Object.values(channels)) {
    const dbtp = measureBufferTruePeakDbtpFn(data);
    if (dbtp > maxDbtp) maxDbtp = dbtp;
  }
  return maxDbtp;
}

function rms(x) {
  let sum = 0;
  for (let i = 0; i < x.length; i++) sum += x[i] * x[i];
  return Math.sqrt(sum / x.length);
}

// --- Bundle a source module with esbuild so it runs under plain Node ----
async function loadBundledModule(entry, tag) {
  const result = await esbuild.build({
    entryPoints: [entry],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    target: "node22",
  });
  const code = result.outputFiles[0].text;
  const tmpFile = path.join(webRoot, "scripts", `.${tag}.bundle.${process.pid}.mjs`);
  fs.writeFileSync(tmpFile, code);
  try {
    return await import(`file://${tmpFile}`);
  } finally {
    fs.unlinkSync(tmpFile);
  }
}

async function loadPreviewGraphModule() {
  return loadBundledModule(path.join(webRoot, "src/features/projects/previewGraph.ts"), "previewGraph");
}

async function loadSpatialModule() {
  return loadBundledModule(path.join(webRoot, "src/lib/spatial.ts"), "spatial");
}

async function loadMasteringProfilesModule() {
  return loadBundledModule(path.join(webRoot, "src/features/projects/masteringProfiles.ts"), "masteringProfiles");
}

// --- Disk-based EQ FIR loader (harness has no browser `fetch`) ---------
async function loadFirFromDisk(ctx, assetName) {
  const filePath = path.join(webRoot, "public/eq_fir", `${assetName}.wav`);
  const bytes = fs.readFileSync(filePath);
  const arrayBuffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  return ctx.decodeAudioData(arrayBuffer);
}

// --- Disk-based decode-filter-set part loader (same pattern as EQ) -----
async function loadDecodeFilterPartFromDisk(ctx, partName) {
  const filePath = path.join(webRoot, "public/hrir", `${partName}.wav`);
  const bytes = fs.readFileSync(filePath);
  const arrayBuffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  return ctx.decodeAudioData(arrayBuffer);
}

// upmixer/binaural/renderer.py BINAURAL_LOUDNESS_MAX_GAIN_DB — the binaural
// collapse's own loudness correction is capped small since the bed is
// already loudness-matched before collapse (see masteringProfiles.ts).
const BINAURAL_LOUDNESS_MAX_GAIN_DB = 6.0;
// upmixer/config.py loudness_target_lkfs default — the Studio/Flat profiles'
// own VOICING_PARAMS.loudnessTargetLkfs is null, so `apply()` in
// useStemPreview.ts falls back to this same default in the live preview.
const LOUDNESS_TARGET_LKFS = -18.0;
// upmixer/config.py lfe_gain default (-10 dB) — see masteringProfiles.ts LFE_GAIN.
const LFE_GAIN = 0.31622776601683794;
const LFE_LOWPASS_HZ = 120;
const BINAURAL_PROFILE = "studio";
// masteringProfiles.ts DECODE_FILTER_SET[BINAURAL_PROFILE] — only the
// "studio" entry is needed since this harness is fixed to that profile.
const DECODE_FILTER_SET_NAME = "studio_o3_decode";

// Mirrors useStemPreview.ts's `loudnessGainFor` exactly — this is the one
// number the live preview actually computes and applies (a single measured
// pre-gain, no true-peak safety net, unlike the backend's `normalize_loudness`
// — see this stage's comment in `main()` for why the harness deliberately
// does not add that safety net either).
function loudnessGainFor(measuredLkfs, targetLkfs, maxGainDb) {
  if (measuredLkfs <= -70) return 1;
  const gainDb = Math.min(targetLkfs - measuredLkfs, maxGainDb);
  return 10 ** (gainDb / 20);
}

async function main() {
  const { buildMasteringGraph } = await loadPreviewGraphModule();
  const { buildSoftLimitCurve, measureBufferTruePeakDbtp } = await loadMasteringProfilesModule();

  const { n, channels: bedSamples } = deterministicBed(SR, DURATION_S);
  const ctx = new OfflineAudioContext(CHANNELS.length, n, SR);

  const merger = ctx.createChannelMerger(CHANNELS.length);
  merger.connect(ctx.destination);

  const channelPorts = new Map();
  const sources = [];
  CHANNELS.forEach((name, index) => {
    const buffer = ctx.createBuffer(1, n, SR);
    buffer.copyToChannel(bedSamples[name], 0);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const output = ctx.createGain();
    output.connect(merger, 0, index);
    channelPorts.set(name, { input: source, output });
    sources.push(source);
  });

  // Same mastering config as test_preview_export_golden.py::_mastering_config.
  const masterConfig = {
    eq: { profile: "spatial-air", strength: 1 },
    compressor: { profile: "glue" },
    bass: { profile: "enhance" },
  };

  const handle = buildMasteringGraph(ctx, channelPorts, masterConfig, new Map(), {
    firLoader: loadFirFromDisk,
  });

  // Let the FIR asset's disk-read + decodeAudioData resolve (see
  // buildMasteringGraph's non-blocking loader comment) before rendering —
  // otherwise the convolver could still be silent when startRendering runs.
  await new Promise((resolve) => setTimeout(resolve, 50));

  sources.forEach((source) => {
    source.start(0);
    source.stop(DURATION_S);
  });

  // Suspend/resume-scheduled polling replaces the live hook's
  // requestAnimationFrame-driven `tick()` — OfflineAudioContext has no real
  // time to poll against, but suspend() lets us run JS at an exact
  // rendered timestamp and read `.reduction` there instead. Ticks are
  // computed as `i / 60` (not accumulated by repeated addition) and kept a
  // couple hops clear of `DURATION_S` — repeated float addition drift can
  // land the last accumulated tick a few ULPs past the render's actual
  // sample count, which node-web-audio-api's `suspend()` rejects.
  const hop = 1 / 60;
  const lastTick = Math.floor(DURATION_S / hop) - 2;
  for (let i = 1; i <= lastTick; i++) {
    const t = i * hop;
    ctx.suspend(t)
      .then(() => {
        handle.applyCompressorReduction();
        ctx.resume();
      })
      .catch(() => {});
  }

  const rendered = await ctx.startRendering();

  const outputChannels = {};
  CHANNELS.forEach((name, index) => {
    outputChannels[name] = rendered.getChannelData(index);
  });

  const metrics = {
    measured_lkfs: measureIntegratedLkfs(outputChannels, SR),
    measured_tp_dbtp: measureTruePeakDbtp(outputChannels, measureBufferTruePeakDbtp),
    channel_rms: Object.fromEntries(CHANNELS.map((name) => [name, rms(outputChannels[name])])),
  };

  const outDir = path.join(repoRoot, "tests/fixtures/preview_export_golden");
  fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, "web_bed_metrics.json");
  fs.writeFileSync(outPath, JSON.stringify(metrics, null, 2));
  console.log(`Wrote ${outPath}`);
  console.log(JSON.stringify(metrics, null, 2));

  // --- Stage 2: binaural collapse + loudness + soft-limit -----------------
  // Feeds the same mastered bed (`outputChannels` above) through the
  // ambisonic-encode -> HOA-decode -> voicing graph `buildBinauralGraph`
  // extracts from useStemPreview.ts's `initialize()`, then reproduces the
  // live preview's own downstream stages exactly: `measureOutputLoudness`'s
  // one-shot LKFS read -> `loudnessGainFor`'s capped gain -> the soft-limit
  // WaveShaper. Mirrors upmixer/binaural/renderer.py::render_binaural_delivery
  // on the Python side (see tests/test_preview_export_golden.py). See Ledger
  // D10/D11 in docs/contracts/preview_export_parity.md.
  const { buildBinauralGraph, createPositionalEncoder, loadDecodeFilterChannels, assignDecodeFilterBuffers } =
    await loadPreviewGraphModule();
  const { speakerCoordinates, positionToAzimuthElevation } = await loadSpatialModule();

  const positionalChannels = CHANNELS.filter((name) => name !== "LFE");
  // Generous tail margin for the decode filters' convolution ringout
  // (~6.1k taps / 0.13s at 48kHz as of this writing) — trimmed back to
  // exactly `n` samples before measurement anyway, matching
  // `decode_to_binaural`'s own truncation to the bed's original length.
  const tailMargin = SR;
  const stageBCtx = new OfflineAudioContext(2, n + tailMargin, SR);

  const binaural = buildBinauralGraph(stageBCtx, BINAURAL_PROFILE);
  positionalChannels.forEach((name) => {
    const buffer = stageBCtx.createBuffer(1, n, SR);
    buffer.copyToChannel(outputChannels[name], 0);
    const source = stageBCtx.createBufferSource();
    source.buffer = buffer;
    const { azim, elev } = positionToAzimuthElevation(speakerCoordinates[name]);
    const encoder = createPositionalEncoder(stageBCtx, azim, elev);
    source.connect(encoder.in);
    encoder.out.connect(binaural.hoaBus);
    source.start(0);
  });

  // LFE: lowpass + gain, summed directly into both of `binaural.preVoicing`'s
  // channels — a ChannelMergerNode sums multiple sources landing on the
  // same input index, reproducing the live preview's LFE wiring (Ledger
  // D11, fixed: before the voicing chain, matching render_binaural's own
  // `left = left + lfe` / `right = right + lfe` ahead of `apply_voicing`).
  const lfeBuffer = stageBCtx.createBuffer(1, n, SR);
  lfeBuffer.copyToChannel(outputChannels.LFE, 0);
  const lfeSource = stageBCtx.createBufferSource();
  lfeSource.buffer = lfeBuffer;
  const lfeLowpass = stageBCtx.createBiquadFilter();
  lfeLowpass.type = "lowpass";
  lfeLowpass.frequency.value = LFE_LOWPASS_HZ;
  const lfeGainNode = stageBCtx.createGain();
  lfeGainNode.gain.value = LFE_GAIN;
  lfeSource.connect(lfeLowpass).connect(lfeGainNode);
  lfeGainNode.connect(binaural.preVoicing, 0, 0);
  lfeGainNode.connect(binaural.preVoicing, 0, 1);
  lfeSource.start(0);

  binaural.output.connect(stageBCtx.destination);

  const decodeChannels = await loadDecodeFilterChannels(
    stageBCtx, DECODE_FILTER_SET_NAME, loadDecodeFilterPartFromDisk,
  );
  assignDecodeFilterBuffers(stageBCtx, binaural.convolverPairs, decodeChannels);

  const stageBRendered = await stageBCtx.startRendering();
  // Truncate to `n` samples — matches Python's `decode_to_binaural`
  // returning `left[:n_samples], right[:n_samples]` rather than keeping the
  // convolution's ringout tail.
  const rawLeft = stageBRendered.getChannelData(0).slice(0, n);
  const rawRight = stageBRendered.getChannelData(1).slice(0, n);

  const preGainLkfs = measureIntegratedLkfs({ FL: rawLeft, FR: rawRight }, SR);
  const gain = loudnessGainFor(preGainLkfs, LOUDNESS_TARGET_LKFS, BINAURAL_LOUDNESS_MAX_GAIN_DB);

  // Stage 3: gain -> soft-limit, in that order (see the "Limiting the raw
  // pre-gain sum would bake in saturation..." comment on this same ordering
  // in useStemPreview.ts) — a real WaveShaperNode with 4x oversampling, not
  // a naive per-sample tanh eval, to match what the browser's native node
  // actually does.
  const stageCCtx = new OfflineAudioContext(2, rawLeft.length, SR);
  const stageCBuffer = stageCCtx.createBuffer(2, rawLeft.length, SR);
  stageCBuffer.copyToChannel(rawLeft, 0);
  stageCBuffer.copyToChannel(rawRight, 1);
  const stageCSource = stageCCtx.createBufferSource();
  stageCSource.buffer = stageCBuffer;
  const gainNode = stageCCtx.createGain();
  gainNode.gain.value = gain;
  const softLimitNode = stageCCtx.createWaveShaper();
  softLimitNode.curve = buildSoftLimitCurve();
  softLimitNode.oversample = "4x";
  stageCSource.connect(gainNode).connect(softLimitNode).connect(stageCCtx.destination);
  stageCSource.start(0);
  const stageCRendered = await stageCCtx.startRendering();

  const finalChannels = {
    FL: stageCRendered.getChannelData(0).slice(0, n),
    FR: stageCRendered.getChannelData(1).slice(0, n),
  };

  const binauralMetrics = {
    measured_lkfs: measureIntegratedLkfs(finalChannels, SR),
    measured_tp_dbtp: measureTruePeakDbtp(finalChannels, measureBufferTruePeakDbtp),
    channel_rms: { FL: rms(finalChannels.FL), FR: rms(finalChannels.FR) },
  };

  const binauralOutPath = path.join(outDir, "web_binaural_metrics.json");
  fs.writeFileSync(binauralOutPath, JSON.stringify(binauralMetrics, null, 2));
  console.log(`Wrote ${binauralOutPath}`);
  console.log(JSON.stringify(binauralMetrics, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
