// Main-thread client for the shared DSP core running in the preview worklet.
//
// This file is glue only — it compiles the wasm, hands stems over, and
// forwards parameter changes. All DSP lives in packages/dsp; see
// docs/contracts/preview_export_parity.md.

export type DspEngineParams = Record<string, unknown>;

export type DspEngineCallbacks = {
  onReady?: (coreVersion: string) => void;
  onLoaded?: (totalFrames: number) => void;
  onEnded?: () => void;
  onError?: (message: string) => void;
};

const WASM_URL = "/wasm/upmixer_dsp.wasm";
const WORKLET_URL = "/dsp.worklet.js";
const PROCESSOR_NAME = "upmixer-dsp-processor";

let modulePromise: Promise<WebAssembly.Module> | null = null;

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

    return new DspEngineClient(node, callbacks, ready);
  }

  setParams(params: DspEngineParams): void {
    this.node.port.postMessage({ type: "params", json: JSON.stringify(params) });
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

  rewind(): void {
    this.node.port.postMessage({ type: "rewind" });
  }

  dispose(): void {
    this.node.port.postMessage({ type: "dispose" });
    this.node.port.onmessage = null;
    this.node.disconnect();
  }
}
