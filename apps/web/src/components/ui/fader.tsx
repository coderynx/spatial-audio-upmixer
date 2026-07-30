import * as React from "react";
import { canvasTheme } from "@/lib/canvasTheme";
import { cn } from "@/lib/utils";

/** Vertical channel fader: a hairline tick ladder in a left gutter, a thin
 * recessed travel slot, and a cap drawn flat — the same `fill-secondary
 * stroke-border` plate `Pot` uses for its knob, with a centre grip line
 * standing in for `Pot`'s pointer, just rotated onto the vertical axis a
 * fader travels. No gradient or drop shadow: the two controls are one
 * visual family, not a skeuomorphic fader next to a flat knob.
 *
 * `Slider` stays the control for a level read off a horizontal scale in a
 * panel or inspector; a fader is the same kind of value laid out the way a
 * console lays it out, so a rack of them can be compared at a glance. The
 * interaction contract (drag, arrows, page, Home/End, double-click to reset,
 * focus-gated wheel) deliberately matches `Pot`.
 *
 * The slot and ticks stay fixed instrument colours from `canvasTheme` — a
 * console's travel slot is a physical groove with its own finish, and Logic
 * keeps it identical in both appearances. The cap is the part a hand grips,
 * so it takes the same theme tokens as every other control's grip. */

const CAP_HEIGHT = 34;
const CAP_WIDTH = 22;
const SLOT_WIDTH = 3;
/** Left gutter the tick ladder occupies, measured from the control's edge. */
const TICK_GUTTER = 10;

function quantize(value: number, min: number, max: number, step: number) {
  const stepped = min + Math.round((value - min) / step) * step;
  return Math.min(max, Math.max(min, Number(stepped.toFixed(6))));
}

export type FaderProps = {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  /** Double-click target. The unity/rest value for this fader. */
  onReset?: () => void;
  /** Value the travel is marked at, e.g. 0 dB unity. Omit for no detent. */
  detent?: number;
  /** Accessible value text — pass the formatted readout, e.g. "-6.0 dB". */
  valueText: string;
  /** Scale marks in the left gutter, in value units. Logic prints an
   * unlabelled ladder here; the numerals belong to the meter beside it. */
  ticks?: number[];
  disabled?: boolean;
  className?: string;
  style?: React.CSSProperties;
};

export function Fader({
  label,
  value,
  min,
  max,
  step,
  onChange,
  onReset,
  detent,
  valueText,
  ticks,
  disabled = false,
  className,
  style,
}: FaderProps) {
  const ref = React.useRef<HTMLDivElement>(null);
  const trackRef = React.useRef<HTMLDivElement>(null);
  const dragging = React.useRef(false);

  const span = max - min || 1;
  const fraction = Math.min(1, Math.max(0, (value - min) / span));

  const commit = React.useCallback(
    (next: number) => {
      const quantized = quantize(next, min, max, step);
      if (quantized !== value) onChange(quantized);
    },
    [max, min, onChange, step, value],
  );

  // Non-passive so it can preventDefault, and gated on focus read from the
  // document rather than component state — the strip rack scrolls, and a
  // fader that swallowed an incidental hover-scroll would be hostile. Same
  // rule as `Pot`.
  React.useEffect(() => {
    const node = ref.current;
    if (!node || disabled) return;
    const onWheel = (event: WheelEvent) => {
      if (document.activeElement !== node) return;
      event.preventDefault();
      commit(value + (event.deltaY < 0 ? step : -step));
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [commit, disabled, step, value]);

  // The cap centre travels between the two ends of the track, so the usable
  // travel is the track height minus the cap — mapping against the raw height
  // would put max and min half a cap outside the visible slot.
  const valueForClientY = (clientY: number) => {
    const track = trackRef.current;
    if (!track) return value;
    const rect = track.getBoundingClientRect();
    const travel = Math.max(1, rect.height - CAP_HEIGHT);
    const offset = clientY - rect.top - CAP_HEIGHT / 2;
    return max - (Math.min(1, Math.max(0, offset / travel))) * span;
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (disabled || event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragging.current = true;
    ref.current?.focus();
    commit(valueForClientY(event.clientY));
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current || disabled) return;
    commit(valueForClientY(event.clientY));
  };

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    dragging.current = false;
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

  // Scale marks line up with the cap's centre, not the raw track, so a tick
  // and the cap sitting on it read as the same value.
  const markAt = (mark: number) =>
    `calc(${CAP_HEIGHT / 2}px + (100% - ${CAP_HEIGHT}px) * ${1 - (mark - min) / span} - 0.5px)`;

  return (
    <div
      ref={ref}
      role="slider"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-valuetext={valueText}
      aria-orientation="vertical"
      aria-disabled={disabled || undefined}
      tabIndex={disabled ? -1 : 0}
      title={`${label}: ${valueText}`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={disabled ? undefined : onReset}
      onKeyDown={handleKeyDown}
      style={style}
      className={cn(
        "relative shrink-0 touch-none select-none rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-1 focus-visible:ring-offset-card",
        disabled ? "pointer-events-none opacity-40" : "cursor-ns-resize",
        className,
      )}
    >
      <div ref={trackRef} className="relative h-full w-full">
        {/* Tick ladder, left gutter. Unlabelled by design — Logic prints the
            numerals once, beside the meter, not twice. */}
        {ticks?.map((tick) => (
          <span
            key={tick}
            aria-hidden="true"
            className="absolute left-0 h-px"
            style={{
              top: markAt(tick),
              width: tick === detent ? TICK_GUTTER : TICK_GUTTER - 3,
              backgroundColor: tick === detent ? canvasTheme.labelStrong : canvasTheme.faderTick,
            }}
          />
        ))}
        {/* Travel slot, centred in the space to the right of the gutter. */}
        <span
          aria-hidden="true"
          className="absolute rounded-full"
          style={{
            left: `calc(${TICK_GUTTER}px + (100% - ${TICK_GUTTER}px) / 2 - ${SLOT_WIDTH / 2}px)`,
            top: CAP_HEIGHT / 2,
            bottom: CAP_HEIGHT / 2,
            width: SLOT_WIDTH,
            backgroundColor: canvasTheme.stripWell,
          }}
        />
        {detent !== undefined && !ticks?.includes(detent) && (
          <span
            aria-hidden="true"
            className="absolute left-0 h-px"
            style={{ top: markAt(detent), width: TICK_GUTTER, backgroundColor: canvasTheme.labelStrong }}
          />
        )}
        {/* Cap: the same flat plate `Pot` draws for its knob — a
            `fill-secondary stroke-border` shape with a single grip line,
            just a rectangle standing in for a circle. */}
        <span
          aria-hidden="true"
          className="absolute flex items-center justify-center rounded-[4px] border border-border bg-secondary"
          style={{
            left: `calc(${TICK_GUTTER}px + (100% - ${TICK_GUTTER}px) / 2 - ${CAP_WIDTH / 2}px)`,
            width: CAP_WIDTH,
            height: CAP_HEIGHT,
            top: `calc((100% - ${CAP_HEIGHT}px) * ${1 - fraction})`,
          }}
        >
          <span className="h-px w-3 bg-foreground" />
        </span>
      </div>
    </div>
  );
}
