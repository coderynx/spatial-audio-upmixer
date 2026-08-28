import type { DeliveryFormat, Job } from "@/api";

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

export function jobOutputFormat(job: Job) {
  const formats = job.delivery_formats?.length
    ? job.delivery_formats
    : [job.manifest.format as DeliveryFormat | undefined];
  return [...new Set(formats.map(formatLabel))].join(" · ");
}

function formatLabel(format: DeliveryFormat | undefined) {
  const type = format?.type === "wav" ? "multichannel" : format?.type;
  if (!type) return "—";
  if (type === "adm-bwf") return "ADM-BWF";
  const label = type[0].toUpperCase() + type.slice(1);
  return format?.codec === "wav_pcm" ? `${label} WAV` : label;
}

type FoldMeasurement = {
  lkfs: number;
  tp_dbtp: number;
  plr_db: number;
  lkfs_delta_lu: number;
  tp_compliant: boolean;
  loudness_divergent: boolean;
};

type DeliveryResult = {
  measured_lkfs?: number | null;
  measured_tp_dbtp?: number | null;
  target_preset?: string | null;
  loudness_compliant?: boolean | null;
  tp_compliant?: boolean | null;
  fold_referenced?: boolean;
  folds?: {
    native_lkfs: number;
    stereo?: FoldMeasurement | null;
    surround_51?: FoldMeasurement | null;
    binaural?: FoldMeasurement | null;
  } | null;
};

function deliveryResult(job: Job) {
  return job.tracks.find((track) => track.result)?.result as DeliveryResult | undefined;
}

/** Labels for `MasteringResult.folds`, in the order the table shows them. */
const FOLD_LABELS: [keyof NonNullable<DeliveryResult["folds"]>, string][] = [
  ["stereo", "Stereo fold"],
  ["surround_51", "5.1 re-render"],
  ["binaural", "Binaural"],
];

/** Loudness and true peak of each fold of a finished master, against the
 * delivered bed's own loudness. Warnings only — the fold is never corrected
 * (docs/standards/spatial_layouts_bs775_bs2051.md §"Fold QC thresholds"). */
export function jobFolds(job: Job) {
  const folds = deliveryResult(job)?.folds;
  if (!folds) return null;
  const rows = FOLD_LABELS.flatMap(([key, label]) => {
    const fold = folds[key];
    return typeof fold === "object" && fold !== null
      ? [{ key, label, ...fold, flagged: !fold.tp_compliant || fold.loudness_divergent }]
      : [];
  });
  if (rows.length === 0) return null;
  return { nativeLkfs: folds.native_lkfs, rows, flagged: rows.some((row) => row.flagged) };
}

/** The delivered loudness a finished track reported, with the target it was
 * held to. `compliant` is null where the target publishes no tolerance —
 * there is a number to show but no pass/fail to claim. `foldReferenced`
 * marks a loudness measured on the 5.1 re-render rather than the full bed
 * (docs/standards/loudness_dsp_bs1770.md). */
export function jobDelivery(job: Job) {
  const result = deliveryResult(job);
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
