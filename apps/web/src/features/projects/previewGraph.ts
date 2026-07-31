// Framework-free preview mastering + binaural graph, extracted from
// audioEngine.ts so it also runs under an OfflineAudioContext (Node's
// node-web-audio-api polyfill) for the golden-diff harness — see
// docs/contracts/preview_export_parity.md §5 and tests/test_preview_export_golden.py.
import numericLib from "numeric";
// ambi-monoEncoder's shared SH util expects `numeric` as a bare global, not an
// import — set once so this module also works standalone (golden-diff harness).
(globalThis as typeof globalThis & { numeric?: unknown }).numeric ??= numericLib;
import AmbiMonoEncoderImport from "ambisonics/dist/ambi-monoEncoder";

// Vite unwraps this package's CJS `exports.default` to the class itself, but
// esbuild's Node interop (golden-diff harness bundle) leaves it wrapped as
// `{ __esModule, default: monoEncoder }` — this makes both bundlers work.
const AmbiMonoEncoder = (
  typeof AmbiMonoEncoderImport === "function"
    ? AmbiMonoEncoderImport
    : (AmbiMonoEncoderImport as unknown as { default: typeof AmbiMonoEncoderImport }).default
);
import {
  ACN12_INDEX,
  ACN12_N3D_CORRECTION,
  AMBISONIC_ORDER,
  buildExciteCurve,
  buildFirEqNode,
  buildVoicingChain,
  applyVoicingParams,
  BUTTERWORTH_Q,
  DECODE_FILTER_SPLITS,
  fetchEqFirBuffer,
  MONO_MAKER_STEREO_PAIRS,
  N_ACN_CHANNELS,
  type BassProfileName,
  type CompProfileName,
  type EngineConstants,
  type EqProfileName,
  type SpatialProfile,
  type TransauralProfile,
  type VoicingChain,
} from "./masteringProfiles";

/** Per-processing-parameter mastering config — mirrors the shape the
 * project manifest's `mastering` block sends to the preview (see
 * `docs/project_manifest_parity.md`). Individual fields override the named
 * profile's preset the same way `upmixer/mastering/chain.py` lets
 * individual `UpmixConfig` fields override a profile preset. */
