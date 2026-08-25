// Main-thread client for the shared DSP core running in the preview worklet.
//
// This file is glue only — it compiles the wasm, hands stems over, and
// forwards parameter changes. All DSP lives in packages/dsp; see
// docs/contracts/preview_export_parity.md.

export type DspEngineParams = Record<string, unknown>;

type FirUpdate = {
  masterEq?: Float64Array;
  reference?: Float64Array;
  stemEq?: Array<{ index: number; taps: Float64Array }>;
};

/** `[rms, peak]` pairs: stems, then bed channels, then the output pair. */
export type DspMeterFrame = {
  position: number;
  meters: number[];
  /** `[level, centroid]` pairs, one per stem — see `stem_spectrum` in the core. */
  spectrum: number[];
  underruns: number;
};

export type DspEngineCallbacks = {
  onReady?: (coreVersion: string) => void;
  onLoaded?: (totalFrames: number) => void;
  /** ~30 Hz playhead and level report. */
  onFrame?: (frame: DspMeterFrame) => void;
  onEnded?: () => void;
  /** Fraction of the current measurement stage measured, while in flight. */
  onMeasureProgress?: (progress: number) => void;
  /**
   * The exact whole-programme measurement landed, refining the fast excerpt
   * result `measure()` already resolved with. See `DspEngineClient.measure`.
   */
  onMeasured?: (result: { lkfs: number; dbtp: number }) => void;
  onError?: (message: string) => void;
};

const WASM_URL = "/wasm/upmixer_dsp.wasm";
const WORKLET_URL = "/dsp.worklet.js";
const PROCESSOR_NAME = "upmixer-dsp-processor";

let modulePromise: Promise<WebAssembly.Module> | null = null;

// An AudioWorkletGlobalScope has no TextEncoder, so the parameter block is
// encoded here and the bytes are transferred.
const PARAM_ENCODER = new TextEncoder();

function encodeParams(params: DspEngineParams): Uint8Array {
  return PARAM_ENCODER.encode(JSON.stringify(params));
}

/** Compile once per page; the Module is structured-cloneable and reusable. */
export function loadDspModule(): Promise<WebAssembly.Module> {
  if (!modulePromise) {
    modulePromise = fetch(WASM_URL).then((response) => {
      if (!response.ok) {
        throw new Error(`Failed to fetch ${WASM_URL}: ${response.status}`);
      }
      return WebAssembly.compileStreaming(response);
    });
  }
  return modulePromise;
}

export class DspEngineClient {
  readonly node: AudioWorkletNode;
  private readonly callbacks: DspEngineCallbacks;
  /** Resolves once the processor has instantiated the wasm. */
  readonly ready: Promise<string>;

  private pendingMeasure: ((result: { lkfs: number; dbtp: number } | null) => void) | null = null;
  private nextSeekId = 0;
  private readonly pendingSeeks = new Map<number, () => void>();
  /** Latest params awaiting the next coalesced `updateParams` post. */
  private pendingUpdate: DspEngineParams | null = null;
  private updateScheduled = false;
  private disposed = false;
  private masterEqFir: ArrayLike<number> | null = null;
  private referenceFir: ArrayLike<number> | null = null;
  private stemEqFir: Array<ArrayLike<number> | null> = [];

  private constructor(
    node: AudioWorkletNode,
    callbacks: DspEngineCallbacks,
    ready: Promise<string>,
  ) {
    this.node = node;
    this.callbacks = callbacks;
    this.ready = ready;
  }

