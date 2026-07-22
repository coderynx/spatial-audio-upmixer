import { Activity, Box, Headphones } from "lucide-react";
import type { Job } from "@/api";
import { Card, CardContent } from "@/components/ui/card";

export function QueueMetrics({ jobs }: { jobs: Job[] }) {
  const running = jobs.filter((job) =>
    ["running", "queued", "pause_requested"].includes(job.status),
  ).length;
  const complete = jobs.filter((job) => job.status === "completed").length;
  const outputs = jobs.reduce(
    (total, job) =>
      total +
      job.artifacts.filter((artifact) => artifact.kind === "upmix").length,
    0,
  );
  const metrics = [
    {
      label: "Active jobs",
      value: running,
      icon: Activity,
      note: "Queued and processing",
    },
    {
      label: "Masters ready",
      value: complete,
      icon: Headphones,
      note: `${outputs} downloadable outputs`,
    },
    {
      label: "Cache policy",
      value: "Shared",
      icon: Box,
      note: "Content-addressed stems",
    },
  ];
  return (
    <section className="grid gap-3 sm:grid-cols-3">
      {metrics.map((metric) => (
        <Card key={metric.label}>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-xs font-medium text-muted-foreground">
                {metric.label}
              </p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">
                {metric.value}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {metric.note}
              </p>
            </div>
            <metric.icon className="h-5 w-5 text-muted-foreground" />
          </CardContent>
        </Card>
      ))}
    </section>
  );
}
