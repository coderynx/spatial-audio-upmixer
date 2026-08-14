import * as React from "react";
import { Pause, Play } from "lucide-react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function SeparationToggle({ collapsed }: { collapsed?: boolean }) {
  const [paused, setPaused] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const refresh = React.useCallback(async () => {
    try {
      setPaused((await api.getSeparationState()).paused);
    } catch {
      // A transient fetch failure just leaves the last known state on screen.
    }
  }, []);
  React.useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);
  const toggle = async () => {
    setBusy(true);
    try {
      const next = paused ? await api.resumeSeparation() : await api.pauseSeparation();
      setPaused(next.paused);
    } finally {
      setBusy(false);
    }
  };
  const label = paused ? "Resume background stem separation" : "Pause background stem separation";
  return (
    <Button
      variant="ghost"
      size={collapsed ? "icon" : "sm"}
      aria-label={label}
      aria-pressed={paused}
      title={label}
      disabled={busy}
      onClick={() => void toggle()}
      className={cn(paused && "text-primary", !collapsed && "gap-2 px-2")}
    >
      {paused ? <Play /> : <Pause />}
      {!collapsed && <span className="truncate">{paused ? "Resume separation" : "Pause separation"}</span>}
    </Button>
  );
}
