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

/** The delivered loudness a finished track reported, with the target it was
 * held to. `compliant` is null where the target publishes no tolerance —
 * there is a number to show but no pass/fail to claim. `foldReferenced`
 * marks a loudness measured on the 5.1 re-render rather than the full bed
 * (docs/standards/loudness_dsp_bs1770.md). */
export function jobDelivery(job: Job) {
  const result = job.tracks.find((track) => track.result)?.result as
    | {
        measured_lkfs?: number | null;
        measured_tp_dbtp?: number | null;
        target_preset?: string | null;
        loudness_compliant?: boolean | null;
        tp_compliant?: boolean | null;
        fold_referenced?: boolean;
      }
    | undefined;
  if (!result || result.measured_lkfs == null) return null;
  const compliant =
    result.loudness_compliant == null && result.tp_compliant == null
      ? null
      : result.loudness_compliant !== false && result.tp_compliant !== false;
  return {
    lkfs: result.measured_lkfs,
    dbtp: result.measured_tp_dbtp ?? null,
    preset: result.target_preset ?? null,
    foldReferenced: result.fold_referenced === true,
    compliant,
  };
}
