// Framework-free preview mastering graph — the parity-critical extraction
// from useStemPreview.ts's `buildMasteringTopology`, pulled out so it can
// run under any BaseAudioContext (a live AudioContext for the React preview,
// or an OfflineAudioContext for the headless cross-engine golden-diff
// harness, see docs/contracts/preview_export_parity.md §5 and
// tests/test_preview_export_golden.py) instead of only inside the React
// hook. This module has no React dependency and no browser-only globals
// beyond the Web Audio API itself, so it also runs under Node's
// `node-web-audio-api` polyfill without a browser.
//
// Mirrors upmixer/mastering/chain.py's stage order (EQ -> compression ->
// bass control) on the discrete channel bed — see that module's docstring
// and docs/contracts/preview_export_parity.md §1 for the pipeline map this
// implements.
import {
  BASS_PROFILES,
  buildExciteCurve,
  buildFirEqNode,
  BUTTERWORTH_Q,
  COMP_PROFILES,
  EQ_FIR_ASSETS,
  EXCITE_BLEND,
  fetchEqFirBuffer,
  MID_CUTOFF_HZ,
  MONO_MAKER_STEREO_PAIRS,
  SUB_CUTOFF_HZ,
  type BassProfileName,
  type CompProfileName,
  type EqProfileName,
} from "./masteringProfiles";

/** Per-processing-parameter mastering config — mirrors the shape the
 * project manifest's `mastering` block sends to the preview (see
 * `docs/project_manifest_parity.md`). Individual fields override the named
 * profile's preset the same way `upmixer/mastering/chain.py` lets
 * individual `UpmixConfig` fields override a profile preset. */
export type MasterPreview = {
  loudness?: { normalize?: boolean; target?: number; max_tp?: number };
  eq?: { profile?: string | null; strength?: number };
  compressor?: {
    profile?: string | null;
    threshold_db?: number | null;
    ratio?: number | null;
    attack_ms?: number | null;
    release_ms?: number | null;
    knee_db?: number | null;
    makeup_db?: number | null;
  };
  bass?: {
    profile?: string | null;
    sub_gain_db?: number | null;
    mid_gain_db?: number | null;
    mono_cutoff_hz?: number | null;
    excite?: boolean;
    lfe_gain_db?: number | null;
  };
};

/** One channel's insert point in the caller's graph — audio already
 * upstream of mastering connects to `input`; this module's output connects
 * onward to `output`. In the live preview these are `SpeakerBus.masterIn`/
 * `masterOut`; in the golden-diff harness they're plain per-channel
 * gain nodes wrapping a fixed input buffer and the context destination. */
export type MasteringChannelPort = { input: AudioNode; output: AudioNode };

export type MasteringGraphHandle = {
  /** Every node this call created, for teardown (`node.disconnect()`). */
  nodes: AudioNode[];
  /** Fan-in of every channel's post-EQ signal — connect a keep-alive sink
   * to it if the host context requires every node to reach the
   * destination to keep processing (see `sidechainSink`). */
  sidechainSum: GainNode;
  /** Zero-gain tap driven by the compressor detector; connect this
   * somewhere reaching the context's destination to keep the detector
   * live, exactly like `sidechainSum` — the two are usually chained. */
  sidechainSink: GainNode;
  compressor: DynamicsCompressorNode | null;
  compGains: GainNode[];
  compMakeupGain: number;
  bassActive: boolean;
  bassLfeGainDb: number;
  /** Applies the compressor's current `.reduction` to every `compGains`
   * node. Call this periodically while the graph is live — a live
   * `AudioContext` via `requestAnimationFrame`, or an `OfflineAudioContext`
   * via scheduled `suspend()`/`resume()` (see the golden-diff harness). */
  applyCompressorReduction: () => void;
};

/** Dedupes concurrent/repeat fetches (or, in the harness, disk reads) of the
 * same EQ FIR asset by name — the master bus and every stem addressed with
 * the same profile share one decode. */
