import { AudioLines } from "lucide-react";
import type { Job } from "@/api";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { JobActions } from "./JobActions";
import { jobDetails, statusStyle } from "./status";
import type { JobAction } from "./useJobs";

export function JobCard({
  job,
  onAction,
  onRemix,
}: {
  job: Job;
  onAction: (action: JobAction, job: Job) => void;
  onRemix: (job: Job) => void;
}) {
  const { layout, mode } = jobDetails(job);
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 gap-3">
            <div className="mt-0.5 rounded-md bg-muted p-2.5 text-muted-foreground">
              <AudioLines className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <CardTitle className="truncate text-base">{job.name}</CardTitle>
              <CardDescription className="mt-1">
                {job.tracks.length} track{job.tracks.length === 1 ? "" : "s"} ·{" "}
                {layout} · {mode}
              </CardDescription>
            </div>
          </div>
          <Badge
            variant="outline"
            className={cn("capitalize", statusStyle[job.status])}
          >
            {job.status.replace("_", " ")}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="truncate text-muted-foreground">
              {job.status_message}
            </span>
            <span className="ml-3 font-mono tabular-nums">
              {Math.round(job.progress * 100)}%
            </span>
          </div>
          <Progress value={job.progress * 100} />
        </div>
        <div className="border-t pt-3">
          <JobActions job={job} onAction={onAction} onRemix={onRemix} />
        </div>
        {job.error && (
          <p className="rounded-md bg-destructive/10 p-2 text-xs text-destructive">
            {job.error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
