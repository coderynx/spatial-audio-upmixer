// Maps the preview's existing view of a project onto the shared DSP core's
// parameter block.
//
// This file holds no DSP and no acoustic constants of its own: every value
// either comes from the served `EngineConstants` or from the project's own
// mix. See docs/contracts/preview_export_parity.md §2.

import type {
  BassProfile,
  CompProfile,
  EngineConstants,
  SpatialProfile,
  TransauralProfile,
  VoicingParams,
} from "../masteringProfiles";
import { resolveLfTargets } from "../masteringProfiles";
import type { DynamicEqBand } from "@/lib/manifest";

export type OutputMode = "binaural" | "transaural" | "stereo" | "native";

/** Which shaped signal a speaker draws on, mirroring `CHANNEL_SIGNAL`. */
const CHANNEL_SHAPE: Record<string, string> = {
  FL: "left",
  FR: "right",
  C: "mono",
  SL: "surround_left",
  SR: "surround_right",
  BL: "surround_left",
  BR: "surround_right",
  TFL: "height_left",
  TFR: "height_right",
  TBL: "height_left",
  TBR: "height_right",
};

function groupGain(channel: string, c: EngineConstants): number {
  const g = c.channelGains;
  if (channel === "C") return g.center;
  if (channel === "BL" || channel === "BR") return g.back;
  if (channel === "SL" || channel === "SR") return g.surround;
  if (channel === "TFL" || channel === "TFR" || channel === "TBL" || channel === "TBR") {
    return g.height;
  }
  return 1;
}

function voicingToWire(v: VoicingParams) {
  return {
    crossfeed_amount: v.crossfeedAmount,
    crossfeed_cutoff_hz: v.crossfeedCutoffHz,
    bass_shelf_hz: v.bassShelfHz,
    bass_shelf_gain_db: v.bassShelfGainDb,
    air_shelf_hz: v.airShelfHz,
    air_shelf_gain_db: v.airShelfGainDb,
    presence_hz: v.presenceHz,
    presence_gain_db: v.presenceGainDb,
    presence_q: v.presenceQ,
    stereo_widen: v.stereoWiden,
  };
}

export type StemMix = {
  id: string;
  /** Speaker name to weight, including "LFE". */
  routing: Record<string, number>;
  rebalanceDb?: number;
  enabled?: boolean;
  /** Minimum-phase FIR taps for this stem's EQ profile, if any. */
  eqFir?: Float64Array | number[];
  /** Whole-stem route-energy normalization, as `StemRouter.route` computes. */
  routeScale?: number;
  /** How much of the stem's ambient half reaches the surrounds, and the
   * heights. Both 0..1; zero is a stem routed the way it always was. */
  ambientRear?: number;
  ambientHeight?: number;
  ambientHeightCrossoverHz?: number;
  objectMode?: "linked-stereo" | "mono";
  objectPlacement?: { azimuth_deg: number; elevation_deg: number; width_deg: number; spread_deg: number };
};

export type MasterMix = {
  /** Already resolved — profile preset merged with the project's per-field
   * overrides. Resolving upstream is what keeps a moved pot from being
   * dropped between the UI and the worklet (ledger D30). */
  comp?: CompProfile | null;
  bass?: BassProfile | null;
  /** Subsonic corner for the chain head, or null with the stage off. */
  highpassHz?: number | null;
  /** Soft-clip depth and knee, or null with the stage off. The curve's
   * asymptote is the limiter's ceiling, so it is not carried here. */
  clip?: { clip_db: number; knee: number } | null;
  eqFir?: Float64Array | number[];
  eqStrength?: number;
  /** Dynamic-EQ bells; empty is the stage absent, not a stage doing nothing. */
  dynamicEq?: DynamicEqBand[];
  referenceFir?: Float64Array | number[];
  referenceGain?: number;
  /** Loudness/true-peak correction from the offline precompute pass. */
  outputGain?: number;
  limiterCeilingDbtp?: number;
};

export type BuildEngineParamsInput = {
  constants: EngineConstants;
  /** Full channel set of the project's layout, LFE included. */
  layoutChannels: string[];
  stems: StemMix[];
  master?: MasterMix;
  outputMode: OutputMode;
  spatialProfile: SpatialProfile;
  transauralProfile: TransauralProfile;
  /**
   * Send values the project's manifest `routing` block sets per track. The
   * served constant is only the default: a track that carries its own value
   * must preview with it, or the export diverges from what the preview did.
   */
  sendOverrides?: { heightDirectionalBandGain?: number };
  /** Per-speaker mute; a muted speaker contributes nothing to any render. */
  speakerEnabled?: Record<string, boolean>;
  /** Restore each routed stem's stereo fold at the routing boundary. */
  spatialDownmixLock?: boolean;
  /** Transport A/B: render the bed without any mastering stage. */
  bypassMastering?: boolean;
  /** BS.1770 weights for the live loudness meters, in delivered-channel
   * order — the same set the measurement pass is given. */
  meterWeights?: number[];
};

