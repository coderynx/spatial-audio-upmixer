import { loadDspModule } from "./engineClient";

/** Where one stem sits, before any layout is applied. Mirrors the core's
 * `StemPlacement`; `lfe` is carried by the route's own LFE send instead. */
export type StemPlacement = {
  azimuth_deg: number;
  elevation_deg: number;
  width_deg: number;
  object_size: number;
  diversity?: number;
  center_level_db?: number;
};

/** Where a stem sits when neither the manifest nor the preset names it: front
 * and centred, with the same falloff a dragged scene position gets. */
export const NEUTRAL_PLACEMENT: StemPlacement = {
  azimuth_deg: 0, elevation_deg: 0, width_deg: 0, object_size: 0,
};

/** What a preset sends a stem outside its panned image. */
export type PresetSends = {
  lfe: number;
  rear: number;
  height: number;
  heightCrossoverHz: number;
};

export type PresetTreatment = {
  placement: StemPlacement;
  sends: PresetSends;
};

type PannerExports = {
  memory: WebAssembly.Memory;
  dsp_alloc(bytes: number): number;
  dsp_free(ptr: number, bytes: number): void;
  dsp_panner_channel_count(): number;
  dsp_panner_channel_len(index: number): number;
  dsp_panner_channel_ptr(index: number): number;
  dsp_preset_count(): number;
  dsp_preset_name_len(preset: number): number;
  dsp_preset_name_ptr(preset: number): number;
  dsp_preset_stem_count(preset: number): number;
  dsp_preset_stem_name_len(preset: number, stem: number): number;
  dsp_preset_stem_name_ptr(preset: number, stem: number): number;
  dsp_preset_treatment(preset: number, stem: number, out: number): number;
  dsp_placement_route(
    azimuth: number, elevation: number, width: number, spread: number,
    diversity: number, centerLevelDb: number, lfe: number,
    channels: number, nChannels: number, out: number,
  ): number;
  dsp_object_routes(
    azimuth: number, elevation: number, width: number, spread: number,
    channels: number, nChannels: number, left: number, right: number,
  ): number;
  dsp_project_placement(
    azimuth: number, elevation: number, width: number, spread: number, lfe: number,
    channels: number, nChannels: number, out: number,
  ): number;
  dsp_fold_route_to_stereo(
    route: number, channels: number, nChannels: number, out: number,
  ): number;
  dsp_panner_max_elevation(channels: number, nChannels: number): number;
};

const DECODER = new TextDecoder();

/** The panner runs on the main thread, not in the worklet: it is control-rate
 * work (one call per slider commit) and the audio thread's per-quantum budget
 * has no room to spare for it. The compiled module is shared with the
 * worklet's instance, so this costs an instantiation, not a second fetch. */
let pannerPromise: Promise<Panner> | null = null;

export class Panner {
  private readonly channelIndex = new Map<string, number>();
  private readonly presetNames: string[] = [];

  constructor(private readonly exports: PannerExports) {
    for (let index = 0; index < exports.dsp_panner_channel_count(); index += 1) {
      this.channelIndex.set(
        this.readString(exports.dsp_panner_channel_ptr(index), exports.dsp_panner_channel_len(index)),
        index,
      );
    }
    for (let preset = 0; preset < exports.dsp_preset_count(); preset += 1) {
      this.presetNames.push(
        this.readString(exports.dsp_preset_name_ptr(preset), exports.dsp_preset_name_len(preset)),
      );
    }
  }

  private readString(ptr: number, len: number): string {
    if (!ptr || !len) return "";
    return DECODER.decode(new Uint8Array(this.exports.memory.buffer, ptr, len));
  }

  /** Run `body` with scratch space for the channel indices and a result
   * buffer, freeing both however it returns. */
  private withBuffers<T>(
    channels: string[],
    outLength: number,
    body: (channelPtr: number, outPtr: number) => T,
  ): T {
    const indices = channels.map((channel) => this.channelIndex.get(channel) ?? -1);
    if (indices.some((index) => index < 0)) {
      throw new Error(`Unknown channel in [${channels.join(", ")}]`);
    }
    const channelBytes = indices.length * 4;
    const outBytes = outLength * 8;
    const channelPtr = this.exports.dsp_alloc(channelBytes);
    const outPtr = this.exports.dsp_alloc(outBytes);
    try {
      new Uint32Array(this.exports.memory.buffer, channelPtr, indices.length).set(indices);
      return body(channelPtr, outPtr);
    } finally {
      this.exports.dsp_free(channelPtr, channelBytes);
      this.exports.dsp_free(outPtr, outBytes);
    }
  }

