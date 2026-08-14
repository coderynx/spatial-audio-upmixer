import type { ProjectStem, StemScene } from "@/api";
import { routingFromAzimuthElevation } from "@/lib/spatial";
import type { EngineConstants } from "../masteringProfiles";
import { estimateRouteScale } from "../masteringProfiles";
import type { StemMix } from "./engineParams";

export type MixPreviewShape = {
  stem_routing?: Record<string, Record<string, number>>;
  stem_rebalance?: Record<string, number>;
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_eq?: Record<string, string>;
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
    let routing = mix?.stem_routing?.[stem.stem_key] || mix?.stem_routing?.[base];
    // No resolved routing yet (a freshly dropped stem, say) — fall back to
    // the same nearest-3-speakers weighting `routing_for_scene` uses.
    if (!routing || Object.keys(routing).length === 0) {
      routing =
        scene.azimuth_deg != null || scene.elevation_deg != null
          ? routingFromAzimuthElevation(scene.azimuth_deg || 0, scene.elevation_deg || 0)
          : {};
    }

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

    const anchorDb = 20 * Math.log10(Math.max(1 - anchor * frontFraction, 1e-6));
    return {
      id: stem.id,
      routing,
      rebalanceDb: (mix?.stem_rebalance?.[base] || 0) + anchorDb,
      enabled,
      eqFir: stemEqTaps.get(stem.stem_key),
      routeScale: estimateRouteScale(routing, constants.channelGains),
    };
  });
}
