import * as React from "react";
import { cn } from "@/lib/utils";

/** Rotary pot for effect parameters — the knob idiom Logic Pro uses for
 * compressor and tone controls, where a grid of dials reads faster and packs
 * tighter than a stack of horizontal sliders.
 *
 * Levels and blends stay on `Slider`; a pot is for parameters you tweak by
 * feel rather than read off a scale. */

const SWEEP_DEGREES = 270;
const START_DEGREES = -135;
/** Pointer travel, in pixels, that covers the parameter's full range. */
const DRAG_RANGE_PX = 160;
const FINE_DRAG_SCALE = 0.25;

const SIZES = {
  default: { box: 48, stroke: 3, capScale: 0.62 },
  sm: { box: 36, stroke: 2.5, capScale: 0.6 },
} as const;

function polar(center: number, radius: number, degrees: number) {
  // -90 puts 0° at 12 o'clock; increasing degrees then runs clockwise, which
  // is also SVG's positive sweep direction.
  const radians = ((degrees - 90) * Math.PI) / 180;
  return [center + radius * Math.cos(radians), center + radius * Math.sin(radians)] as const;
}

function arc(center: number, radius: number, from: number, to: number) {
  const [x0, y0] = polar(center, radius, from);
  const [x1, y1] = polar(center, radius, to);
  const largeArc = Math.abs(to - from) > 180 ? 1 : 0;
  const sweep = to >= from ? 1 : 0;
  return `M ${x0} ${y0} A ${radius} ${radius} 0 ${largeArc} ${sweep} ${x1} ${y1}`;
}

function decimalsFor(step: number) {
  return step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
}

function quantize(value: number, min: number, max: number, step: number) {
  const stepped = min + Math.round((value - min) / step) * step;
  return Math.min(max, Math.max(min, Number(stepped.toFixed(6))));
}

export type PotProps = {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  /** Double-click handler. Use to restore a profile default or a reset value. */
  onReset?: () => void;
  suffix?: string;
  /** Where the value arc starts. Inferred as "center" for ranges that span
   * zero, so cut and boost read differently, and "min" otherwise. */
  origin?: "min" | "center";
  size?: keyof typeof SIZES;
  disabled?: boolean;
  /** Slightly softens the dial: the value shown belongs to something else (a
   * profile) until the user takes it over. The value arc stays fully coloured
   * either way — a knob reads as a knob at rest, not only once touched. */
  inherited?: boolean;
  /** Appended to the accessible value text while `inherited`, because opacity
   * alone conveys nothing to assistive tech. */
  inheritedHint?: string;
  className?: string;
};

