import * as React from "react";
import { Loader2, RotateCcw, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { InspectorGroup, InspectorRow } from "@/app/InspectorRow";
import { StatusBar, StatusCell, StatusSeparator, StatusSpacer } from "@/app/StatusBar";
import { Toolbar, ToolbarSpacer } from "@/app/Toolbar";
import { Workspace, WorkspaceScroll } from "@/app/Workspace";
import type { Project } from "@/api";
import { formatDate } from "@/lib/format";
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
    <Workspace
      toolbar={
        <Toolbar>
          <Terminal className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="text-[13px] font-medium">Preparing stems</span>
          <ToolbarSpacer />
          {!failed && (
            <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
              {gerundAt(tick)}…
            </span>
          )}
        </Toolbar>
      }
      rail={
        <>
          <WorkspaceScroll>
            <InspectorGroup title="Project">
              <p className="mb-1.5 truncate text-[13px] font-semibold">{project.name}</p>
              <InspectorRow label="Status" value={project.status.replace(/_/g, " ")} />
              <InspectorRow label="Tracks" value={project.tracks.length} />
              <InspectorRow label="Requested stems" value={project.requested_stems.length} />
              <InspectorRow label="Prepared stems" value={project.prepared_stems.length} />
              <InspectorRow label="Updated" value={formatDate(project.updated_at)} />
            </InspectorGroup>
            <InspectorGroup title="Progress">
              <div className="flex items-center gap-2">
                <Progress value={project.progress * 100} />
                <span className="w-8 shrink-0 text-right text-[11px] tabular-nums">
                  {Math.round(project.progress * 100)}%
                </span>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{project.status_message}</p>
            </InspectorGroup>
            {project.error && (
              <InspectorGroup title="Error">
                <p className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-[11px] leading-relaxed text-destructive">
                  {project.error}
                </p>
              </InspectorGroup>
            )}
          </WorkspaceScroll>
          {failed && (
            <div className="shrink-0 border-t p-2">
              <Button className="w-full" onClick={onRetry}>
                <RotateCcw />
                Retry preparation
              </Button>
            </div>
          )}
        </>
      }
      status={
        <StatusBar>
          <StatusCell label="Progress" value={`${Math.round(project.progress * 100)}%`} />
          <StatusSeparator />
          <StatusCell label="Log lines" value={lines.length} />
          <StatusSpacer />
          <span className="truncate">{project.status_message}</span>
        </StatusBar>
      }
    >
      <div ref={logRef} className="min-h-0 flex-1 overflow-y-auto bg-black p-3 font-mono text-[11px] leading-relaxed">
        {lines.length === 0 && <p className="text-muted-foreground">Waiting for worker…</p>}
        {lines.map((entry, index) => (
          <p key={index} className="flex gap-2">
            <span className="shrink-0 text-[#8E8E93]">{new Date(entry.ts).toLocaleTimeString()}</span>
            <span className="text-[#EBEBF5]">{entry.message}</span>
          </p>
        ))}
      </div>
    </Workspace>
  );
}
