import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type Segment<T extends string> = {
  value: T;
  label: string;
  icon?: LucideIcon;
};

/** Apple segmented control — one recessed track, the active segment raised.
 * Used for the project workflow steps and the list/grid view switches. */
export function SegmentedControl<T extends string>({
  segments,
  value,
  onChange,
  size = "default",
  "aria-label": ariaLabel,
  className,
}: {
  segments: readonly Segment<T>[];
  value: T;
  onChange: (value: T) => void;
  size?: "default" | "sm";
  "aria-label"?: string;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex shrink-0 items-center gap-0.5 rounded-md bg-muted p-0.5",
        size === "sm" ? "h-6" : "h-7",
        className,
      )}
    >
      {segments.map((segment) => {
        const Icon = segment.icon;
        const active = segment.value === value;
        return (
          <button
            key={segment.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(segment.value)}
            className={cn(
              "flex h-full items-center gap-1.5 whitespace-nowrap rounded-[5px] px-2.5 font-medium transition-colors",
              size === "sm" ? "text-[11px]" : "text-[13px]",
              // Must come after the text-size class: tailwind-merge groups
              // arbitrary `text-[…]` font sizes with `leading-*` (real
              // Tailwind's named sizes pair a default line-height), so
              // whichever one is later in the argument list wins the merge.
              "leading-none",
              active
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {Icon && <Icon className={cn("shrink-0", size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5")} />}
            <span className="leading-none">{segment.label}</span>
          </button>
        );
      })}
    </div>
  );
}
