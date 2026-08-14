import { Power } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// A/B toggle for the mastering chain — see monitorMastering (masterPreview.ts).
// Same icon-button idiom as Transport's loop button: warning variant when
// engaged, ghost otherwise.
export function MasterBypassButton({ bypassed, onToggle }: { bypassed: boolean; onToggle: () => void }) {
  return (
    <Button
      variant={bypassed ? "warning" : "ghost"}
      size="icon"
      className={cn("h-8 w-8 [&_svg]:size-4", !bypassed && "text-foreground hover:bg-accent hover:text-foreground")}
      aria-label="Bypass master chain"
      aria-pressed={bypassed}
      title="Bypass master chain (B)"
      onClick={onToggle}
    >
      <Power />
    </Button>
  );
}
