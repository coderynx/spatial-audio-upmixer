import type { Job } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { JobActions } from "./JobActions";
import { jobDelivery, jobDetails, jobOutputFormat, statusLabel, statusVariant } from "./status";
import type { JobAction } from "./useJobs";

export function JobTable({
  jobs,
  selectedId,
  onSelect,
  onAction,
  onRemix,
}: {
  jobs: Job[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAction: (action: JobAction, job: Job) => void;
  onRemix: (job: Job) => void;
}) {
  return (
    <table className="w-full min-w-[960px] text-left text-[13px]">
      <thead className="sticky top-0 z-10 border-b bg-card text-[11px] font-semibold uppercase tracking-[.06em] text-muted-foreground">
        <tr>
          <th className="px-3 py-1.5 font-semibold">Job</th>
          <th className="px-3 py-1.5 font-semibold">Render</th>
          <th className="px-3 py-1.5 font-semibold">Output</th>
          <th className="px-3 py-1.5 font-semibold">Delivered</th>
          <th className="px-3 py-1.5 font-semibold">Status</th>
          <th className="min-w-40 px-3 py-1.5 font-semibold">Progress</th>
          <th className="px-3 py-1.5 font-semibold">Updated</th>
          <th className="px-3 py-1.5 text-right font-semibold">Actions</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => {
          const { layout, mode } = jobDetails(job);
          const delivery = jobDelivery(job);
          return (
            <tr
              key={job.id}
              onClick={() => onSelect(job.id)}
              className={cn(
                "cursor-pointer border-b last:border-0",
                job.id === selectedId ? "bg-primary/10" : "hover:bg-accent/50",
              )}
            >
              <td className="max-w-xs px-3 py-1.5">
                <p className="truncate font-medium">{job.name}</p>
                <p className="truncate text-[11px] text-muted-foreground">
                  {job.tracks.length} track{job.tracks.length === 1 ? "" : "s"} · {job.status_message}
                </p>
              </td>
              <td className="px-3 py-1.5">
                <p className="tabular-nums">{layout}</p>
                <p className="text-[11px] capitalize text-muted-foreground">{mode}</p>
              </td>
              <td className="whitespace-nowrap px-3 py-1.5">{jobOutputFormat(job)}</td>
              <td className="whitespace-nowrap px-3 py-1.5">
                {delivery ? (
                  <>
                    <p className="tabular-nums">
                      {delivery.lkfs.toFixed(1)} LKFS
                      {delivery.dbtp === null ? "" : ` · ${delivery.dbtp.toFixed(1)} dBTP`}
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      {delivery.preset ?? "custom"}
                      {delivery.foldReferenced ? " · 5.1 fold" : ""}
                      {delivery.compliant === null
                        ? ""
                        : delivery.compliant
                          ? " · pass"
                          : " · fail"}
                    </p>
                  </>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="px-3 py-1.5">
                <Badge variant={statusVariant(job.status)} className="capitalize">
                  {statusLabel(job.status)}
                </Badge>
              </td>
              <td className="px-3 py-1.5">
                <div className="flex items-center gap-2">
                  <Progress value={job.progress * 100} />
                  <span className="w-8 shrink-0 text-right text-[11px] tabular-nums">
                    {Math.round(job.progress * 100)}%
                  </span>
                </div>
              </td>
              <td className="whitespace-nowrap px-3 py-1.5 text-[11px] text-muted-foreground">
                {formatDate(job.updated_at)}
              </td>
              <td className="px-3 py-1.5">
                <JobActions job={job} onAction={onAction} onRemix={onRemix} compact />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
