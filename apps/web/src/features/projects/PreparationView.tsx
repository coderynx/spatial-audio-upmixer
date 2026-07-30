import * as React from "react";
import { Loader2, RotateCcw, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { Project } from "@/api";
import { gerundAt } from "./agentFlavor";

const FAILED_STATUSES = new Set(["failed", "expansion_failed"]);

/** Realtime "agent" log for a project's stem preparation — rendered inline
 * as one state of the Prepare tab (see `assets/AssetsTab.tsx`) rather than a
 * full-page gate, so the stage bar and track tree stay reachable while
 * preparation runs. */
export function PreparationPanel({ project, onRetry }: { project: Project; onRetry: () => void }) {
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
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border bg-card">
      <header className="flex h-8 shrink-0 items-center gap-2 border-b px-3">
        <Terminal className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="text-[13px] font-medium">Preparing stems</span>
        <div className="min-w-0 flex-1" />
        {!failed && (
          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
            {gerundAt(tick)}…
          </span>
        )}
      </header>
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
        <Progress value={project.progress * 100} className="flex-1" />
        <span className="w-9 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground">
          {Math.round(project.progress * 100)}%
        </span>
      </div>
      <p className="shrink-0 border-b px-3 py-1.5 text-[11px] text-muted-foreground">{project.status_message}</p>
      {project.error && (
        <p className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-3 py-1.5 text-[11px] text-destructive">
          {project.error}
        </p>
      )}
      <div ref={logRef} className="min-h-0 flex-1 overflow-y-auto bg-background p-3 font-mono text-[11px] leading-relaxed">
        {lines.length === 0 && <p className="text-muted-foreground">Waiting for worker…</p>}
        {lines.map((entry, index) => (
          <p key={index} className="flex gap-2">
            <span className="shrink-0 text-muted-foreground">{new Date(entry.ts).toLocaleTimeString()}</span>
            <span className="text-foreground">{entry.message}</span>
          </p>
        ))}
      </div>
      {failed && (
        <div className="shrink-0 border-t p-2">
          <Button className="w-full" onClick={onRetry}>
            <RotateCcw />
            Retry preparation
          </Button>
        </div>
      )}
    </div>
  );
}