  static async create(
    ctx: BaseAudioContext,
    channelCount: number,
    callbacks: DspEngineCallbacks = {},
  ): Promise<DspEngineClient> {
    const [module] = await Promise.all([
      loadDspModule(),
      ctx.audioWorklet.addModule(WORKLET_URL),
    ]);

    const node = new AudioWorkletNode(ctx, PROCESSOR_NAME, {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [channelCount],
      channelCount,
      channelCountMode: "explicit",
      channelInterpretation: "discrete",
      processorOptions: { module, channelCount },
    });

    let client: DspEngineClient | null = null;
    let resolveReady: (version: string) => void = () => {};
    let rejectReady: (reason: Error) => void = () => {};
    const ready = new Promise<string>((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });

    node.port.onmessage = (event: MessageEvent) => {
      const message = event.data as { type: string; [key: string]: unknown };
      switch (message.type) {
        case "ready":
          resolveReady(String(message.version));
          callbacks.onReady?.(String(message.version));
          break;
        case "loaded":
          callbacks.onLoaded?.(Number(message.totalFrames));
          break;
        case "frame":
          callbacks.onFrame?.({
            position: Number(message.position),
            meters: (message.meters as number[]) ?? [],
            spectrum: (message.spectrum as number[]) ?? [],
            underruns: Number(message.underruns),
          });
          break;
        case "measuring":
          callbacks.onMeasureProgress?.(Number(message.progress));
          break;
        case "measured": {
          const result = { lkfs: Number(message.lkfs), dbtp: Number(message.dbtp) };
          if (message.stage === "exact") {
            callbacks.onMeasured?.(result);
          } else {
            client?.resolveMeasure(result);
          }
          break;
        }
        case "seeked":
          client?.resolveSeek(Number(message.id));
          break;
        case "ended":
          callbacks.onEnded?.();
          break;
        case "error": {
          const text = String(message.message);
          rejectReady(new Error(text));
          callbacks.onError?.(text);
          break;
        }
        default:
          break;
      }
    };

    client = new DspEngineClient(node, callbacks, ready);
    return client;
  }

  /** Create the engine. Stems must be added afterwards. */
  setParams(params: DspEngineParams): void {
    // Supersedes any coalesced update: this block builds a fresh engine.
    this.pendingUpdate = null;
    this.masterEqFir = null;
    this.referenceFir = null;
    this.stemEqFir = [];
    const { bytes, firs, transfer } = this.prepareParams(params, true);
    this.node.port.postMessage({ type: "params", bytes, firs }, transfer);
  }

  /**
   * Replace the parameter block in place, keeping the loaded stems and the
   * playhead — the one path for "the mix changed", whether that means mute,
   * solo, rebalance, routing, mastering, or the output mode.
   *
   * Coalesced to one post per animation frame: a fader drag calls this once
   * per pointer-move, and each call already carries the whole parameter
   * block, so only the latest one before a frame actually needs to reach the
   * worklet — the frames in between would just be immediately superseded.
   */
  updateParams(params: DspEngineParams): void {
    this.pendingUpdate = params;
    if (this.updateScheduled || this.disposed) return;
    this.updateScheduled = true;
    requestAnimationFrame(() => {
      this.updateScheduled = false;
      this.flushParams();
    });
  }

  private flushParams(): void {
    const latest = this.pendingUpdate;
    this.pendingUpdate = null;
    if (!latest || this.disposed) return;
    const { bytes, firs, transfer } = this.prepareParams(latest);
    this.node.port.postMessage({ type: "update", bytes, firs }, transfer);
  }

  private prepareParams(
    params: DspEngineParams,
    force = false,
  ): { bytes: Uint8Array; firs: FirUpdate; transfer: Transferable[] } {
    const firs: FirUpdate = {};
    const transfer: Transferable[] = [];
    const master = params.master as Record<string, unknown> | undefined;
    const take = (
      source: unknown,
      previous: ArrayLike<number> | null,
      setPrevious: (value: ArrayLike<number> | null) => void,
    ): Float64Array | undefined => {
      const value = source as ArrayLike<number> | undefined;
      if (!force && value === previous) return undefined;
      setPrevious(value ?? null);
      if (!value && !previous) return undefined;
      const taps = value ? new Float64Array(value) : new Float64Array();
      transfer.push(taps.buffer);
      return taps;
    };

    if (master) {
      firs.masterEq = take(master.eq_fir, this.masterEqFir, (value) => (this.masterEqFir = value));
      firs.reference = take(master.reference_fir, this.referenceFir, (value) => (this.referenceFir = value));
      delete master.eq_fir;
      delete master.reference_fir;
    }
    const stems = params.stems as Array<Record<string, unknown>> | undefined;
    if (stems) {
      const updates: Array<{ index: number; taps: Float64Array }> = [];
      for (const [index, stem] of stems.entries()) {
        const taps = take(stem.eq_fir, this.stemEqFir[index] ?? null, (value) => {
          this.stemEqFir[index] = value ?? null;
        });
        if (taps) updates.push({ index, taps });
        delete stem.eq_fir;
      }
      this.stemEqFir.length = stems.length;
      if (updates.length) firs.stemEq = updates;
    }
    params.transferred_firs = true;
    const bytes = encodeParams(params);
    transfer.unshift(bytes.buffer);
    return { bytes, firs, transfer };
  }

