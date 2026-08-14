import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type Metric = {
  label: string;
  value: string | number;
  note?: string;
  icon?: LucideIcon;
};

/** KPI cells sharing one bordered row (horizontal) or stacked in a rail
 * (vertical) — never free-floating cards with gutters between them. */
export function MetricStrip({
  metrics,
  orientation = "horizontal",
  className,
}: {
  metrics: Metric[];
  orientation?: "horizontal" | "vertical";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid overflow-hidden rounded-lg border bg-card",
        orientation === "horizontal"
          ? "grid-flow-col auto-cols-fr divide-x"
          : "grid-cols-1 divide-y",
        className,
      )}
    >
      {metrics.map((metric) => (
        <div key={metric.label} className="flex items-center justify-between gap-3 px-3 py-2">
          <div className="min-w-0">
            <p className="truncate text-[11px] uppercase tracking-[.06em] text-muted-foreground">{metric.label}</p>
            <p className="mt-0.5 text-lg font-semibold leading-none tabular-nums">{metric.value}</p>
            {metric.note && <p className="mt-1 truncate text-[11px] text-muted-foreground">{metric.note}</p>}
          </div>
          {metric.icon && <metric.icon className="h-4 w-4 shrink-0 text-muted-foreground" />}
        </div>
      ))}
    </div>
  );
}
