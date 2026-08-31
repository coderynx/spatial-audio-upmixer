import { Power } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function StemEffectTrigger({ active, label, ariaLabel, expanded, onOpen, onToggle }: {
  active: boolean;
  label: string;
  ariaLabel: string;
  expanded: boolean;
  onOpen: () => void;
  onToggle: () => void;
}) {
  return (
    <div className="flex w-full min-w-0" role="group" aria-label={`${label} effect`}>
      <Button
        variant={active ? "default" : "secondary"}
        size="sm"
        className={cn("h-6 min-w-0 flex-1 justify-center gap-1 rounded-r-none px-1 text-[10px]", !active && "bg-muted text-muted-foreground hover:bg-accent hover:text-foreground")}
        aria-haspopup="dialog"
        aria-expanded={expanded}
        aria-label={ariaLabel}
        onClick={onOpen}
      >
        <span className="truncate">{label}</span>
      </Button>
      <button
        type="button"
        aria-label={`${active ? "Disable" : "Enable"} ${label}`}
        title={`${active ? "Disable" : "Enable"} ${label}`}
        className={cn(
          "flex h-6 w-5 shrink-0 items-center justify-center rounded-r-[6px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-1 focus-visible:ring-offset-background",
          active
            ? "border-l border-primary-foreground/25 bg-primary text-primary-foreground hover:bg-primary/85"
            : "border-l border-border bg-muted text-muted-foreground hover:bg-accent hover:text-foreground",
        )}
        onClick={onToggle}
      ><Power className="h-3 w-3" /></button>
    </div>
  );
}
