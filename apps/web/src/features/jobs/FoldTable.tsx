import { cn } from "@/lib/utils";
import type { jobFolds } from "./status";

/** What a delivered master measures after each collapse, against the bed's own
 * loudness. A flagged row is a warning, never a correction — see
 * docs/standards/spatial_layouts_bs775_bs2051.md §"Fold QC thresholds". */
export function FoldTable({ folds }: { folds: NonNullable<ReturnType<typeof jobFolds>> }) {
  return (
    <>
      <table className="w-full text-left text-[11px] tabular-nums">
        <thead className="text-[10px] font-semibold uppercase tracking-[.06em] text-muted-foreground">
          <tr>
            <th className="py-1 pr-2 font-semibold">Fold</th>
            <th className="py-1 pr-2 text-right font-semibold">LKFS</th>
            <th className="py-1 pr-2 text-right font-semibold">Δ LU</th>
            <th className="py-1 text-right font-semibold">dBTP</th>
          </tr>
        </thead>
        <tbody>
          {folds.rows.map((row) => (
            <tr key={row.key} className="border-b last:border-0">
              <td className={cn("py-1 pr-2", row.flagged && "text-warning")}>{row.label}</td>
              <td className="py-1 pr-2 text-right">{row.lkfs.toFixed(1)}</td>
              <td
                className={cn("py-1 pr-2 text-right", row.loudness_divergent && "text-warning")}
                title={
                  row.loudness_divergent
                    ? "Diverges from the delivered bed's loudness beyond the ±1.5 LU threshold"
                    : undefined
                }
              >
                {row.lkfs_delta_lu >= 0 ? "+" : ""}
                {row.lkfs_delta_lu.toFixed(2)}
              </td>
              <td
                className={cn("py-1 text-right", !row.tp_compliant && "text-warning")}
                title={!row.tp_compliant ? "Over the delivery target's true-peak ceiling" : undefined}
              >
                {row.tp_dbtp.toFixed(1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">
        {folds.flagged
          ? `Measured against the delivered bed at ${folds.nativeLkfs.toFixed(1)} LKFS. Flagged folds are reported, not corrected — revisit the mix or the mastering settings.`
          : `Measured against the delivered bed at ${folds.nativeLkfs.toFixed(1)} LKFS.`}
      </p>
    </>
  );
}
