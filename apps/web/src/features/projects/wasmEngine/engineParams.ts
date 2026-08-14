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

/** BS.775-4 Annex 4 Table 2 contributions; height channels and LFE excluded. */
function downmixGains(
  channel: string,
  c: EngineConstants,
): [number, number] | undefined {
  const itu = c.ituCenterCoeff;
  const surround = c.surroundDownmixCoeff;
  switch (channel) {
    case "FL":
      return [1, 0];
    case "FR":
      return [0, 1];
    case "C":
      return [itu, itu];
    case "SL":
      return [surround, 0];
    case "SR":
      return [0, surround];
    case "BL":
      return [surround * itu, 0];
    case "BR":
      return [0, surround * itu];
    default:
      return undefined;
  }
}

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
};

export type MasterMix = {
  /** Already resolved — profile preset merged with the project's per-field
   * overrides. Resolving upstream is what keeps a moved pot from being
   * dropped between the UI and the worklet (ledger D30). */
  comp?: CompProfile | null;
  bass?: BassProfile | null;
  eqFir?: Float64Array | number[];
  eqStrength?: number;
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
  /** Per-speaker mute; a muted speaker contributes nothing to any render. */
  speakerEnabled?: Record<string, boolean>;
  /** Transport A/B: render the bed without any mastering stage. */
  bypassMastering?: boolean;
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
      // Muting a speaker zeroes its group gain, which silences everything
      // routed to it without disturbing any other channel.
      const muted = input.speakerEnabled?.[name] === false;
      return {
        name,
        azimuth_rad: direction.azimuth_rad,
        elevation_rad: direction.elevation_rad,
        group_gain: muted ? 0 : groupGain(name, c),
        downmix: downmixGains(name, c) ?? null,
      };
    }),
    lfe_index: lfeIndex ?? null,
    // LFE has no shaped send of its own; it is summed from each stem's mono
    // signal and filtered once on the bus.
    shapes: speakers.map((name) => CHANNEL_SHAPE[name] ?? "mono"),
    sends: {
      surround_bass_cutoff_hz: c.surroundBassCutoffHz,
      surround_haas_ms: [c.surroundHaasMs.left, c.surroundHaasMs.right],
      height_haas_ms: [c.heightHaasMs.left, c.heightHaasMs.right],
      diffuse_blend: c.diffuseSendBlend,
      height_low_rolloff_hz: c.heightShaping.lowRolloffHz,
      height_low_rolloff_gain: c.heightShaping.lowRolloffGain,
      height_crossover_hz: c.heightShaping.crossoverHz,
      height_high_shelf_gain: c.heightShaping.highShelfGain,
      lfe_cutoff_hz: c.lfeLowpassHz,
      lfe_filter_order: 4,
      lfe_gain: c.lfeGain,
    },
    stems: input.stems.map((stem) => ({
      routing: Object.entries(stem.routing).filter(([name]) => index.has(name) || name === "LFE"),
      rebalance_db: stem.rebalanceDb ?? 0,
      enabled: stem.enabled ?? true,
      eq_fir: stem.eqFir ? Array.from(stem.eqFir) : [],
      route_scale: stem.routeScale ?? 1,
    })),
    master: {
      reference_gain: master.referenceGain ?? 1,
      reference_fir: master.referenceFir ? Array.from(master.referenceFir) : [],
      eq_fir: master.eqFir ? Array.from(master.eqFir) : [],
      eq_strength: master.eqStrength ?? 1,
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
  };
}
