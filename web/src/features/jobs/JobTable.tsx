import type { Job } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { JobActions } from "./JobActions";
import { jobDetails, statusStyle } from "./status";
import type { JobAction } from "./useJobs";

export function JobTable({
  jobs,
  onAction,
  onRemix,
}: {
  jobs: Job[];
  onAction: (action: JobAction, job: Job) => void;
  onRemix: (job: Job) => void;
}) {
  return (
    <div className="hidden overflow-x-auto rounded-md border md:block">
      <table className="w-full min-w-[850px] text-left text-sm">
        <thead className="border-b bg-muted/50 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-2">Job</th>
            <th className="px-4 py-2">Render</th>
            <th className="px-4 py-2">Status</th>
            <th className="min-w-44 px-4 py-2">Progress</th>
            <th className="px-4 py-2">Updated</th>
            <th className="px-4 py-2 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const { layout, mode } = jobDetails(job);
            return (
              <tr
                key={job.id}
                className="border-b last:border-0 hover:bg-muted/30"
              >
                <td className="max-w-xs px-4 py-2.5">
                  <p className="truncate font-medium">{job.name}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {job.tracks.length} track
                    {job.tracks.length === 1 ? "" : "s"}
                  </p>
                </td>
                <td className="px-4 py-2.5">
                  <p>{layout}</p>
                  <p className="mt-1 text-xs capitalize text-muted-foreground">
                    {mode}
                  </p>
                </td>
                <td className="px-4 py-2.5">
                  <Badge
                    variant="outline"
                    className={cn("capitalize", statusStyle[job.status])}
                  >
                    {job.status.replace("_", " ")}
                  </Badge>
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-3">
                    <Progress value={job.progress * 100} />
                    <span className="w-9 font-mono text-xs tabular-nums">
                      {Math.round(job.progress * 100)}%
                    </span>
                  </div>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {job.status_message}
                  </p>
                </td>
                <td className="whitespace-nowrap px-4 py-2.5 text-xs text-muted-foreground">
                  {formatDate(job.updated_at)}
                </td>
                <td className="px-4 py-2.5">
                  <JobActions
                    job={job}
                    onAction={onAction}
                    onRemix={onRemix}
                    compact
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