export function loadCachedEqBuffer(
  cache: Map<string, Promise<AudioBuffer>>,
  ctx: BaseAudioContext,
  assetName: string,
  loader: (ctx: BaseAudioContext, assetName: string) => Promise<AudioBuffer> = fetchEqFirBuffer,
): Promise<AudioBuffer> {
  let pending = cache.get(assetName);
  if (!pending) {
    pending = loader(ctx, assetName);
    cache.set(assetName, pending);
  }
  return pending;
}

/** Additive band-gain identity: `output = input + band*(gainLin - 1)`,
 * where `band` is `input` passed through a lowpass (optionally then a
 * highpass, for a bandpass band) — the same shelf/peak substitute
 * `upmixer/mastering/bass.py::BassController._apply_band_gain` and
 * `upmixer/utils.py::elevation_eq` use instead of a native shelf/peak
 * filter (`(ch - band) + band*gain_lin`, algebraically identical to the
 * form here). Using this instead of a native `BiquadFilterNode` "lowshelf"/
 * "peaking" type keeps the bass sub/mid stage's frequency response shaped
 * like the backend's, rather than a differently-shaped native shelf. */
function buildAdditiveBandGain(
  ctx: BaseAudioContext,
  input: AudioNode,
  lowpassHz: number,
  gainDb: number,
  highpassHz?: number,
): { output: AudioNode; nodes: AudioNode[] } {
  const gainLin = 10 ** (gainDb / 20);
  const nodes: AudioNode[] = [];
  const lowpass = ctx.createBiquadFilter();
  lowpass.type = "lowpass";
  lowpass.frequency.value = lowpassHz;
  lowpass.Q.value = BUTTERWORTH_Q;
  input.connect(lowpass);
  nodes.push(lowpass);

  let band: AudioNode = lowpass;
  if (highpassHz != null) {
    const highpass = ctx.createBiquadFilter();
    highpass.type = "highpass";
    highpass.frequency.value = highpassHz;
    highpass.Q.value = BUTTERWORTH_Q;
    band.connect(highpass);
    band = highpass;
    nodes.push(highpass);
  }

  const bandGain = ctx.createGain();
  bandGain.gain.value = gainLin - 1;
  band.connect(bandGain);
  nodes.push(bandGain);

  const output = ctx.createGain();
  input.connect(output);
  bandGain.connect(output);
  nodes.push(output);

  return { output, nodes };
}

export type BuildMasteringGraphOptions = {
  /** Lets a non-browser host (the Node golden-diff harness) supply its own
   * asset loader instead of the browser-`fetch`-based `fetchEqFirBuffer`
   * default. */
  firLoader?: (ctx: BaseAudioContext, assetName: string) => Promise<AudioBuffer>;
  /** Reuse existing, caller-owned sidechain nodes across rebuilds instead
   * of creating fresh ones — the live React hook creates `sum`/`sink` once
   * in its graph-init pass and connects `sink`'s *output* to its mixdown
   * bus a single time, then calls this function again on every mastering
   * config change; passing the same nodes back in each time preserves that
   * downstream wiring instead of orphaning it. This function clears `sum`'s
   * own outgoing connections before rewiring it (mirroring the original
   * `sum.disconnect()` at the top of every rebuild) but never touches
   * `sink`'s connections — only what feeds into it. Omit this for a
   * single-shot build (e.g. the golden-diff harness), which gets fresh
   * nodes included in the returned `nodes` array for teardown. */
  sidechain?: { sum: GainNode; sink: GainNode };
};

/** Builds the EQ -> compressor -> bass-shelf (incl. mono-maker/exciter)
 * chain between each entry in `channelPorts`' `input` and `output`, exactly
 * mirroring `upmixer/mastering/chain.py`'s stage order and
 * `upmixer/mastering/bass.py`'s mono-maker identity (Ledger D5). Stages are
 * entirely omitted when their config profile is unset, same as the
 * backend. Returns the built handle instead of writing to refs, so both
 * the live React hook and a headless harness can call this the same way.
 *
 * Callers are responsible for clearing `channelPorts`' own prior
 * connections (e.g. `port.input.disconnect()`) and disconnecting any nodes
 * from a previous call's `nodes` array before rebuilding — this function
 * only wires a fresh topology, it doesn't tear down an old one, since it
 * has no way to know which `nodes` array preceded it. */
