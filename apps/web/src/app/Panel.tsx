import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cn("flex min-h-0 flex-col overflow-hidden rounded-lg border bg-card", className)}>{children}</section>;
}

export function PanelHeader({
  title,
  actions,
  className,
}: {
  title: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("flex h-8 shrink-0 items-center justify-between gap-2 border-b px-3", className)}>
      <span className="min-w-0 truncate text-[11px] font-semibold uppercase tracking-[.08em] text-muted-foreground">
        {title}
      </span>
      {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
    </header>
  );
}

export function PanelBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("min-h-0 flex-1 overflow-y-auto p-3", className)}>{children}</div>;
}
