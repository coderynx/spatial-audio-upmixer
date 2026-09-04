import type { ProjectStem, StemScene } from "@/api";
import type { SpatialProfile, TransauralProfile } from "./masteringProfiles";
import type { MasterPreview } from "./masterPreview";
import type { MixPreview, OutputMode } from "./wasmEngine/engineTypes";

/** One fully resolved track-layout mix ready for a preview host to render. */
export type PreviewProgramme = {
  sourceKey: string;
  key: string;
  stems: ProjectStem[];
  scene: { stems?: StemScene };
  mix: MixPreview;
  sourcePreviewUrl: string | null;
  mastering: MasterPreview | undefined;
  routing: { height_directional_band_gain?: number } | undefined;
  layoutChannels: string[];
};

export type PreviewMonitor = {
  outputMode: OutputMode;
  spatialProfile: SpatialProfile;
  transauralProfile: TransauralProfile;
  appleHeadTracking: boolean;
  speakerEnabled: Record<string, boolean>;
  masteringBypassed: boolean;
  matchBypassed: boolean;
};

type PreviewProgrammeInput = Pick<PreviewProgramme, "stems" | "mix" | "layoutChannels"> & Partial<Pick<PreviewProgramme, "scene" | "sourcePreviewUrl" | "mastering" | "routing">>;

export function createPreviewProgramme({
  stems,
  scene = {},
  mix,
  sourcePreviewUrl = null,
  mastering,
  routing,
  layoutChannels,
}: PreviewProgrammeInput): PreviewProgramme {
  const sourceKey = `${stems.map((stem) => `${stem.id}:${stem.preview_url || stem.audio_url}`).join("|")}|${sourcePreviewUrl || ""}`;
  return {
    sourceKey,
    key: JSON.stringify({ layoutChannels, scene: scene.stems ?? null, mix, mastering, routing }),
    stems,
    scene,
    mix,
    sourcePreviewUrl,
    mastering,
    routing,
    layoutChannels,
  };
}

export function createPreviewMonitor({
  outputMode = "binaural",
  spatialProfile = "studio",
  transauralProfile = "stereo",
  appleHeadTracking = true,
  speakerEnabled = {},
  masteringBypassed = false,
  matchBypassed = false,
}: Partial<PreviewMonitor> = {}): PreviewMonitor {
  return {
    outputMode,
    spatialProfile,
    transauralProfile,
    appleHeadTracking,
    speakerEnabled,
    masteringBypassed,
    matchBypassed,
  };
}
