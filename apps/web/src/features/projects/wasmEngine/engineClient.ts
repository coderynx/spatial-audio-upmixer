// Main-thread client for the shared DSP core running in the preview worklet.
//
// This file is glue only — it compiles the wasm, hands stems over, and
// forwards parameter changes. All DSP lives in packages/dsp; see
// docs/contracts/preview_export_parity.md.

export type DspEngineParams = Record<string, unknown>;

/** `[rms, peak]` pairs: stems, then bed channels, then the output pair. */
export type DspMeterFrame = {
  position: number;
  meters: number[];
};

export type DspEngineCallbacks = {
  onReady?: (coreVersion: string) => void;
  onLoaded?: (totalFrames: number) => void;
  /** ~30 Hz playhead and level report. */
  onFrame?: (frame: DspMeterFrame) => void;
  onEnded?: () => void;
  /** Fraction of the programme measured, while a measurement is in flight. */
  onMeasureProgress?: (progress: number) => void;
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
          });
          break;
        case "measuring":
          callbacks.onMeasureProgress?.(Number(message.progress));
          break;
        case "measured":
          client?.resolveMeasure({
            lkfs: Number(message.lkfs),
            dbtp: Number(message.dbtp),
          });
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
    const bytes = encodeParams(params);
    this.node.port.postMessage({ type: "params", bytes }, [bytes.buffer]);
  }

  /**
   * Replace the parameter block in place, keeping the loaded stems and the
   * playhead — the one path for "the mix changed", whether that means mute,
   * solo, rebalance, routing, mastering, or the output mode.
   */
  updateParams(params: DspEngineParams): void {
    const bytes = encodeParams(params);
    this.node.port.postMessage({ type: "update", bytes }, [bytes.buffer]);
  }

  setTransport(state: { playing?: boolean; loop?: boolean }): void {
    this.node.port.postMessage({ type: "transport", ...state });
  }

  /**
   * Measure the whole collapsed programme. Resolves with real BS.1770
   * integrated loudness and true peak; the transport is left where it was.
   *
   * The pass is advanced in slices from the render callback, so this takes as
   * long as the programme does to walk — minutes for a long track, faster
   * while paused. Resolves with `null` if another measurement supersedes it.
   */
  measure(weights: number[] = []): Promise<{ lkfs: number; dbtp: number } | null> {
    this.pendingMeasure?.(null);
    return new Promise((resolve) => {
      this.pendingMeasure = resolve;
      this.node.port.postMessage({ type: "measure", weights });
    });
  }

  seek(frame: number): void {
    this.node.port.postMessage({ type: "seek", frame: Math.max(0, Math.round(frame)) });
  }

  /**
   * Hand a decoded stem to the engine. The buffers are transferred, so the
   * caller must drop its reference — keeping both copies would double the
   * memory a multi-stem project needs.
   */
  addStem(left: Float32Array, right: Float32Array): void {
    this.node.port.postMessage({ type: "stem", left, right }, [
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
    this.node.port.postMessage({ type: "decodeTaps", taps }, [taps.buffer as ArrayBuffer]);
  }

  /** Replace the crosstalk-cancellation matrix. See `setDecodeTaps`. */
  setXtcTaps(taps: Float64Array): void {
    this.node.port.postMessage({ type: "xtcTaps", taps }, [taps.buffer as ArrayBuffer]);
  }

  rewind(): void {
    this.node.port.postMessage({ type: "rewind" });
  }

  /** Called from the port handler; not part of the public surface. */
  resolveMeasure(result: { lkfs: number; dbtp: number } | null): void {
    this.pendingMeasure?.(result);
    this.pendingMeasure = null;
  }

  dispose(): void {
    this.pendingMeasure?.(null);
    this.pendingMeasure = null;
    this.node.port.postMessage({ type: "dispose" });
    this.node.port.onmessage = null;
    this.node.disconnect();
  }
}
