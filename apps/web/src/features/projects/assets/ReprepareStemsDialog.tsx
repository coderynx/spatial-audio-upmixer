import * as React from "react";
import { RotateCcw } from "lucide-react";
import type { Configuration, Project } from "@/api";
import { ToggleField } from "@/components/forms/fields";
import { StemSelectorGrid } from "@/components/stems/StemSelectorGrid";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { defaultManifest, fallbackStems } from "@/lib/manifest";
import { normalizeStemHierarchy } from "@/lib/stemHierarchy";

export type ReprepareSettings = {
  stems: string[];
  stemBleedReduction: boolean;
};

export function ReprepareStemsDialog({
  open,
  project,
  configuration,
  onOpenChange,
  onReprepare,
}: {
  open: boolean;
  project: Project;
  configuration: Configuration | null;
  onOpenChange: (open: boolean) => void;
  onReprepare: (settings: ReprepareSettings) => Promise<void>;
}) {
  const [stems, setStems] = React.useState(project.requested_stems);
  const [stemBleedReduction, setStemBleedReduction] = React.useState(defaultManifest.engine.stem_bleed_reduction);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const availableStems = configuration?.choices.stems || fallbackStems;

  React.useEffect(() => {
    if (!open) return;
    const engine = project.manifest.engine as Record<string, unknown> | undefined;
    setStems(normalizeStemHierarchy(project.requested_stems));
    setStemBleedReduction(
      typeof engine?.stem_bleed_reduction === "boolean"
        ? engine.stem_bleed_reduction
        : defaultManifest.engine.stem_bleed_reduction,
    );
    setError(null);
  }, [open, project]);

  async function reprepare() {
    if (!stems.length) return;
    setBusy(true);
    setError(null);
    try {
      await onReprepare({ stems, stemBleedReduction });
      onOpenChange(false);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(680px,92vw)]">
        <DialogHeader className="border-b px-3 py-3 pr-10">
          <DialogTitle className="text-[13px]">Re-prepare stems</DialogTitle>
          <DialogDescription className="text-[11px]">
            Choose the stems and cleanup to apply to every track.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 p-3">
          <StemSelectorGrid available={availableStems} selected={stems} onChange={setStems} />
          <details className="rounded-md border">
            <summary className="cursor-pointer px-3 py-2 text-[13px] font-medium">DSP stem cleanup</summary>
            <div className="border-t p-3">
              <ToggleField
                label="DSP stem cleanup"
                description="Apply cleanup during separation."
                checked={stemBleedReduction}
                onChange={setStemBleedReduction}
              />
            </div>
          </details>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <div className="flex items-center justify-end gap-2 border-t p-3">
          <Button variant="ghost" disabled={busy} onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={busy || !stems.length} onClick={() => void reprepare()}>
            <RotateCcw />
            {busy ? "Starting…" : "Re-prepare stems"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
