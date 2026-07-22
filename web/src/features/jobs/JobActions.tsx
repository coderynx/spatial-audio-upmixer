import { Download, Pause, Play, RotateCcw, Trash2 } from "lucide-react";
import type { Job } from "@/api";
import { Button } from "@/components/ui/button";
import type { JobAction } from "./useJobs";
import { jobDetails } from "./status";

export function JobActions({
  job,
  onAction,
  onRemix,
  compact = false,
}: {
  job: Job;
  onAction: (action: JobAction, job: Job) => void;
  onRemix: (job: Job) => void;
  compact?: boolean;
}) {
  const { downloadable } = jobDetails(job);
  return (
    <div className="flex items-center justify-end gap-1">
      {(job.status === "running" || job.status === "queued") && (
        <Button
          variant="ghost"
          size={compact ? "icon" : "sm"}
          aria-label="Pause job"
          onClick={() => onAction("pause", job)}
        >
          <Pause />
          {!compact && "Pause"}
        </Button>
      )}
      {(job.status === "paused" || job.status === "failed") && (
        <Button
          variant="ghost"
          size={compact ? "icon" : "sm"}
          aria-label="Resume job"
          onClick={() => onAction("resume", job)}
        >
          <Play />
          {!compact && "Resume"}
        </Button>
      )}
      {job.status === "completed" && (
        <Button
          variant="ghost"
          size={compact ? "icon" : "sm"}
          aria-label="Remix job"
          onClick={() => onRemix(job)}
        >
          <RotateCcw />
          {!compact && "Remix"}
        </Button>
      )}
      {downloadable.map((artifact) => (
        <Button
          key={artifact.id}
          variant="outline"
          size={compact ? "icon" : "sm"}
          asChild
        >
          <a
            href={artifact.download_url}
            aria-label={`Download ${artifact.filename}`}
          >
            <Download />
            {!compact && "Download"}
          </a>
        </Button>
      ))}
      <Button
        variant="ghost"
        size="icon"
        aria-label={`Delete ${job.name}`}
        onClick={() => onAction("delete", job)}
      >
        <Trash2 className="text-muted-foreground" />
      </Button>
    </div>
  );
}
