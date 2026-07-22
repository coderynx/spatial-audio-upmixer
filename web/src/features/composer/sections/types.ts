import type { Configuration } from "@/api";
import type { Manifest } from "@/lib/manifest";

export type ManifestSectionProps = {
  manifest: Manifest;
  setManifest: React.Dispatch<React.SetStateAction<Manifest>>;
  configuration: Configuration | null;
};
