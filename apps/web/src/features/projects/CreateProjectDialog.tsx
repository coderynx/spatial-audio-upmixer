import * as React from "react";
import { Play } from "lucide-react";
import { api, type Project } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

/** Empty-project creation: name and notes only. Assets, stems, and format
 * are chosen afterwards in the project's own Assets tab — see
 * `features/projects/assets/AssetsTab.tsx` — rather than up front here,
 * which is what let a project only ever start from one upload. */
export function CreateProjectDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (project: Project) => void;
}) {
  const [name, setName] = React.useState("New project");
  const [notes, setNotes] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setName("New project");
      setNotes("");
      setError(null);
    }
  }, [open]);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject({ name: name.trim(), notes: notes.trim() || null });
      onOpenChange(false);
      onCreated(project);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(480px,92vw)]">
        <div className="p-4">
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
            <DialogDescription>
              Name it now — you'll upload tracks and choose stems, sample rate, and channel layout on the next screen.
            </DialogDescription>
          </DialogHeader>
          <div className="mt-4 space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="new-project-name">Project name</Label>
              <Input
                id="new-project-name"
                value={name}
                autoFocus
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Enter") void create(); }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-project-notes">Notes</Label>
              <textarea
                id="new-project-notes"
                rows={3}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Optional — session notes, references, deadlines"
                className={cn(
                  "flex w-full resize-none rounded-md border border-input bg-secondary px-2.5 py-1.5 text-[13px]",
                  "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                )}
              />
            </div>
            {error && <p className="text-xs text-destructive">{error}</p>}
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-end gap-2 border-t bg-card p-3">
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={() => void create()} disabled={busy || !name.trim()}>
            <Play />
            {busy ? "Creating…" : "Create project"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
