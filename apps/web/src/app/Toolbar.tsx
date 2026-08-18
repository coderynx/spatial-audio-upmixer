import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Page-scoped control row directly under the global top bar. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex h-11 shrink-0 items-center gap-2 border-b bg-card px-3", className)}>
      {children}
    </div>
  );
}

export function ToolbarSeparator() {
  return <div className="h-5 w-px shrink-0 bg-border" aria-hidden="true" />;
}

export function ToolbarSpacer() {
  return <div className="flex-1" aria-hidden="true" />;
}
