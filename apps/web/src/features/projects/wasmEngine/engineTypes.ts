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
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_source_anchor_strength?: number;
};

export type EngineCallbacks = {
  onReady(ready: boolean): void;
  onLoadProgress(progress: number): void;
  onError(message: string | null): void;
  onPlaying(playing: boolean): void;
  onCurrentTime(time: number): void;
  onDuration(duration: number): void;
  onMeasuring(measuring: boolean): void;
  /** Fraction of the current measurement stage measured, for a progress bar. */
  onMeasureProgress(progress: number): void;
  onMaxChannels(maxChannels: number): void;
  onVolume(volume: number): void;
  onMuted(muted: boolean): void;
  onLoop(loop: boolean): void;
};
