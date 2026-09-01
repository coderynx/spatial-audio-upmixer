import { Channel, invoke } from "@tauri-apps/api/core";
import { getServerUrl } from "@/runtime";
import type { DspEngineCallbacks, DspEngineParams, DspMeterFrame } from "./wasmEngine/engineClient";

export type NativePreviewAssets = {
  decodeAsset?: string;
  xtcAsset?: string;
  masterEqAsset?: string;
  referenceFirUrl?: string;
};

export type NativeRenderer = "direct" | "apple_spatial";

type NativeEvent = {
  type: string;
  coreVersion?: string;
  totalFrames?: number;
  maxChannels?: number;
  progress?: number;
  position?: number;
  meters?: number[];
  spectrum?: number[];
  underruns?: number;
  stage?: "fast" | "exact";
  requestId?: number;
  lkfs?: number;
  dbtp?: number;
  monitorLkfs?: number;
  monitorDbtp?: number;
  message?: string;
};

export type NativeOpenOptions = {
  sources: { key: string; url: string; channels: number }[];
  params: DspEngineParams;
  assets: NativePreviewAssets;
  renderer: NativeRenderer;
  onMaxChannels(maxChannels: number): void;
  onLoadProgress(progress: number): void;
};

export class NativePreviewClient {
  readonly ready: Promise<string>;
  private sessionId = 0;
  private disposed = false;
  private pendingUpdate: { params: DspEngineParams; assets: NativePreviewAssets; renderer: NativeRenderer } | null = null;
  private updateScheduled = false;
  private queue = Promise.resolve<unknown>(undefined);
  private pendingMeasure: ((value: { lkfs: number; dbtp: number; monitorLkfs?: number; monitorDbtp?: number } | null) => void) | null = null;

  private constructor(
    ready: Promise<string>,
    private readonly callbacks: DspEngineCallbacks,
  ) {
    this.ready = ready;
  }

  static async create(options: NativeOpenOptions, callbacks: DspEngineCallbacks): Promise<NativePreviewClient> {
    let resolveReady!: (version: string) => void;
    let rejectReady!: (error: Error) => void;
    const ready = new Promise<string>((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });
    const client = new NativePreviewClient(ready, callbacks);
    const channel = new Channel<NativeEvent>();
    channel.onmessage = (event) => {
      switch (event.type) {
        case "loadProgress": options.onLoadProgress(event.progress ?? 0); break;
        case "ready":
          options.onMaxChannels(event.maxChannels ?? 2);
          callbacks.onLoaded?.(event.totalFrames ?? 0);
          callbacks.onReady?.(event.coreVersion ?? "native");
          resolveReady(event.coreVersion ?? "native");
          break;
        case "frame": callbacks.onFrame?.({
          position: event.position ?? 0,
          meters: event.meters ?? [],
          spectrum: event.spectrum ?? [],
          underruns: event.underruns ?? 0,
        } satisfies DspMeterFrame); break;
        case "measuring": callbacks.onMeasureProgress?.(event.progress ?? 0); break;
        case "measured": client.onMeasured(event); break;
        case "ended": callbacks.onEnded?.(); break;
        case "error": {
          const error = new Error(event.message || "Native preview failed");
          rejectReady(error);
          callbacks.onError?.(error.message);
          break;
        }
        default: break;
      }
    };
    client.sessionId = await invoke<number>("native_preview_open", {
      request: {
        serverBase: getServerUrl(),
        sources: options.sources,
        params: options.params,
        assets: options.assets,
        renderer: options.renderer,
      },
      onEvent: channel,
    });
    return client;
  }

  updateParams(params: DspEngineParams, assets: NativePreviewAssets, renderer: NativeRenderer) {
    this.pendingUpdate = { params, assets, renderer };
    if (this.updateScheduled || this.disposed) return;
    this.updateScheduled = true;
    requestAnimationFrame(() => {
      this.updateScheduled = false;
      this.flush();
    });
  }

  start(frame: number, looping: boolean) {
    this.flush();
    this.enqueue("native_preview_transport", { request: {
      sessionId: this.sessionId,
      playing: true,
      looping,
      frame: Math.max(0, Math.round(frame)),
    } });
  }

  setTransport(state: { playing?: boolean; loop?: boolean }) {
    this.flush();
    this.enqueue("native_preview_transport", { request: {
      sessionId: this.sessionId,
      playing: state.playing,
      looping: state.loop,
    } });
  }

  seek(frame: number): Promise<void> {
    this.flush();
    return this.enqueue("native_preview_seek", { request: {
      sessionId: this.sessionId,
      frame: Math.max(0, Math.round(frame)),
    } }).then(() => undefined);
  }

  measure(weights: number[], requestId: number) {
    this.pendingMeasure?.(null);
    return new Promise<{ lkfs: number; dbtp: number; monitorLkfs?: number; monitorDbtp?: number } | null>((resolve) => {
      this.pendingMeasure = resolve;
      this.flush();
      this.enqueue("native_preview_measure", { request: {
        sessionId: this.sessionId,
        weights,
        requestId,
      } });
    });
  }

  setMonitor(volume: number, muted: boolean) {
    this.enqueue("native_preview_monitor", { request: { sessionId: this.sessionId, volume, muted } });
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.pendingUpdate = null;
    this.pendingMeasure?.(null);
    this.pendingMeasure = null;
    void invoke("native_preview_close", { sessionId: this.sessionId });
  }

  private flush() {
    const update = this.pendingUpdate;
    this.pendingUpdate = null;
    if (!update || this.disposed) return;
    this.enqueue("native_preview_update", { request: { sessionId: this.sessionId, ...update } });
  }

  private enqueue(command: string, args: Record<string, unknown>): Promise<unknown> {
    this.queue = this.queue.catch(() => undefined).then(() => this.disposed ? undefined : invoke(command, args));
    this.queue.catch((error) => this.callbacks.onError?.(error instanceof Error ? error.message : String(error)));
    return this.queue;
  }

  private onMeasured(event: NativeEvent) {
    const result = {
      lkfs: event.lkfs ?? -70,
      dbtp: event.dbtp ?? -70,
      monitorLkfs: event.monitorLkfs,
      monitorDbtp: event.monitorDbtp,
    };
    if (event.stage === "exact") {
      this.callbacks.onMeasured?.(result, event.requestId ?? 0);
    } else {
      this.pendingMeasure?.(result);
      this.pendingMeasure = null;
    }
  }
}