export function buildMasteringGraph(
  ctx: BaseAudioContext,
  channelPorts: Map<string, MasteringChannelPort>,
  mastering: MasterPreview | undefined,
  firBufferCache: Map<string, Promise<AudioBuffer>>,
  options: BuildMasteringGraphOptions = {},
): MasteringGraphHandle {
  const created: AudioNode[] = [];
  const newCompGains: GainNode[] = [];
  const { firLoader, sidechain } = options;
  const sum = sidechain?.sum ?? ctx.createGain();
  const sink = sidechain?.sink ?? ctx.createGain();
  if (sidechain) {
    sum.disconnect();
  } else {
    sink.gain.value = 0;
    created.push(sum, sink);
  }

  const eqCfg = mastering?.eq;
  const eqAssetName = eqCfg?.profile && eqCfg.profile in EQ_FIR_ASSETS
    ? EQ_FIR_ASSETS[eqCfg.profile as EqProfileName]
    : null;
  const eqStrength = eqCfg?.strength ?? 1;

  const compCfg = mastering?.compressor;
  const compPreset = compCfg?.profile && compCfg.profile in COMP_PROFILES
    ? COMP_PROFILES[compCfg.profile as CompProfileName]
    : null;

  const bassCfg = mastering?.bass;
  const bassPreset = bassCfg?.profile && bassCfg.profile in BASS_PROFILES
    ? BASS_PROFILES[bassCfg.profile as BassProfileName]
    : undefined;
  const bassActive = Boolean(bassPreset) || Boolean(
    bassCfg && (
      bassCfg.sub_gain_db != null || bassCfg.mid_gain_db != null
      || bassCfg.mono_cutoff_hz != null || bassCfg.lfe_gain_db != null || bassCfg.excite
    ),
  );
  const subGainDb = bassCfg?.sub_gain_db ?? bassPreset?.sub_gain_db ?? 0;
  const midGainDb = bassCfg?.mid_gain_db ?? bassPreset?.mid_gain_db ?? 0;
  const lfeGainDb = bassCfg?.lfe_gain_db ?? bassPreset?.lfe_gain_db ?? 0;
  const monoCutoffHz = bassCfg?.mono_cutoff_hz ?? bassPreset?.mono_cutoff_hz ?? null;
  const exciteActive = bassActive && Boolean(bassCfg?.excite || bassPreset?.excite);

  // Channels awaiting the mono-maker's cross-channel pairing below, keyed
  // by channel name — populated instead of connecting straight to
  // `port.output` when mono-maker is active and the channel belongs to a
  // MONO_MAKER_STEREO_PAIRS entry.
  const pendingMonoChain = new Map<string, AudioNode>();

  for (const [channel, port] of channelPorts.entries()) {
    let postEq: AudioNode = port.input;
    if (eqAssetName) {
      const firEq = buildFirEqNode(ctx, eqStrength);
      created.push(...firEq.nodes);
      port.input.connect(firEq.input);
      postEq = firEq.output;
      // Non-blocking: the convolver stays silent on its wet path until
      // this resolves (see `buildFirEqNode`), so audio can play
      // immediately (or, offline, still render deterministically once the
      // caller awaits this promise before `startRendering()`) while the
      // FIR asset fetches/decodes.
      void loadCachedEqBuffer(firBufferCache, ctx, eqAssetName, firLoader)
        .then((buffer) => { firEq.convolver.buffer = buffer; })
        .catch(() => {});
    }
    postEq.connect(sum);

    const compGain = ctx.createGain();
    compGain.gain.value = 1;
    created.push(compGain);
    newCompGains.push(compGain);
    postEq.connect(compGain);

    const bassNodes: AudioNode[] = [];
    let chainEnd: AudioNode = compGain;
    if (bassActive && subGainDb !== 0) {
      const stage = buildAdditiveBandGain(ctx, chainEnd, SUB_CUTOFF_HZ, subGainDb);
      bassNodes.push(...stage.nodes);
      chainEnd = stage.output;
    }
    if (bassActive && midGainDb !== 0) {
      const stage = buildAdditiveBandGain(ctx, chainEnd, MID_CUTOFF_HZ, midGainDb, SUB_CUTOFF_HZ);
      bassNodes.push(...stage.nodes);
      chainEnd = stage.output;
    }
    created.push(...bassNodes);
    const inMonoPair = monoCutoffHz != null
      && MONO_MAKER_STEREO_PAIRS.some((pair) => pair.includes(channel));
    if (inMonoPair) {
      pendingMonoChain.set(channel, chainEnd);
    } else {
      chainEnd.connect(port.output);
    }

    if (exciteActive) {
      const lowpass = ctx.createBiquadFilter();
      lowpass.type = "lowpass";
      lowpass.frequency.value = SUB_CUTOFF_HZ;
      const shaper = ctx.createWaveShaper();
      shaper.curve = buildExciteCurve();
      const blend = ctx.createGain();
      blend.gain.value = EXCITE_BLEND;
      compGain.connect(lowpass);
      lowpass.connect(shaper);
      shaper.connect(blend);
      blend.connect(port.output);
      created.push(lowpass, shaper, blend);
    }
  }

  // Bass mono-maker: for each stereo pair with both sides deferred above,
  // lowpass each side at monoCutoffHz, average the two lowpassed copies to
  // mono, and swap each side's own low band for that shared mono band —
  // out_L = L + 0.5*(lowR - lowL), out_R = R + 0.5*(lowL - lowR) — the same
  // identity upmixer/mastering/bass.py's BassController.process computes
  // as `mono_bass + (channel - own_low_band)`. A pair with only one side
  // present just passes that side through unprocessed, same as the
  // backend's `if l_key not in out or r_key not in out: continue` guard.
  if (monoCutoffHz != null) {
    for (const [leftKey, rightKey] of MONO_MAKER_STEREO_PAIRS) {
      const leftChain = pendingMonoChain.get(leftKey);
      const rightChain = pendingMonoChain.get(rightKey);
      const leftPort = channelPorts.get(leftKey);
      const rightPort = channelPorts.get(rightKey);
      if (!leftChain || !rightChain || !leftPort || !rightPort) continue;

      // Cascaded pair (not a single biquad): the backend applies this
      // lowpass via `sosfiltfilt` (forward + backward, zero-phase) once
      // buffers exceed 15 samples — magnitude-wise that squares the
      // single-pass response, i.e. an effective 4th-order roll-off, not
      // 2nd-order. A single BiquadFilterNode here leaks noticeably more
      // energy near the cutoff than that, which on decorrelated
      // multichannel content was enough to flip the mono-maker's net level
      // effect from a slight cut (backend) to a slight boost (single-stage
      // preview) — found via the golden-diff harness, Ledger D9.
      const lowL = ctx.createBiquadFilter();
      lowL.type = "lowpass";
      lowL.frequency.value = monoCutoffHz;
      lowL.Q.value = BUTTERWORTH_Q;
      const lowL2 = ctx.createBiquadFilter();
      lowL2.type = "lowpass";
      lowL2.frequency.value = monoCutoffHz;
      lowL2.Q.value = BUTTERWORTH_Q;
      const lowR = ctx.createBiquadFilter();
      lowR.type = "lowpass";
      lowR.frequency.value = monoCutoffHz;
      lowR.Q.value = BUTTERWORTH_Q;
      const lowR2 = ctx.createBiquadFilter();
      lowR2.type = "lowpass";
      lowR2.frequency.value = monoCutoffHz;
      lowR2.Q.value = BUTTERWORTH_Q;
      leftChain.connect(lowL).connect(lowL2);
      rightChain.connect(lowR).connect(lowR2);

      // diff = 0.5*(lowR - lowL); out_L = L + diff, out_R = R - diff.
      const diff = ctx.createGain();
      diff.gain.value = 1;
      const halfR = ctx.createGain();
      halfR.gain.value = 0.5;
      const negHalfL = ctx.createGain();
      negHalfL.gain.value = -0.5;
      lowR2.connect(halfR).connect(diff);
      lowL2.connect(negHalfL).connect(diff);
      const diffInv = ctx.createGain();
      diffInv.gain.value = -1;
      diff.connect(diffInv);

      leftChain.connect(leftPort.output);
      diff.connect(leftPort.output);
      rightChain.connect(rightPort.output);
      diffInv.connect(rightPort.output);

      created.push(lowL, lowL2, lowR, lowR2, diff, halfR, negHalfL, diffInv);
    }
    // Any deferred channel without its pair partner present in this
    // layout still needs to reach its output.
    for (const [channel, chainEnd] of pendingMonoChain.entries()) {
      const hasPartner = MONO_MAKER_STEREO_PAIRS.some(
        (pair) => pair.includes(channel) && pair.every((ch) => pendingMonoChain.has(ch)),
      );
      if (!hasPartner) {
        const port = channelPorts.get(channel);
        if (port) chainEnd.connect(port.output);
      }
    }
  }

  let compressor: DynamicsCompressorNode | null = null;
  let compMakeupGain = 1;
  if (compPreset || compCfg?.profile) {
    const preset = compPreset ?? { threshold_db: -22, ratio: 1.5, attack_ms: 30, release_ms: 300, knee_db: 9, makeup_db: 0 };
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = compCfg?.threshold_db ?? preset.threshold_db;
    comp.ratio.value = compCfg?.ratio ?? preset.ratio;
    comp.attack.value = (compCfg?.attack_ms ?? preset.attack_ms) / 1000;
    comp.release.value = (compCfg?.release_ms ?? preset.release_ms) / 1000;
    comp.knee.value = compCfg?.knee_db ?? preset.knee_db;
    // `sum` is a raw fan-in of every channel's post-EQ signal, so for N
    // correlated channels its level is ~N times a single channel's — up to
    // +20dB too hot into the detector for an 11-channel bed.
    // upmixer/mastering/compressor.py detects on
    // sqrt(sum(ch^2)/n_ch) (an RMS *average* across channels), not a raw
    // sum — this gain brings the sidechain back down to that same
    // per-channel-average level before it reaches the detector.
    const detectorScale = ctx.createGain();
    detectorScale.gain.value = 1 / Math.sqrt(Math.max(channelPorts.size, 1));
    created.push(detectorScale);
    sum.connect(detectorScale);
    detectorScale.connect(comp);
    comp.connect(sink);
    created.push(comp);
    compressor = comp;
    // makeup_db is a static addition, not part of the detected reduction —
    // the periodic `applyCompressorReduction` call multiplies it into
    // every channel's comp-gain alongside the live reduction, since setting
    // `.gain.value` here would just be overwritten on the next poll.
    compMakeupGain = 10 ** ((compCfg?.makeup_db ?? preset.makeup_db) / 20);
    newCompGains.forEach((g) => { g.gain.value = compMakeupGain; });
  } else {
    newCompGains.forEach((g) => { g.gain.value = 1; });
  }

  const capturedCompressor = compressor;
  const capturedMakeup = compMakeupGain;
  const applyCompressorReduction = () => {
    if (!capturedCompressor || newCompGains.length === 0) return;
    // `.reduction` must be <= 0 dB per the Web Audio spec (it never applies
    // makeup gain on its own) — clamped defensively since it's the one
    // input here this module doesn't fully control, and at least one
    // non-browser Web Audio implementation (node-web-audio-api, used by
    // the golden-diff harness, see docs/contracts/preview_export_parity.md
    // Ledger D8) has been observed returning small positive values for a
    // sub-threshold signal instead of 0.
    const reductionDb = Math.min(0, capturedCompressor.reduction);
    const gain = 10 ** (reductionDb / 20) * capturedMakeup;
    for (const node of newCompGains) node.gain.value = gain;
  };

  return {
    nodes: created,
    sidechainSum: sum,
    sidechainSink: sink,
    compressor,
    compGains: newCompGains,
    compMakeupGain,
    bassActive,
    bassLfeGainDb: bassActive ? lfeGainDb : 0,
    applyCompressorReduction,
  };
}
