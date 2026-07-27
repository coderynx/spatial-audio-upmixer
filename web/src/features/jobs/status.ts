import type { Job } from "@/api";

export function statusVariant(status: string) {
  if (status === "completed") return "success" as const;
  if (status === "running") return "default" as const;
  if (status === "paused" || status === "pause_requested") return "warning" as const;
  if (status === "failed") return "destructive" as const;
  return "secondary" as const;
}

export function statusLabel(status: string) {
  return status.replace(/_/g, " ");
}

export function jobDetails(job: Job) {
  const layout =
    (job.manifest.mixing as { channel_layout?: string } | undefined)
      ?.channel_layout || "—";
  const mode =
    (job.manifest.engine as { mode?: string } | undefined)?.mode || "realtime";
  const downloadable = job.artifacts.filter(
    (artifact) =>
      artifact.kind === (job.tracks.length > 1 ? "bundle" : "upmix"),
  );
  return { layout, mode, downloadable };
}
