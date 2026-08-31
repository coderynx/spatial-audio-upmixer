import type { ProjectStem, StemScene } from "@/api";
import type { EngineConstants } from "../masteringProfiles";
import { estimateRouteScale } from "../masteringProfiles";
import type { StemMix } from "./engineParams";
import type { StemDynamicEqSettings, StemDynamicsSettings, StemEqSettings } from "@/lib/manifest";
import { isBedStem } from "@/lib/stems";

export type MixPreviewShape = {
  stem_routing?: Record<string, Record<string, number>>;
  bed_trim_db?: number;
  stem_rebalance?: Record<string, number>;
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_eq?: Record<string, string | StemEqSettings>;
  stem_dynamic_eq?: Record<string, StemDynamicEqSettings>;
  stem_dynamics?: Record<string, StemDynamicsSettings>;
  stem_ambient_rear?: Record<string, number>;
  stem_ambient_height?: Record<string, number>;
  stem_ambient_height_crossover_hz?: Record<string, number>;
  spatial_downmix_lock?: boolean;
  stem_object_mode?: Record<string, "linked-stereo" | "mono">;
  stem_object_metadata?: Record<string, { gain?: number; importance?: number; channel_lock?: boolean; zone_exclusion?: string[] }>;
  stem_placement?: Record<string, { azimuth_deg: number; elevation_deg: number; width_deg: number; object_size: number; diversity?: number; center_level_db?: number }>;
  stem_source_anchor_strength?: number;
};

/** Resolve the project's mix into the core's per-stem parameters. */
export function resolveStemMixes(options: {
  stems: ProjectStem[];
  scene: { stems?: StemScene };
  mix: MixPreviewShape | undefined;
  stemEqTaps: Map<string, Float64Array>;
  constants: EngineConstants;
}): StemMix[] {
  const { stems, scene: sceneRoot, mix, constants } = options;
  const anchor = mix?.stem_source_anchor_strength || 0;
  return stems.map((stem) => {
    const base = stem.stem_key.split("@", 1)[0];
    const scene = sceneRoot.stems?.[stem.stem_key] || sceneRoot.stems?.[base] || {};
    // Routing is always the core's: `routing_for_scene` pans a scene position
    // through the same panner the export uses, so the preview never derives
    // one of its own.
    const routing = mix?.stem_routing?.[stem.stem_key] || mix?.stem_routing?.[base] || {};

    let total = 0;
    let frontWeight = 0;
    for (const [channel, weight] of Object.entries(routing)) {
      if (weight <= 0) continue;
      total += weight;
      if (channel === "FL" || channel === "FR") frontWeight += weight;
    }
    // Only the FL/FR portion crossfades toward the dry source, matching
    // source_anchor.py's front-zone-only blend.
    const frontFraction = total > 0 ? frontWeight / total : 0;

    const soloed = mix?.stem_solo?.length
      ? mix.stem_solo.includes(stem.stem_key) || mix.stem_solo.includes(base)
      : true;
    const enabled = soloed && mix?.stem_enabled?.[base] !== false && scene.enabled !== false;

    const send = (table: Record<string, number> | undefined) => {
      const value = table?.[stem.stem_key] ?? table?.[base] ?? 0;
      return Math.min(1, Math.max(0, value));
    };
    const crossover = mix?.stem_ambient_height_crossover_hz?.[stem.stem_key]
      ?? mix?.stem_ambient_height_crossover_hz?.[base]
      ?? 2000;
    const placement = mix?.stem_placement?.[stem.stem_key]
      ?? mix?.stem_placement?.[base]
      ?? (scene.azimuth_deg != null ? {
        azimuth_deg: scene.azimuth_deg,
        elevation_deg: scene.elevation_deg ?? 0,
        width_deg: 0,
        object_size: 0,
      } : undefined);
    const objectMode = placement && !isBedStem(stem.stem_key)
      ? mix?.stem_object_mode?.[stem.stem_key] ?? mix?.stem_object_mode?.[base] ?? "linked-stereo"
      : undefined;
    const objectMetadata = mix?.stem_object_metadata?.[stem.stem_key]
      ?? mix?.stem_object_metadata?.[base];

    const storedEq = mix?.stem_eq?.[stem.stem_key] ?? mix?.stem_eq?.[base];
    const preset = typeof storedEq === "string" ? constants.stemEqSettings[storedEq] : storedEq?.preset ? constants.stemEqSettings[storedEq.preset] : undefined;
    const eq = storedEq && typeof storedEq === "object" ? {
      ...preset, ...storedEq,
      highpass: { ...preset?.highpass, ...storedEq.highpass }, low_shelf: { ...preset?.low_shelf, ...storedEq.low_shelf },
      bell_1: { ...preset?.bell_1, ...storedEq.bell_1 }, bell_2: { ...preset?.bell_2, ...storedEq.bell_2 },
      high_shelf: { ...preset?.high_shelf, ...storedEq.high_shelf }, lowpass: { ...preset?.lowpass, ...storedEq.lowpass },
    } : preset;
    const dynamics = mix?.stem_dynamics?.[stem.stem_key] ?? mix?.stem_dynamics?.[base];
    const dynamicEq = mix?.stem_dynamic_eq?.[stem.stem_key] ?? mix?.stem_dynamic_eq?.[base];
    const anchorDb = 20 * Math.log10(Math.max(1 - anchor * frontFraction, 1e-6));
    return {
      id: stem.id,
      routing,
      rebalanceDb: (mix?.stem_rebalance?.[base] || 0)
        + (isBedStem(stem.stem_key) ? mix?.bed_trim_db || 0 : 0)
        + anchorDb,
      enabled,
      eq,
      dynamics,
      dynamicEq,
      routeScale: objectMode ? 1 : estimateRouteScale(routing, constants.channelGains),
      ambientRear: send(mix?.stem_ambient_rear),
      ambientHeight: send(mix?.stem_ambient_height),
      ambientHeightCrossoverHz: Math.min(4000, Math.max(500, crossover)),
      objectMode,
      objectPlacement: placement && {
        ...placement,
        gain: objectMetadata?.gain ?? 1,
        channel_lock: objectMetadata?.channel_lock ?? false,
        zone_exclusion: objectMetadata?.zone_exclusion ?? [],
      },
    };
  });
}
