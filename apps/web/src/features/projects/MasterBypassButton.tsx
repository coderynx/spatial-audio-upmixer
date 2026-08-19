import { Power, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// A/B toggles for the mastering chain — see monitorMastering
// (masterPreview.ts). Same icon-button idiom as Transport's loop button:
// warning variant when engaged, ghost otherwise. Both sides are monitored at
// matched loudness (docs/contracts/preview_export_parity.md §3, P4).
function BypassButton({
  bypassed, label, title, icon, onToggle,
}: {
  bypassed: boolean;
  label: string;
  title: string;
  icon: React.ReactNode;
  onToggle: () => void;
}) {
  return (
    <Button
      variant={bypassed ? "warning" : "ghost"}
      size="icon"
      className={cn("h-8 w-8 [&_svg]:size-4", !bypassed && "text-foreground hover:bg-accent hover:text-foreground")}
      aria-label={label}
      aria-pressed={bypassed}
      title={title}
      onClick={onToggle}
    >
      {icon}
    </Button>
  );
}

export function MasterBypassButton({ bypassed, onToggle }: { bypassed: boolean; onToggle: () => void }) {
  return (
    <BypassButton
      bypassed={bypassed}
      label="Bypass master chain"
      title="Bypass master chain (B)"
      icon={<Power />}
      onToggle={onToggle}
    />
  );
}

export function MatchBypassButton({
  bypassed, disabled, onToggle,
}: {
  bypassed: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  if (disabled) return null;
  return (
    <BypassButton
      bypassed={bypassed}
      label="Bypass reference match"
      title="Bypass reference match, at matched loudness"
      icon={<SlidersHorizontal />}
      onToggle={onToggle}
    />
  );
}