  private read(outPtr: number, length: number): number[] {
    return Array.from(new Float64Array(this.exports.memory.buffer, outPtr, length));
  }

  get presets(): string[] {
    return [...this.presetNames];
  }

  /** Every complete treatment a preset names. */
  presetTreatments(preset: string): Record<string, PresetTreatment> {
    const index = this.presetNames.indexOf(preset);
    if (index < 0) return {};
    const out: Record<string, PresetTreatment> = {};
    const bytes = 10 * 8;
    const ptr = this.exports.dsp_alloc(bytes);
    try {
      for (let stem = 0; stem < this.exports.dsp_preset_stem_count(index); stem += 1) {
        const name = this.readString(
          this.exports.dsp_preset_stem_name_ptr(index, stem),
          this.exports.dsp_preset_stem_name_len(index, stem),
        );
        if (this.exports.dsp_preset_treatment(index, stem, ptr) !== 0) continue;
        const [
          azimuth_deg, elevation_deg, width_deg, object_size, lfe, diversity,
          center_level_db, rear, height, heightCrossoverHz,
        ] = this.read(ptr, 10);
        out[name] = {
          placement: { azimuth_deg, elevation_deg, width_deg, object_size, diversity, center_level_db },
          sends: { lfe, rear, height, heightCrossoverHz },
        };
      }
    } finally {
      this.exports.dsp_free(ptr, bytes);
    }
    return out;
  }

  /** Pan a placement into `channels`, keeping `lfe` as the LFE send. */
  placementRoute(
    placement: StemPlacement,
    channels: string[],
    lfe = 0,
  ): Record<string, number> {
    const gains = this.withBuffers(channels, channels.length, (channelPtr, outPtr) => {
      const status = this.exports.dsp_placement_route(
        placement.azimuth_deg, placement.elevation_deg, placement.width_deg,
        placement.object_size, placement.diversity ?? 0, placement.center_level_db ?? 0,
        lfe, channelPtr, channels.length, outPtr,
      );
      if (status !== 0) throw new Error("placement_route rejected the channel set");
      return this.read(outPtr, channels.length);
    });
    const route: Record<string, number> = {};
    channels.forEach((channel, index) => {
      if (gains[index] > 0) route[channel] = gains[index];
    });
    return route;
  }

  /** MDAP routes for the linked left/right direct-object feeds. */
  objectRoutes(placement: StemPlacement, channels: string[]): [Record<string, number>, Record<string, number>] {
    const [left, right] = this.withBuffers(channels, channels.length * 2, (channelPtr, outPtr) => {
      const rightPtr = outPtr + channels.length * 8;
      const status = this.exports.dsp_object_routes(
        placement.azimuth_deg, placement.elevation_deg, placement.width_deg,
        placement.object_size, channelPtr, channels.length, outPtr, rightPtr,
      );
      if (status !== 0) throw new Error("object_routes rejected the channel set");
      return [this.read(outPtr, channels.length), this.read(rightPtr, channels.length)];
    });
    return [
      Object.fromEntries(channels.map((channel, index) => [channel, left[index]])),
      Object.fromEntries(channels.map((channel, index) => [channel, right[index]])),
    ];
  }

  /** The highest elevation `channels` can reproduce; placements above it are
   * clamped by the panner, so this is the height slider's range. */
  maxElevationDeg(channels: string[]): number {
    return this.withBuffers(channels, 1, (channelPtr) =>
      this.exports.dsp_panner_max_elevation(channelPtr, channels.length));
  }

  /** Restate a placement as what `channels` can reproduce. */
  project(placement: StemPlacement, channels: string[]): StemPlacement {
    const fields = this.withBuffers(channels, 5, (channelPtr, outPtr) => {
      const status = this.exports.dsp_project_placement(
        placement.azimuth_deg, placement.elevation_deg, placement.width_deg,
        placement.object_size, 0, channelPtr, channels.length, outPtr,
      );
      if (status !== 0) throw new Error("project_placement rejected the channel set");
      return this.read(outPtr, 5);
    });
    const [azimuth_deg, elevation_deg, width_deg, object_size] = fields;
    return { azimuth_deg, elevation_deg, width_deg, object_size };
  }
}

export function loadPanner(): Promise<Panner> {
  if (!pannerPromise) {
    pannerPromise = loadDspModule()
      .then((module) => WebAssembly.instantiate(module, {}))
      .then((instance) => new Panner(instance.exports as unknown as PannerExports));
  }
  return pannerPromise;
}
