import type { Job } from "@/api";

export const statusStyle: Record<string, string> = {
  completed:
    "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  running: "border-blue-500/25 bg-blue-500/10 text-blue-700 dark:text-blue-400",
  queued:
    "border-slate-500/25 bg-slate-500/10 text-slate-700 dark:text-slate-300",
  paused:
    "border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  pause_requested:
    "border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  failed: "border-red-500/25 bg-red-500/10 text-red-700 dark:text-red-400",
  deleting:
    "border-slate-500/25 bg-slate-500/10 text-slate-700 dark:text-slate-300",
};

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
