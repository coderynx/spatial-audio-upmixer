import { speakerCoordinates } from "@/lib/spatial";

export type EngineRef<T> = { current: T };

export function engineRef<T>(value: T): EngineRef<T> {
  return { current: value };
}

export type OutputMode = "binaural" | "transaural" | "stereo" | "native";

export const POSITIONAL_CHANNELS = Object.keys(speakerCoordinates);

export type MixPreview = {
  stem_routing?: Record<string, Record<string, number>>;
  stem_rebalance?: Record<string, number>;
  stem_eq?: Record<string, string>;
  stem_ambient_rear?: Record<string, number>;
  stem_ambient_height?: Record<string, number>;
  stem_ambient_height_crossover_hz?: Record<string, number>;
  spatial_downmix_lock?: boolean;
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_source_anchor_strength?: number;
};

/** The slow half of the master readout: what the delivered programme measures
 * and what it is being normalized to. Everything here is the *delivered*
 * value — the measurement plus whatever correction gain is applied — so it
 * reads against the target directly. */
export type LoudnessSummary = {
  /** BS.1770 integrated loudness, LKFS; -70 until the first pass lands. */
  integratedLkfs: number;
  /** Maximum true peak, dBTP. */
  truePeakDbtp: number;
  targetLkfs: number;
  ceilingDbtp: number;
  /** Monitor-only gain the loudness-matched A/B is applying, dB. Non-zero
   * only while the master chain is bypassed. */
  bypassMatchDb: number;
};

export const SILENT_LOUDNESS: LoudnessSummary = {
  integratedLkfs: -70,
  truePeakDbtp: -70,
  targetLkfs: -18,
  ceilingDbtp: -1,
  bypassMatchDb: 0,
};

export type EngineCallbacks = {
  onReady(ready: boolean): void;
  onLoadProgress(progress: number): void;
  onError(message: string | null): void;
  onPlaying(playing: boolean): void;
  onCurrentTime(time: number): void;
  onDuration(duration: number): void;
  onMeasuring(measuring: boolean): void;
  /** Measured loudness, the target it is normalized to, and the A/B match
   * gain — pushed whenever any of them moves, not per frame. */
  onLoudness(summary: LoudnessSummary): void;
  /** Fraction of the current measurement stage measured, for a progress bar. */
  onMeasureProgress(progress: number): void;
  onMaxChannels(maxChannels: number): void;
  onVolume(volume: number): void;
  onMuted(muted: boolean): void;
  onLoop(loop: boolean): void;
};
