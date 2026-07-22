import { Archive } from "lucide-react";
import type { Job } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { JobCard } from "./JobCard";
import { JobTable } from "./JobTable";
import { QueueMetrics } from "./QueueMetrics";
import type { JobAction } from "./useJobs";

export function JobsPage({
  jobs,
  loading,
  error,
  onAction,
  onRemix,
  onCreate,
}: {
  jobs: Job[];
  loading: boolean;
  error: string | null;
  onAction: (action: JobAction, job: Job) => void;
  onRemix: (job: Job) => void;
  onCreate: () => void;
}) {
  return (
    <main className="mx-auto max-w-7xl space-y-6 p-4 sm:p-7">
      <section className="flex flex-col justify-between gap-4 border-b pb-6 sm:flex-row sm:items-end">
        <div>
          <p className="text-sm font-medium text-muted-foreground">Upmixer</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Jobs</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Create, monitor, and retrieve multichannel rendering jobs.
          </p>
        </div>
        <Button onClick={onCreate}>New job</Button>
      </section>
      <QueueMetrics jobs={jobs} />
      <section>
        <div className="mb-4 flex items-end justify-between">
          <div>
            <h2 className="text-lg font-semibold">Processing queue</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Jobs persist until deleted.
            </p>
          </div>
          <Badge variant="outline">{jobs.length} total</Badge>
        </div>
        {error && (
          <p className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </p>
        )}
        {loading ? (
          <div className="space-y-3">
            {[0, 1, 2, 3].map((item) => (
              <div
                key={item}
                className="h-16 animate-pulse rounded-md border bg-muted/40"
              />
            ))}
          </div>
        ) : jobs.length ? (
          <>
            <JobTable jobs={jobs} onAction={onAction} onRemix={onRemix} />
            <div className="grid gap-4 md:hidden">
              {jobs.map((job) => (
                <JobCard
                  key={job.id}
                  job={job}
                  onAction={onAction}
                  onRemix={onRemix}
                />
              ))}
            </div>
          </>
        ) : (
          <Card className="border-dashed">
            <CardContent className="flex min-h-64 flex-col items-center justify-center text-center">
              <div className="rounded-md bg-muted p-3 text-muted-foreground">
                <Archive className="h-7 w-7" />
              </div>
              <h3 className="mt-4 font-semibold">No jobs</h3>
              <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                Create an upmix job from a track, album folder, or ZIP archive.
              </p>
              <Button className="mt-5" onClick={onCreate}>
                Create job
              </Button>
            </CardContent>
          </Card>
        )}
      </section>
    </main>
  );
}