/** Build the JSON parameter block the worklet hands to the core. */
export function buildEngineParams(input: BuildEngineParamsInput): Record<string, unknown> {
  const { constants: c, layoutChannels, outputMode } = input;
  const speakers = layoutChannels.filter(
    (channel) => channel === "LFE" || CHANNEL_SHAPE[channel] !== undefined,
  );
  const index = new Map(speakers.map((name, i) => [name, i]));
  const lfeIndex = index.get("LFE");

  const master = input.master ?? {};
  const comp = master.comp ?? null;
  const bass = master.bass ?? null;

  const voicing =
    outputMode === "transaural"
      ? c.transauralVoicingParams[input.transauralProfile]
      : c.voicingParams[input.spatialProfile];

  return {
    speakers: speakers.map((name) => {
      const direction = c.speakerDirections[name] ?? { azimuth_rad: 0, elevation_rad: 0 };
      return {
        name,
        azimuth_rad: direction.azimuth_rad,
        elevation_rad: direction.elevation_rad,
        group_gain: groupGain(name, c),
        // Monitor-only: the core applies it to the finished bed, so it never
        // reaches the shared bass bus or the linked compressor's detector.
        muted: input.speakerEnabled?.[name] === false,
      };
    }),
    lfe_index: lfeIndex ?? null,
    // LFE has no shaped send of its own; it is summed from each stem's mono
    // signal and filtered once on the bus.
    shapes: speakers.map((name) => CHANNEL_SHAPE[name] ?? "mono"),
    surround_downmix_coeff: c.surroundDownmixCoeff,
    height_downmix_coeff: c.heightDownmixCoeff,
    spatial_downmix_lock: input.spatialDownmixLock ?? false,
    sends: {
      surround_bass_cutoff_hz: c.surroundBassCutoffHz,
      height_low_rolloff_hz: c.heightShaping.lowRolloffHz,
      height_low_rolloff_gain: c.heightShaping.lowRolloffGain,
      height_crossover_hz: c.heightShaping.crossoverHz,
      height_high_shelf_gain: c.heightShaping.highShelfGain,
      height_directional_band_hz: c.heightShaping.directionalBandHz,
      height_directional_band_gain:
        input.sendOverrides?.heightDirectionalBandGain ?? c.heightShaping.directionalBandGain,
      lfe_cutoff_hz: c.lfeLowpassHz,
      lfe_filter_order: c.lfeFilterOrder,
      lfe_gain: c.lfeGain,
    },
    stems: input.stems.map((stem) => ({
      routing: Object.entries(stem.routing).filter(([name]) => index.has(name) || name === "LFE"),
      rebalance_db: stem.rebalanceDb ?? 0,
      enabled: stem.enabled ?? true,
      eq_fir: stem.eqFir ?? [],
      route_scale: stem.routeScale ?? 1,
      ambient_rear: stem.ambientRear ?? 0,
      ambient_height: stem.ambientHeight ?? 0,
      ambient_height_crossover_hz: stem.ambientHeightCrossoverHz ?? 2000,
      object_mode: stem.objectMode ?? null,
      object_placement: stem.objectPlacement ?? null,
    })),
    master: {
      head: master.highpassHz != null ? { cutoff_hz: master.highpassHz } : null,
      reference_gain: master.referenceGain ?? 1,
      reference_fir: master.referenceFir ?? [],
      eq_fir: master.eqFir ?? [],
      eq_strength: master.eqStrength ?? 1,
      dynamic_eq: master.dynamicEq ?? [],
      compressor: comp ?? null,
      bass: bass
        ? {
            sub_gain_db: bass.sub_gain_db,
            mid_gain_db: bass.mid_gain_db,
            unify_hz: bass.unify_hz,
            punch: bass.punch,
            excite: bass.excite,
            lfe_gain_db: bass.lfe_gain_db,
            sub_cutoff_hz: c.subCutoffHz,
            mid_cutoff_hz: c.midCutoffHz,
            excite_blend: c.exciteBlend,
            excite_drive: c.exciteDrive,
            punch_fast_ms: c.punchFastMs,
            punch_slow_ms: c.punchSlowMs,
            punch_max_db: c.punchMaxDb,
            decorrelate: bass.decorrelate,
            decorr_low_hz: c.decorrLowHz,
            decorr_high_hz: c.decorrHighHz,
            decorr_sections: c.decorrSections,
            decorr_max_delay_ms: c.decorrMaxDelayMs,
            decorr_fast_ms: c.decorrFastMs,
            decorr_slow_ms: c.decorrSlowMs,
          }
        : null,
      // On the bed in every output mode, matching `MasteringChain` — unlike
      // the limiter below, which is native-only.
      clip: master.clip
        ? { ceiling_dbtp: master.limiterCeilingDbtp ?? -1, ...master.clip }
        : null,
      limiter:
        outputMode === "native"
          ? {
              ceiling_dbtp: master.limiterCeilingDbtp ?? -1,
              lookahead_ms: c.limiterLookaheadMs,
              release_ms: c.limiterReleaseMs,
              safety_margin_db: c.safetyMarginDb,
            }
          : null,
      lf_targets:
        bass && bass.unify_hz !== null
          ? resolveLfTargets(speakers, bass, c.lfSpreads, c.lfeGain)
          : [],
      output_gain: master.outputGain ?? 1,
    },
    output_mode: outputMode,
    // The decode/XTC banks travel over `DspEngineClient.setDecodeTaps` /
    // `setXtcTaps` instead — see those methods' doc comments — so this block
    // never carries them.
    voicing: voicing ? voicingToWire(voicing) : null,
    // Only the collapse paths soft-limit; native output has the look-ahead
    // limiter as its safety net instead.
    soft_limit_threshold: outputMode === "native" ? 0 : c.softLimitThreshold,
    bypass_mastering: input.bypassMastering ?? false,
    meter_weights: input.meterWeights ?? [],
  };
}
