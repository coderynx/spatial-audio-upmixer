import * as React from "react";
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
  fill = false,
  activeClassName,
  activeTextClassName,
  slideIndicator = false,
}: {
  segments: readonly Segment<T>[];
  value: T;
  onChange: (value: T) => void;
  size?: "default" | "sm";
  "aria-label"?: string;
  className?: string;
  /** Stretch to the parent's height instead of the fixed `h-6`/`h-7`. */
  fill?: boolean;
  /** Override the default `bg-card` raised-pill active state (e.g. a primary fill). */
  activeClassName?: string;
  /** Active segment's text color, used only with `slideIndicator` — the pill
   * carries `activeClassName`'s background, the button carries this. */
  activeTextClassName?: string;
  /** Animate the active state as one pill sliding between segments instead
   * of each segment cross-fading its own background in place. Opt-in: other
   * `SegmentedControl`s (settings toggle, list/grid switches) keep the
   * cross-fade. When `value` matches no segment (e.g. a sibling control took
   * over selection), the pill fades out in place rather than sliding. */
  slideIndicator?: boolean;
}) {
  const buttonRefs = React.useRef(new Map<string, HTMLButtonElement>());
  const [indicator, setIndicator] = React.useState({ left: 0, width: 0, top: 0, height: 0, visible: false });

  const measure = React.useCallback(() => {
    const active = buttonRefs.current.get(value);
    setIndicator((current) =>
      active
        ? { left: active.offsetLeft, width: active.offsetWidth, top: active.offsetTop, height: active.offsetHeight, visible: true }
        : { ...current, visible: false },
    );
  }, [value]);

  React.useLayoutEffect(() => {
    if (slideIndicator) measure();
  }, [slideIndicator, measure, segments]);

  React.useEffect(() => {
    if (!slideIndicator || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    buttonRefs.current.forEach((button) => observer.observe(button));
    return () => observer.disconnect();
  }, [slideIndicator, measure, segments]);

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        "relative inline-flex shrink-0 items-center gap-0.5 rounded-md bg-muted p-0.5",
        fill ? "h-full" : size === "sm" ? "h-6" : "h-7",
        className,
      )}
    >
      {slideIndicator && (
        <span
          aria-hidden="true"
          className={cn(
            "absolute rounded-[5px] transition-all duration-200 ease-out",
            activeClassName ?? "bg-card shadow-sm",
            indicator.visible ? "opacity-100" : "opacity-0",
          )}
          style={{ left: indicator.left, width: indicator.width, top: indicator.top, height: indicator.height }}
        />
      )}
      {segments.map((segment) => {
        const Icon = segment.icon;
        const active = segment.value === value;
        return (
          <button
            key={segment.value}
            ref={(el) => {
              if (el) buttonRefs.current.set(segment.value, el);
              else buttonRefs.current.delete(segment.value);
            }}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(segment.value)}
            className={cn(
              "relative z-10 flex h-full items-center gap-1.5 whitespace-nowrap rounded-[5px] px-2.5 font-medium transition-colors",
              size === "sm" ? "text-[11px]" : "text-[13px]",
              // Must come after the text-size class: tailwind-merge groups
              // arbitrary `text-[…]` font sizes with `leading-*` (real
              // Tailwind's named sizes pair a default line-height), so
              // whichever one is later in the argument list wins the merge.
              "leading-none",
              active
                ? slideIndicator
                  ? (activeTextClassName ?? "text-foreground")
                  : (activeClassName ?? "bg-card text-foreground shadow-sm")
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