export function Pot({
  label,
  value,
  min,
  max,
  step,
  onChange,
  onReset,
  suffix = "",
  origin,
  size = "default",
  disabled = false,
  inherited = false,
  inheritedHint,
  className,
}: PotProps) {
  const { box, stroke, capScale } = SIZES[size];
  const ref = React.useRef<HTMLDivElement>(null);
  const drag = React.useRef<{ startY: number; startValue: number } | null>(null);

  const span = max - min;
  const fraction = span === 0 ? 0 : (value - min) / span;
  const bipolar = (origin ?? (min < 0 && max > 0 ? "center" : "min")) === "center";
  const originFraction = bipolar ? (0 - min) / span : 0;

  const center = box / 2;
  const radius = center - stroke / 2 - 1.5;
  const capRadius = radius * capScale;
  const valueAngle = START_DEGREES + fraction * SWEEP_DEGREES;
  const originAngle = START_DEGREES + originFraction * SWEEP_DEGREES;
  const [pointerFromX, pointerFromY] = polar(center, radius * 0.4, valueAngle);
  const [pointerToX, pointerToY] = polar(center, radius * 0.85, valueAngle);

  const decimals = decimalsFor(step);
  const display = `${value.toFixed(decimals)}${suffix}`;

  const commit = React.useCallback(
    (next: number) => {
      const quantized = quantize(next, min, max, step);
      if (quantized !== value) onChange(quantized);
    },
    [max, min, onChange, step, value],
  );
  const valueRef = React.useRef(value);
  const stepRef = React.useRef(step);
  const commitRef = React.useRef(commit);
  valueRef.current = value;
  stepRef.current = step;
  commitRef.current = commit;

  // Wheel is bound natively so it can be non-passive, and only acts once the
  // pot has focus — the panels these sit in scroll, and a knob that swallowed
  // an incidental hover-scroll would be hostile. Focus is read from the
  // document rather than component state so the handler can never act on a
  // stale render's idea of where focus is.
  React.useEffect(() => {
    const node = ref.current;
    if (!node || disabled) return;
    const onWheel = (event: WheelEvent) => {
      if (document.activeElement !== node) return;
      event.preventDefault();
      commitRef.current(valueRef.current + (event.deltaY < 0 ? stepRef.current : -stepRef.current));
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [disabled]);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (disabled || event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = { startY: event.clientY, startValue: value };
    ref.current?.focus();
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (!state || disabled) return;
    const travelled = state.startY - event.clientY;
    const scale = event.shiftKey ? FINE_DRAG_SCALE : 1;
    commit(state.startValue + (travelled / DRAG_RANGE_PX) * span * scale);
  };

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    drag.current = null;
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    const jump = step * 10;
    const moves: Record<string, number> = {
      ArrowUp: step,
      ArrowRight: step,
      ArrowDown: -step,
      ArrowLeft: -step,
      PageUp: jump,
      PageDown: -jump,
    };
    if (event.key in moves) {
      event.preventDefault();
      commit(value + moves[event.key]);
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      commit(min);
    } else if (event.key === "End") {
      event.preventDefault();
      commit(max);
    }
  };

  const hint = inherited && inheritedHint ? `${display} (${inheritedHint})` : display;

  return (
    <div className={cn("flex min-w-0 flex-col items-center gap-1", className)}>
      <div
        ref={ref}
        role="slider"
        aria-label={label}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-valuetext={hint}
        aria-orientation="vertical"
        aria-disabled={disabled || undefined}
        data-disabled={disabled ? "" : undefined}
        data-inherited={inherited ? "" : undefined}
        tabIndex={disabled ? -1 : 0}
        title={hint}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={disabled ? undefined : onReset}
        onKeyDown={handleKeyDown}
        className={cn(
          "touch-none rounded-full outline-none transition-opacity focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-1 focus-visible:ring-offset-background",
          disabled ? "pointer-events-none opacity-40" : "cursor-ns-resize",
          inherited && !disabled && "opacity-80",
        )}
      >
        <svg width={box} height={box} viewBox={`0 0 ${box} ${box}`} aria-hidden="true">
          <path
            d={arc(center, radius, START_DEGREES, START_DEGREES + SWEEP_DEGREES)}
            className="stroke-border"
            strokeWidth={stroke}
            strokeLinecap="round"
            fill="none"
          />
          {Math.abs(valueAngle - originAngle) > 0.5 && (
            <path
              d={arc(center, radius, originAngle, valueAngle)}
              className="stroke-primary"
              strokeWidth={stroke}
              strokeLinecap="round"
              fill="none"
            />
          )}
          <circle
            cx={center}
            cy={center}
            r={capRadius}
            className="fill-secondary stroke-border"
            strokeWidth={1}
          />
          <line
            x1={pointerFromX}
            y1={pointerFromY}
            x2={pointerToX}
            y2={pointerToY}
            className="stroke-foreground"
            strokeWidth={2}
            strokeLinecap="round"
          />
        </svg>
      </div>
      <span className="text-[13px] font-medium leading-none tabular-nums">{display}</span>
      <span className="w-full truncate text-center text-[11px] leading-tight text-muted-foreground" title={label}>
        {label}
      </span>
    </div>
  );
}
