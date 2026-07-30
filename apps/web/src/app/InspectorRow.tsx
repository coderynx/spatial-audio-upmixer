import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Dense label/value row, the Logic Pro parameter-list pattern. */
export function InspectorRow({
  label,
  value,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-h-7 items-center justify-between gap-3 border-b py-1 last:border-0", className)}>
      <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0 truncate text-right text-xs font-medium tabular-nums">{value}</span>
    </div>
  );
}

export function InspectorGroup({
  title,
  actions,
  children,
  className,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("border-b px-3 py-2.5 last:border-0", className)}>
      <header className="mb-1.5 flex items-center justify-between gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-[.08em] text-muted-foreground">{title}</h3>
        {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
      </header>
      {children}
    </section>
  );
}