export type MasterPreview = {
  loudness?: { normalize?: boolean; target?: number; max_tp?: number };
  eq?: { profile?: string | null; strength?: number };
  // Server-precomputed FIR asset, unlike eq's static named-profile FIR — see
  // docs/contracts/preview_export_parity.md Ledger D12.
  match_reference?: {
    fir_url?: string | null;
    channels?: string[];
    rms_gain_db?: number;
    strength?: number;
    spectrum?: boolean;
    rms?: boolean;
  };
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

/** Fetches a project's server-precomputed reference-match FIR asset
 * (`MasterPreview.match_reference.fir_url`) — a single multichannel WAV —
 * and splits it into one mono `AudioBuffer` per channel, keyed by
 * `channels[i]`: the fixed order
 * `upmixer_web/project_storage.py::write_reference_match` wrote them in.
 * Mirrors `loadDecodeFilterChannels`'s disk/browser split pattern, but as a
 * single fetch since this asset (unlike the 32-channel decode filter set)
 * fits under the browser's per-file multichannel decode cap. */
export async function fetchRefMatchFirBuffers(
  ctx: BaseAudioContext,
  firUrl: string,
  channels: string[],
): Promise<Map<string, AudioBuffer>> {
  const response = await fetch(firUrl);
  if (!response.ok) throw new Error(`Reference-match FIR asset missing: ${firUrl}`);
  const data = await response.arrayBuffer();
  const decoded = await ctx.decodeAudioData(data);
  const buffers = new Map<string, AudioBuffer>();
  channels.forEach((name, index) => {
    if (index >= decoded.numberOfChannels) return;
    const mono = ctx.createBuffer(1, decoded.length, decoded.sampleRate);
    mono.copyToChannel(decoded.getChannelData(index), 0);
    buffers.set(name, mono);
  });
  return buffers;
}

/** Dedupes concurrent/repeat fetches of a project's reference-match FIR
 * asset by `fir_url` — same cache-by-key pattern as `loadCachedEqBuffer`,
 * keyed on the URL (which changes whenever the server recomputes the asset)
 * instead of a fixed profile name. */
export function loadCachedRefMatchBuffers(
  cache: Map<string, Promise<Map<string, AudioBuffer>>>,
  ctx: BaseAudioContext,
  firUrl: string,
  channels: string[],
  loader: (
    ctx: BaseAudioContext, firUrl: string, channels: string[],
  ) => Promise<Map<string, AudioBuffer>> = fetchRefMatchFirBuffers,
): Promise<Map<string, AudioBuffer>> {
  let pending = cache.get(firUrl);
  if (!pending) {
    pending = loader(ctx, firUrl, channels);
    cache.set(firUrl, pending);
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
  /** Cache for `mastering.match_reference`'s FIR asset, keyed by `fir_url`
   * (see `loadCachedRefMatchBuffers`) — same one-cache-per-context lifetime
   * as `firBufferCache` above; the caller owns it so it persists across
   * rebuilds. Required whenever `mastering.match_reference.fir_url` is set. */
  refMatchBufferCache?: Map<string, Promise<Map<string, AudioBuffer>>>;
  /** Lets a non-browser host supply its own reference-match FIR loader,
   * mirroring `firLoader` above. */
  refMatchLoader?: (
    ctx: BaseAudioContext, firUrl: string, channels: string[],
  ) => Promise<Map<string, AudioBuffer>>;
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
  constants: EngineConstants,
  options: BuildMasteringGraphOptions = {},
): MasteringGraphHandle {
  const created: AudioNode[] = [];
  const newCompGains: GainNode[] = [];
  const { firLoader, sidechain, refMatchBufferCache, refMatchLoader } = options;
  const sum = sidechain?.sum ?? ctx.createGain();
  const sink = sidechain?.sink ?? ctx.createGain();
  if (sidechain) {
    sum.disconnect();
  } else {
    sink.gain.value = 0;
    created.push(sum, sink);
  }

  // Reference match (mastering step 0, before named EQ) — a server-real
  // FIR bank + RMS gain, not a re-derived approximation (see
  // `MasterPreview.match_reference`'s doc comment). `refMatchBuffers`
  // resolves non-blocking, same pattern as the named-EQ FIR below.
  const refCfg = mastering?.match_reference;
  const refMatchRmsGainLin = refCfg?.rms ? 10 ** ((refCfg.rms_gain_db ?? 0) / 20) : 1;
  const refMatchSpectrumActive = Boolean(
    refCfg?.spectrum && refCfg.fir_url && (refCfg.strength ?? 0) > 0
    && refCfg.channels && refCfg.channels.length > 0,
  );
  const refMatchStrength = refCfg?.strength ?? 1;
  const refMatchBuffers = refMatchSpectrumActive
    ? loadCachedRefMatchBuffers(
      refMatchBufferCache ?? new Map(), ctx, refCfg!.fir_url as string, refCfg!.channels!, refMatchLoader,
    )
    : null;

  const eqCfg = mastering?.eq;
  const eqAssetName = eqCfg?.profile && eqCfg.profile in constants.eqFirAssets
    ? constants.eqFirAssets[eqCfg.profile as EqProfileName]
    : null;
  const eqStrength = eqCfg?.strength ?? 1;

  const compCfg = mastering?.compressor;
  const compPreset = compCfg?.profile && compCfg.profile in constants.compProfiles
    ? constants.compProfiles[compCfg.profile as CompProfileName]
    : null;

  const bassCfg = mastering?.bass;
  const bassPreset = bassCfg?.profile && bassCfg.profile in constants.bassProfiles
    ? constants.bassProfiles[bassCfg.profile as BassProfileName]
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
    // Reference match runs first, matching upmixer/mastering/chain.py's
    // order: RMS scalar (all channels, incl. LFE), then per-channel
    // spectral FIR — see match_reference.py::ReferenceMatchProcessor.process.
    let postRefMatch: AudioNode = port.input;
    if (refCfg?.rms && refMatchRmsGainLin !== 1) {
      const rmsGain = ctx.createGain();
      rmsGain.gain.value = refMatchRmsGainLin;
      created.push(rmsGain);
      postRefMatch.connect(rmsGain);
      postRefMatch = rmsGain;
    }
    if (refMatchBuffers) {
      const firRef = buildFirEqNode(ctx, refMatchStrength);
      created.push(...firRef.nodes);
      postRefMatch.connect(firRef.input);
      postRefMatch = firRef.output;
      void refMatchBuffers
        .then((buffers) => {
          const buffer = buffers.get(channel);
          if (buffer) firRef.convolver.buffer = buffer;
        })
        .catch(() => {});
    }

    let postEq: AudioNode = postRefMatch;
    if (eqAssetName) {
      const firEq = buildFirEqNode(ctx, eqStrength);
      created.push(...firEq.nodes);
      postRefMatch.connect(firEq.input);
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
      const stage = buildAdditiveBandGain(ctx, chainEnd, constants.subCutoffHz, subGainDb);
      bassNodes.push(...stage.nodes);
      chainEnd = stage.output;
    }
    if (bassActive && midGainDb !== 0) {
      const stage = buildAdditiveBandGain(ctx, chainEnd, constants.midCutoffHz, midGainDb, constants.subCutoffHz);
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
      lowpass.frequency.value = constants.subCutoffHz;
      const shaper = ctx.createWaveShaper();
      shaper.curve = buildExciteCurve(constants.exciteDrive);
      const blend = ctx.createGain();
      blend.gain.value = constants.exciteBlend;
      compGain.connect(lowpass);
      lowpass.connect(shaper);
      shaper.connect(blend);
      blend.connect(port.output);
      created.push(lowpass, shaper, blend);
    }
  }

  // out_L = L + 0.5*(lowR - lowL), out_R = R + 0.5*(lowL - lowR) — same identity
  // as upmixer/mastering/bass.py's `mono_bass + (channel - own_low_band)`.
  if (monoCutoffHz != null) {
    for (const [leftKey, rightKey] of MONO_MAKER_STEREO_PAIRS) {
      const leftChain = pendingMonoChain.get(leftKey);
      const rightChain = pendingMonoChain.get(rightKey);
      const leftPort = channelPorts.get(leftKey);
      const rightPort = channelPorts.get(rightKey);
      if (!leftChain || !rightChain || !leftPort || !rightPort) continue;

      // Cascaded pair, not a single biquad: the backend's sosfiltfilt is
      // zero-phase (forward+backward), squaring the response to an effective
      // 4th-order rolloff — a single stage flipped the net level effect from
      // a cut to a boost on decorrelated content. Found via golden-diff, Ledger D9.
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
    // `sum` is a raw N-channel fan-in (~+20dB too hot for 11ch); this brings it
    // back to compressor.py's sqrt(sum(ch^2)/n_ch) per-channel-average level.
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
    // Clamped defensively: node-web-audio-api (golden-diff harness) has been
    // observed returning small positive values instead of 0 — Ledger D8.
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

// --- Binaural collapse graph (ambisonic encode -> HOA decode -> voicing) --
//
// Extracts everything downstream of the per-speaker encoders in
// audioEngine.ts's `initialize()` (which still owns the AmbiMonoEncoders
// themselves). Mirrors upmixer/binaural/renderer.py::render_binaural's signal
// graph. LFE has no input parameter here; see `preVoicing` below and
// docs/contracts/preview_export_parity.md Ledger D11 for why it connects there.
export type BinauralGraphHandle = {
  /** Feed each positional channel's `AmbiMonoEncoder.out` into this. */
  hoaBus: GainNode;
  /** Pre-voicing stereo merge point (2 discrete channels): the decoded HOA
   * bus summed to binaural stereo, before the profile voicing chain runs.
   * Connect an LFE send into this node's input 0 *and* 1 (a
   * ChannelMergerNode sums multiple sources landing on the same input
   * index) to reproduce `render_binaural`'s LFE-before-voicing order. Only
   * meaningful for the binaural render — the stereo (BS.775) and native
   * paths don't route through this graph at all, so an LFE send wired only
   * here (not also at `output`/`mergePoint`) naturally excludes LFE from
   * those paths too, matching BS.775's own exclusion. */
  preVoicing: AudioNode;
  /** Post-voicing stereo output (2 discrete channels via a ChannelMerger) —
   * connect onward to the loudness-gain stage. */
  output: AudioNode;
  voicing: VoicingChain;
  convolverPairs: { left: ConvolverNode; right: ConvolverNode; preGain: GainNode | null }[];
  /** Every node this call created besides `hoaBus`/`preVoicing`/`output`/
   * `voicing`'s own nodes (already covered by `voicing.nodes`) — for
   * teardown. */
  nodes: AudioNode[];
};

export function buildBinauralGraph(ctx: BaseAudioContext, profile: SpatialProfile, constants: EngineConstants): BinauralGraphHandle {
  const nodes: AudioNode[] = [];

  const hoaBus = ctx.createGain();
  hoaBus.channelCount = N_ACN_CHANNELS;
  hoaBus.channelCountMode = "explicit";
  hoaBus.channelInterpretation = "discrete";
  const hoaSplitter = ctx.createChannelSplitter(N_ACN_CHANNELS);
  hoaBus.connect(hoaSplitter);
  nodes.push(hoaSplitter);

  const decodeSumLeft = ctx.createGain();
  const decodeSumRight = ctx.createGain();
  nodes.push(decodeSumLeft, decodeSumRight);
  const convolverPairs: BinauralGraphHandle["convolverPairs"] = [];
  for (let acn = 0; acn < N_ACN_CHANNELS; acn++) {
    const left = ctx.createConvolver();
    const right = ctx.createConvolver();
    left.normalize = false;
    right.normalize = false;
    let preGain: GainNode | null = null;
    if (acn === ACN12_INDEX) {
      preGain = ctx.createGain();
      preGain.gain.value = ACN12_N3D_CORRECTION;
      hoaSplitter.connect(preGain, acn);
      preGain.connect(left);
      preGain.connect(right);
      nodes.push(preGain);
    } else {
      hoaSplitter.connect(left, acn);
      hoaSplitter.connect(right, acn);
    }
    left.connect(decodeSumLeft);
    right.connect(decodeSumRight);
    convolverPairs.push({ left, right, preGain });
    nodes.push(left, right);
  }
  const decodeMerger = ctx.createChannelMerger(2);
  decodeSumLeft.connect(decodeMerger, 0, 0);
  decodeSumRight.connect(decodeMerger, 0, 1);
  nodes.push(decodeMerger);

  const voicingSplitter = ctx.createChannelSplitter(2);
  decodeMerger.connect(voicingSplitter);
  const voicingLeftTap = ctx.createGain();
  const voicingRightTap = ctx.createGain();
  voicingSplitter.connect(voicingLeftTap, 0);
  voicingSplitter.connect(voicingRightTap, 1);
  nodes.push(voicingSplitter, voicingLeftTap, voicingRightTap);

  const voicing = buildVoicingChain(ctx, voicingLeftTap, voicingRightTap);
  applyVoicingParams(voicing, constants.voicingParams[profile]);
  const voicingMerger = ctx.createChannelMerger(2);
  voicing.left.connect(voicingMerger, 0, 0);
  voicing.right.connect(voicingMerger, 0, 1);
  nodes.push(voicingMerger);

  return { hoaBus, preVoicing: decodeMerger, output: voicingMerger, voicing, convolverPairs, nodes };
}

/** One positional channel's fixed direction, in the same
 * (azimuth-degrees, elevation-degrees) convention `AmbiMonoEncoder.azim`/
 * `.elev` expect — see `web/src/lib/spatial.ts::positionToAzimuthElevation`.
 * Passed in by the caller (rather than imported from `spatial.ts` here) so
 * this module stays decoupled from that file's own dependency surface;
 * `useStemPreview.ts` already computes this per channel for its
 * `SpeakerBus` construction and can pass the same values through. */
export function createPositionalEncoder(
  ctx: BaseAudioContext,
  azimuthDeg: number,
  elevationDeg: number,
): InstanceType<typeof AmbiMonoEncoder> {
  const encoder = new AmbiMonoEncoder(ctx, AMBISONIC_ORDER);
  encoder.azim = azimuthDeg;
  encoder.elev = elevationDeg;
  encoder.updateGains();
  return encoder;
}

/** Fetches and decodes a profile's 4-part decode filter set (see
 * `DECODE_FILTER_SPLITS`) into the flat 32-channel `[ACN0_L, ACN0_R, ...,
 * ACN15_L, ACN15_R]` layout `assignDecodeFilterBuffers` expects.
 * `partLoader` decouples the browser `fetch`-based default
 * (`useStemPreview.ts`'s `fetchDecodeFilterChannels`) from the golden-diff
 * harness's disk read, mirroring the `firLoader` pattern above. */
export async function loadDecodeFilterChannels(
  ctx: BaseAudioContext,
  name: string,
  partLoader: (ctx: BaseAudioContext, partName: string) => Promise<AudioBuffer>,
): Promise<Float32Array[]> {
  const parts = await Promise.all(
    DECODE_FILTER_SPLITS.map((suffix) => partLoader(ctx, `${name}_${suffix}`)),
  );
  const channels: Float32Array[] = [];
  for (const buffer of parts) {
    for (let ch = 0; ch < buffer.numberOfChannels; ch++) channels.push(buffer.getChannelData(ch));
  }
  if (channels.length !== 2 * N_ACN_CHANNELS) {
    throw new Error(`Decode filter set '${name}' has ${channels.length} channels, expected ${2 * N_ACN_CHANNELS}`);
  }
  return channels;
}

/** Dedupes concurrent/repeat fetches of the same profile's decode filter set
 * by `name` — only three possible keys (the decode filter set's values), so
 * this makes an A->B->A profile switch free after the first load. Same
 * cache-by-key pattern as `loadCachedEqBuffer`. */
export function loadCachedDecodeFilterChannels(
  cache: Map<string, Promise<Float32Array[]>>,
  ctx: BaseAudioContext,
  name: string,
  partLoader: (ctx: BaseAudioContext, partName: string) => Promise<AudioBuffer>,
): Promise<Float32Array[]> {
  let pending = cache.get(name);
  if (!pending) {
    pending = loadDecodeFilterChannels(ctx, name, partLoader);
    cache.set(name, pending);
  }
  return pending;
}

/** Assigns a loaded decode filter set's 32 flat channels onto a
 * `buildBinauralGraph` handle's `convolverPairs`, two per ACN index (L, R) —
 * the non-blocking buffer-assignment half of the pattern `buildFirEqNode`
 * above also uses: the graph is already wired and silent until this runs. */
export function assignDecodeFilterBuffers(
  ctx: BaseAudioContext,
  convolverPairs: BinauralGraphHandle["convolverPairs"],
  channels: Float32Array[],
): void {
  const length = channels[0].length;
  convolverPairs.forEach((pair, acn) => {
    const leftBuffer = ctx.createBuffer(1, length, ctx.sampleRate);
    leftBuffer.copyToChannel(channels[2 * acn], 0);
    const rightBuffer = ctx.createBuffer(1, length, ctx.sampleRate);
    rightBuffer.copyToChannel(channels[2 * acn + 1], 0);
    pair.left.buffer = leftBuffer;
    pair.right.buffer = rightBuffer;
  });
}

// ---- Crosstalk-cancellation (transaural) speaker rendering ----
//
// Mirrors upmixer/crosstalk/renderer.py::render_crosstalk exactly: reuse the
// anechoic binaural ("flat") ear signals, apply a 2x2 crosstalk-cancellation
// FIR matrix, then a profile voicing chain — see
// docs/standards/transaural_speakers.md §1.

export type XtcConvolvers = {
  ll: ConvolverNode; // left speaker <- left ear
  lr: ConvolverNode; // left speaker <- right ear
  rl: ConvolverNode; // right speaker <- left ear
  rr: ConvolverNode; // right speaker <- right ear
};

export type CrosstalkGraphHandle = {
  /** Feed each positional channel's `AmbiMonoEncoder.out` into this — same
   * role as `BinauralGraphHandle.hoaBus`, since this graph decodes its own
   * internal "flat" (anechoic) binaural render before crosstalk-cancelling
   * it (upmixer/crosstalk/renderer.py always renders the ear signals with
   * the `flat` profile, never a room-tail profile — a real speaker/room
   * already supplies reverberant coloration on playback). */
  hoaBus: GainNode;
  /** Pre-voicing insertion point for LFE — same contract as
   * `BinauralGraphHandle.preVoicing` (Ledger D11): LFE folds into the
   * anechoic ear signals *before* the XTC matrix and transaural voicing
   * chain run, matching `render_binaural`'s own LFE-before-voicing order
   * (the XTC/voicing stages downstream both then see the LFE-inclusive
   * signal, same as the backend). */
  preVoicing: AudioNode;
  /** Post-voicing stereo output (2 discrete channels via a ChannelMerger). */
  output: AudioNode;
  /** The internal anechoic binaural sub-graph — exposed so its convolvers
   * can be filled by `loadDecodeFilterSet("flat")`, same as the primary
   * binaural graph's. */
  binaural: BinauralGraphHandle;
  xtcConvolvers: XtcConvolvers;
  voicing: VoicingChain;
  /** Every node this call created besides `hoaBus`/`preVoicing`/`output`/
   * `binaural`/`voicing` (already covered by their own teardown) — for
   * teardown. */
  nodes: AudioNode[];
};

export function buildCrosstalkGraph(ctx: BaseAudioContext, profile: TransauralProfile, constants: EngineConstants): CrosstalkGraphHandle {
  const nodes: AudioNode[] = [];

  const binaural = buildBinauralGraph(ctx, "flat", constants);

  const earSplitter = ctx.createChannelSplitter(2);
  binaural.output.connect(earSplitter);
  const earL = ctx.createGain();
  const earR = ctx.createGain();
  earSplitter.connect(earL, 0);
  earSplitter.connect(earR, 1);
  nodes.push(earSplitter, earL, earR);

  // 2x2 crosstalk-cancellation matrix: speaker = H @ ear (four convolvers,
  // one per H_xy tap — see docs/standards/transaural_speakers.md §4).
  const ll = ctx.createConvolver();
  const lr = ctx.createConvolver();
  const rl = ctx.createConvolver();
  const rr = ctx.createConvolver();
  for (const conv of [ll, lr, rl, rr]) conv.normalize = false;
  earL.connect(ll);
  earR.connect(lr);
  earL.connect(rl);
  earR.connect(rr);
  nodes.push(ll, lr, rl, rr);

  const speakerSumL = ctx.createGain();
  const speakerSumR = ctx.createGain();
  ll.connect(speakerSumL);
  lr.connect(speakerSumL);
  rl.connect(speakerSumR);
  rr.connect(speakerSumR);
  nodes.push(speakerSumL, speakerSumR);

  const xtcMerger = ctx.createChannelMerger(2);
  speakerSumL.connect(xtcMerger, 0, 0);
  speakerSumR.connect(xtcMerger, 0, 1);
  nodes.push(xtcMerger);

  const voicingSplitter = ctx.createChannelSplitter(2);
  xtcMerger.connect(voicingSplitter);
  const voicingLeftTap = ctx.createGain();
  const voicingRightTap = ctx.createGain();
  voicingSplitter.connect(voicingLeftTap, 0);
  voicingSplitter.connect(voicingRightTap, 1);
  nodes.push(voicingSplitter, voicingLeftTap, voicingRightTap);

  const voicing = buildVoicingChain(ctx, voicingLeftTap, voicingRightTap);
  applyVoicingParams(voicing, constants.transauralVoicingParams[profile]);
  const voicingMerger = ctx.createChannelMerger(2);
  voicing.left.connect(voicingMerger, 0, 0);
  voicing.right.connect(voicingMerger, 0, 1);
  nodes.push(voicingMerger);

  return {
    hoaBus: binaural.hoaBus,
    preVoicing: binaural.preVoicing,
    output: voicingMerger,
    binaural,
    xtcConvolvers: { ll, lr, rl, rr },
    voicing,
    nodes,
  };
}

/** Fetches and decodes a profile's single 4-channel XTC filter WAV (no
 * multi-file split needed — see `XTC_FILTER_CHANNELS`). `fileLoader`
 * decouples the browser `fetch`-based default (`useStemPreview.ts`'s
 * `fetchXtcFilterSet`) from the golden-diff harness's disk read, mirroring
 * `loadDecodeFilterChannels`'s `partLoader` pattern above. */
export async function loadXtcFilterChannels(
  ctx: BaseAudioContext,
  name: string,
  fileLoader: (ctx: BaseAudioContext, name: string) => Promise<AudioBuffer>,
): Promise<Float32Array[]> {
  const buffer = await fileLoader(ctx, name);
  const channels: Float32Array[] = [];
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) channels.push(buffer.getChannelData(ch));
  if (channels.length !== 4) {
    throw new Error(`Crosstalk filter set '${name}' has ${channels.length} channels, expected 4`);
  }
  return channels;
}

/** Dedupes concurrent/repeat fetches of the same profile's XTC filter set by
 * `name` — same cache-by-key pattern as `loadCachedDecodeFilterChannels`. */
export function loadCachedXtcFilterChannels(
  cache: Map<string, Promise<Float32Array[]>>,
  ctx: BaseAudioContext,
  name: string,
  fileLoader: (ctx: BaseAudioContext, name: string) => Promise<AudioBuffer>,
): Promise<Float32Array[]> {
  let pending = cache.get(name);
  if (!pending) {
    pending = loadXtcFilterChannels(ctx, name, fileLoader);
    cache.set(name, pending);
  }
  return pending;
}

/** Assigns a loaded XTC filter set's 4 channels onto a `buildCrosstalkGraph`
 * handle's `xtcConvolvers` (H_LL, H_LR, H_RL, H_RR in that order — matching
 * upmixer/crosstalk/filters.py's WAV channel layout). */
export function assignXtcFilterBuffers(
  ctx: BaseAudioContext,
  convolvers: XtcConvolvers,
  channels: Float32Array[],
): void {
  const length = channels[0].length;
  const toBuffer = (data: Float32Array) => {
    const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    buffer.copyToChannel(data, 0);
    return buffer;
  };
  convolvers.ll.buffer = toBuffer(channels[0]);
  convolvers.lr.buffer = toBuffer(channels[1]);
  convolvers.rl.buffer = toBuffer(channels[2]);
  convolvers.rr.buffer = toBuffer(channels[3]);
}
