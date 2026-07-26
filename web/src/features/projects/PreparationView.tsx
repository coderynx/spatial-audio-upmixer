import * as React from "react";
import { Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { Project } from "@/api";
import { gerundAt } from "./agentFlavor";

const FAILED_STATUSES = new Set(["failed", "expansion_failed"]);

/** Realtime "agent" log shown while a project's stems are being prepared. */
export function PreparationView({ project, onRetry }: { project: Project; onRetry: () => void }) {
  const [tick, setTick] = React.useState(0);
  const logRef = React.useRef<HTMLDivElement | null>(null);
  const failed = FAILED_STATUSES.has(project.status);

  React.useEffect(() => {
    if (failed) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), 1400);
    return () => window.clearInterval(timer);
  }, [failed]);

  const progressLog = project.progress_log ?? [];

  React.useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [progressLog.length]);

  const lines = React.useMemo(() => {
    const deduped: typeof progressLog = [];
    for (const entry of progressLog) {
      const previous = deduped[deduped.length - 1];
      if (previous && previous.message === entry.message) continue;
      deduped.push(entry);
    }
    return deduped;
  }, [progressLog]);

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-4 p-5">
      <h1 className="text-2xl font-semibold">{project.name}</h1>
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        {!failed && <Loader2 className="h-4 w-4 shrink-0 animate-spin" />}
        <span>
          {!failed && <span className="font-medium text-foreground">{gerundAt(tick)}… </span>}
          {project.status_message}
        </span>
      </div>
      <Progress value={project.progress * 100} />
      <p className="text-right text-xs text-muted-foreground">{Math.round(project.progress * 100)}%</p>
      <div
        ref={logRef}
        className="max-h-64 overflow-y-auto rounded-md border bg-muted/20 p-3 font-mono text-xs leading-relaxed"
      >
        {lines.length === 0 && <p className="text-muted-foreground">Waiting for worker…</p>}
        {lines.map((entry, index) => (
          <p key={index} className="flex gap-2">
            <span className="shrink-0 text-muted-foreground">
              {new Date(entry.ts).toLocaleTimeString()}
            </span>
            <span>{entry.message}</span>
          </p>
        ))}
      </div>
      {failed && (
        <Button className="self-start" onClick={onRetry}>
          <RotateCcw />
          Retry preparation
        </Button>
      )}
    </main>
  );
}