  /**
   * Post anything that is not a coalesced parameter update, sending the
   * pending block first. The worklet acts on whatever parameters are in the
   * engine the moment a message lands — a measurement forks it right there,
   * and the transport starts rendering with it — so a message that overtook
   * the frame's update would act on the previous mix.
   */
  private post(message: Record<string, unknown>, transfer: Transferable[] = []): void {
    this.flushParams();
    this.node.port.postMessage(message, transfer);
  }

  setTransport(state: { playing?: boolean; loop?: boolean }): void {
    this.post({ type: "transport", ...state });
  }

  /**
   * Measure the collapsed programme's real BS.1770 integrated loudness and
   * true peak; the transport is left where it was.
   *
   * Runs in two stages: this resolves once a fast pass over a handful of
   * excerpts lands, typically within a few seconds. An exact whole-programme
   * pass then keeps running in the background and reports through
   * `onMeasured` once it lands, minutes later on a long track. Resolves with
   * `null` if another measurement supersedes it before the fast pass lands.
   */
  measure(weights: number[] = []): Promise<{ lkfs: number; dbtp: number } | null> {
    this.pendingMeasure?.(null);
    return new Promise((resolve) => {
      this.pendingMeasure = resolve;
      this.post({ type: "measure", weights });
    });
  }

  seek(frame: number): Promise<void> {
    const id = ++this.nextSeekId;
    return new Promise((resolve) => {
      this.pendingSeeks.set(id, resolve);
      this.post({ type: "seek", id, frame: Math.max(0, Math.round(frame)) });
    });
  }

  start(frame: number, loop: boolean): void {
    this.post({ type: "start", frame: Math.max(0, Math.round(frame)), loop });
  }

  /**
   * Hand a decoded stem to the engine. The buffers are transferred, so the
   * caller must drop its reference — keeping both copies would double the
   * memory a multi-stem project needs.
   */
  addStem(left: Float32Array, right: Float32Array): void {
    this.post({ type: "stem", left, right }, [
      left.buffer as ArrayBuffer,
      right.buffer as ArrayBuffer,
    ]);
  }

  /**
   * Replace the binaural decode bank, independent of `updateParams` — the
   * bank is large (order-3 ambisonics: 16 channels x 2 ears x several
   * thousand taps) and changes only when the spatial profile does, so it
   * travels its own transferred channel instead of riding along in every
   * mix edit's JSON block. The engine keeps whatever was last set here
   * across every later `updateParams` call.
   *
   * `taps` is transferred like `addStem`'s buffers: if the caller keeps its
   * own cache of the profile's taps (to skip re-fetching it later), it must
   * pass a copy here, not the cached array itself.
   */
  setDecodeTaps(taps: Float64Array): void {
    this.post({ type: "decodeTaps", taps }, [taps.buffer as ArrayBuffer]);
  }

  /** Replace the crosstalk-cancellation matrix. See `setDecodeTaps`. */
  setXtcTaps(taps: Float64Array): void {
    this.post({ type: "xtcTaps", taps }, [taps.buffer as ArrayBuffer]);
  }

  rewind(): void {
    this.post({ type: "rewind" });
  }

  /** Called from the port handler; not part of the public surface. */
  resolveMeasure(result: { lkfs: number; dbtp: number } | null): void {
    this.pendingMeasure?.(result);
    this.pendingMeasure = null;
  }

  private resolveSeek(id: number): void {
    this.pendingSeeks.get(id)?.();
    this.pendingSeeks.delete(id);
  }

  dispose(): void {
    this.disposed = true;
    this.pendingUpdate = null;
    this.pendingMeasure?.(null);
    this.pendingMeasure = null;
    for (const resolve of this.pendingSeeks.values()) resolve();
    this.pendingSeeks.clear();
    this.node.port.postMessage({ type: "dispose" });
    this.node.port.onmessage = null;
    this.node.disconnect();
  }
}
