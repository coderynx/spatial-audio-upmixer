import type { ProjectTrack } from "@/api";
import { normalizeManifest } from "@/lib/manifest";

const PROFILE_LABELS: Record<string, string> = {
  "atmos-music": "Dolby Atmos Music ADM-BWF", "netflix-atmos-movie": "Netflix Dolby Atmos Movie",
  "disney-plus-atmos-movie": "Disney+ Dolby Atmos Movie", "amazon-prime-atmos-movie": "Amazon Prime Video Dolby Atmos Movie",
  "apple-tv-plus-atmos-movie": "Apple TV+ Dolby Atmos Movie", "max-atmos-movie": "Max (HBO) Dolby Atmos Movie",
};

export function deliveryTargetLabel(track: ProjectTrack, layout: string) {
  const profile = normalizeManifest(track.layout_overrides[layout] || {}).format.delivery_profile;
  return `${PROFILE_LABELS[profile || ""] || "Unspecified"} · ${layout}`;
}
