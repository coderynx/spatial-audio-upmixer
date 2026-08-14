import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Compact empty state that fills its panel instead of stacking a hero block
 * in the middle of an otherwise blank page. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("grid h-full min-h-40 place-items-center p-4 text-center", className)}>
      <div className="max-w-xs">
        <Icon className="mx-auto h-6 w-6 text-muted-foreground" />
        <p className="mt-2 text-[13px] font-medium">{title}</p>
        {description && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p>}
        {action && <div className="mt-3 flex justify-center">{action}</div>}
      </div>
    </div>
  );
}
