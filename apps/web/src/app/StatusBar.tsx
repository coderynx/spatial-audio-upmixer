import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Bottom strip carrying always-on counts and machine state, so the last row
 * of the viewport is information rather than padding. */
export function StatusBar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex h-8 shrink-0 items-center gap-3 border-t bg-card px-3 text-[11px] text-muted-foreground", className)}>
      {children}
    </div>
  );
}

export function StatusCell({
  label,
  value,
  className,
}: {
  label: string;
  value: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("flex shrink-0 items-center gap-1.5", className)}>
      <span className="uppercase tracking-[.06em]">{label}</span>
      <span className="font-medium tabular-nums text-foreground">{value}</span>
    </span>
  );
}

export function StatusSeparator() {
  return <span className="h-3 w-px shrink-0 bg-border" aria-hidden="true" />;
}

export function StatusSpacer() {
  return <span className="flex-1" aria-hidden="true" />;
}
