import type { ProjectStem, StemScene } from "@/api";
import type { EngineConstants } from "../masteringProfiles";
import { estimateRouteScale } from "../masteringProfiles";
import type { StemMix } from "./engineParams";

export type MixPreviewShape = {
  stem_routing?: Record<string, Record<string, number>>;
  stem_rebalance?: Record<string, number>;
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_eq?: Record<string, string>;
  stem_ambient_rear?: Record<string, number>;
  stem_ambient_height?: Record<string, number>;
  stem_ambient_height_crossover_hz?: Record<string, number>;
  spatial_downmix_lock?: boolean;
  stem_object_mode?: Record<string, "linked-stereo" | "mono">;
  stem_object_metadata?: Record<string, { gain?: number; importance?: number; channel_lock?: boolean; zone_exclusion?: string[] }>;
  stem_placement?: Record<string, { azimuth_deg: number; elevation_deg: number; width_deg: number; object_size: number }>;
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
  const { stems, scene: sceneRoot, mix, stemEqTaps, constants } = options;
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
    const objectMode = placement
      ? mix?.stem_object_mode?.[stem.stem_key] ?? mix?.stem_object_mode?.[base] ?? "linked-stereo"
      : undefined;
    const objectMetadata = mix?.stem_object_metadata?.[stem.stem_key]
      ?? mix?.stem_object_metadata?.[base];

    const anchorDb = 20 * Math.log10(Math.max(1 - anchor * frontFraction, 1e-6));
    return {
      id: stem.id,
      routing,
      rebalanceDb: (mix?.stem_rebalance?.[base] || 0) + anchorDb,
      enabled,
      eqFir: stemEqTaps.get(stem.stem_key),
      routeScale: estimateRouteScale(routing, constants.channelGains),
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
