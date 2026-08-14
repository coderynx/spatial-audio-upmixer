import * as React from "react";
import { DB_TICKS, MULTI_CHANNEL_YELLOW_ZONE_DB, STRIP_METER_PALETTE, YELLOW_ZONE_DB, createMeterState, dbToY, levelToDb, zoneColor } from "@/lib/meterScale";
import { cn } from "@/lib/utils";

/** Logic Pro for iPad's horizontal track-volume fader: a dark pill track and
 * a round knob for the value, distinct from `Fader` (`components/ui/
 * fader.tsx`), which renders the desktop-Logic vertical channel-strip fader
 * (flat plate cap, no glow) — the app's own reference, §1's "Apple pro-app
 * idiom used by Logic Pro ... for iPad," draws on both: the vertical
 * flat-plate cap for a mixer channel strip, this control for a per-track
 * volume inline in a row. The knob is the same themed `bg-secondary
 * border-border` plate `Fader`/`Pot` use for their own caps (see the knob's
 * own comment below) rather than a literal-colour cap, so it stays legible
 * in both themes instead of reading as a light-mode control stranded on a
 * dark bar. Same interaction contract as `Fader`/`Pot`: pointer drag,
 * arrow/page/Home/End keys, double-click to reset, wheel gated on focus.
 *
 * Two fill modes, matching what iPad Logic itself draws in each context:
 * - No `meterSource`: a single glowing bar filled to the knob's position
 *   (the value itself, nothing else) — reused `success` token, no new
 *   literal colour.
 * - With `meterSource`: one thin live-level bar per channel, independent of
 *   the knob — the knob still carries the gain value, the bars carry what is
 *   actually playing right now. This is what Logic draws per-track in a
 *   timeline/mixer row, and is deliberately *not* wired to the knob's
 *   position: a fader set loud with silent audio shows empty bars, exactly
 *   like a real level meter. Shares the app's one dB scale (`meterScale.ts`)
 *   — `levelToDb`, `dbToY`, `zoneColor`, `DB_TICKS` — rather than deriving a
 *   second one, though it skips the peak-hold tick the vertical `StripMeter`
 *   draws: at this size a tick would be visual noise, and Logic's own
 *   per-track bar doesn't show one either. */

function quantize(value: number, min: number, max: number, step: number) {
  const stepped = min + Math.round((value - min) / step) * step;
  return Math.min(max, Math.max(min, Number(stepped.toFixed(6))));
}

const METER_BAR_GAP = 2;
const METER_SETTLE_FRAMES = 40;
/** Gap between the pill's own edge and the content inside it (the meter
 * bars, or the plain fill) — the pill itself spans the full control height,
 * not a thin centred line, matching iPad Logic's own track fader. */
const PILL_INSET = 3;

export type HorizontalFaderProps = {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  /** Double-click target. */
  onReset?: () => void;
  /** Accessible value text — pass the formatted readout. */
  valueText: string;
  disabled?: boolean;
  /** Knob diameter in px — also sets the pill's own height, since the pill
   * spans the full control height. */
  knobSize?: number;
  /** Number of live-level bars to draw (1 for mono, 2 for stereo — capped at
   * 2 by callers, same convention as `StripMeter`). Omit for the plain
   * fill-to-value bar. */
  meterChannels?: number;
  /** Per-channel RMS getter, polled in this control's own rAF loop — never
   * passed as a re-rendering prop, same reasoning as every other meter in
   * the app (`useStripMeterLoop`, `ChannelMeters`). */
  meterSource?: () => { rms: number }[];
  /** Whether the meter's source is live right now (playback). Gates the rAF
   * loop the same way `active` does on every other meter. */
  meterActive?: boolean;
  className?: string;
  style?: React.CSSProperties;
};

export function HorizontalFader({
  label,
  value,
  min,
  max,
  step,
  onChange,
  onReset,
  valueText,
  disabled = false,
  knobSize = 16,
  meterChannels,
  meterSource,
  meterActive = false,
  className,
  style,
}: HorizontalFaderProps) {
  const ref = React.useRef<HTMLDivElement>(null);
  const trackRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
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

  // Gated on focus read from the document, not component state, so a
  // scrolling ancestor (a timeline row list) never has an incidental
  // hover-scroll swallowed by an unfocused fader — same rule as `Fader`/`Pot`.
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

  // Own rAF loop, own smoothing state, own canvas — "every strip owns its
  // meter loop" (design spec §6.4) applies just as much to a control this
  // small: a rack of these needs no shared registration to paint correctly.
  const sourceRef = React.useRef(meterSource);
  sourceRef.current = meterSource;
  const activeRef = React.useRef(meterActive);
  activeRef.current = meterActive;
  // Outlives the effect below (unlike a `const` inside it), so pausing —
  // which changes `meterActive` and restarts the effect — doesn't forget the
  // eased level and make the bar snap to zero instead of decaying into it.
  const meterStateRef = React.useRef(createMeterState());
  React.useEffect(() => {
    if (!meterSource || !meterChannels) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const meterState = meterStateRef.current;
    let lastTime: number | null = null;
    let idle = 0;
    let frame: number;

    const draw = (time: number) => {
      const deltaSec = lastTime === null ? 0 : Math.min(0.25, (time - lastTime) / 1000);
      lastTime = time;
      const ratio = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (width <= 0 || height <= 0) { frame = window.requestAnimationFrame(draw); return; }
      if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
      }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const levels = sourceRef.current?.() ?? [];
      const count = Math.max(1, meterChannels);
      const barHeight = count >= 2 ? (height - METER_BAR_GAP) / 2 : height * 0.6;
      // A single-channel meter (a mono stem) keeps the finer, earlier
      // single-channel floor; two or more channels together get the later
      // multi-channel floor — same split `StripMeter`/`ChannelMeters` apply.
      const yellowZoneDb = count >= 2 ? MULTI_CHANNEL_YELLOW_ZONE_DB : YELLOW_ZONE_DB;
      let settled = true;
      for (let channel = 0; channel < count; channel += 1) {
        const target = levels[channel]?.rms ?? 0;
        const eased = meterState.smoothLevel(String(channel), target, deltaSec);
        if (Math.abs(eased - target) > 0.002) settled = false;
        const db = levelToDb(eased);
        // `dbToY` is a plain 1D interpolator regardless of the units its
        // `top`/`bottom` params carry — passing 1/0 turns it into a 0..1
        // fraction along the app's one shared, non-linear dB scale instead
        // of a screen position, so this bar reads the same "how loud" as
        // every vertical meter without re-deriving the curve.
        const levelFraction = dbToY(db, 1, 0, DB_TICKS);
        // True silence (idle, nothing playing) must draw nothing — a floor
        // of `Math.max(1, …)` here would leave a permanent sliver sitting at
        // the pill's left edge even at rest, since `width * 0` still got
        // bumped up to 1px. The 1px floor only applies once there is an
        // actual (if very quiet) signal to show.
        if (levelFraction > 0) {
          const barY = count >= 2 ? channel * (barHeight + METER_BAR_GAP) : (height - barHeight) / 2;
          const barWidth = Math.max(1, width * levelFraction);
          // Green-to-yellow, matching Logic's own mixer-channel-strip meter
          // (`STRIP_METER_PALETTE`) rather than the Level Meter's blue
          // (`ChannelMeters`' palette) — this control is a channel strip's
          // meter, just laid out horizontally instead of vertically.
          ctx.fillStyle = zoneColor(db, STRIP_METER_PALETTE, yellowZoneDb);
          ctx.beginPath();
          ctx.roundRect(0, barY, barWidth, barHeight, barHeight / 2);
          ctx.fill();
        }
      }

      idle = activeRef.current || !settled ? 0 : idle + 1;
      if (idle > METER_SETTLE_FRAMES) return;
      frame = window.requestAnimationFrame(draw);
    };
    frame = window.requestAnimationFrame(draw);
    return () => window.cancelAnimationFrame(frame);
  }, [meterActive, meterChannels, meterSource]);

  // The knob centre travels between the track's two ends, so usable travel is
  // the track width minus the knob — mapping against the raw width would put
  // min/max half a knob outside the visible track, same reasoning as
  // `Fader`'s vertical travel math.
  const valueForClientX = (clientX: number) => {
    const track = trackRef.current;
    if (!track) return value;
    const rect = track.getBoundingClientRect();
    const travel = Math.max(1, rect.width - knobSize);
    const offset = clientX - rect.left - knobSize / 2;
    return min + Math.min(1, Math.max(0, offset / travel)) * span;
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (disabled || event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragging.current = true;
    ref.current?.focus();
    commit(valueForClientX(event.clientX));
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current || disabled) return;
    commit(valueForClientX(event.clientX));
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
      ArrowRight: step,
      ArrowUp: step,
      ArrowLeft: -step,
      ArrowDown: -step,
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

  const showMeter = Boolean(meterSource && meterChannels);

  return (
    <div
      ref={ref}
      role="slider"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-valuetext={valueText}
      aria-orientation="horizontal"
      aria-disabled={disabled || undefined}
      tabIndex={disabled ? -1 : 0}
      title={`${label}: ${valueText}`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={disabled ? undefined : onReset}
      onKeyDown={handleKeyDown}
      style={{ height: knobSize, ...style }}
      className={cn(
        "relative shrink-0 touch-none select-none outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-1 focus-visible:ring-offset-card",
        disabled ? "pointer-events-none opacity-40" : "cursor-ew-resize",
        className,
      )}
    >
      <div ref={trackRef} className="relative h-full w-full">
        {/* Pill: spans the full control height, same base the app's ordinary
            `Slider` track uses so this reads as a sibling control. The
            meter/fill sits inset from its edge by `PILL_INSET`, not flush —
            iPad Logic's own track fader leaves that margin rather than
            filling the pill edge-to-edge. */}
        <span aria-hidden="true" className="absolute inset-0 rounded-full bg-secondary" />
        {showMeter ? (
          <canvas
            ref={canvasRef}
            aria-hidden="true"
            className="pointer-events-none absolute rounded-full"
            // Explicit width/height, not right/bottom insets — a <canvas> is
            // a replaced element, and Chromium mis-sizes an absolutely
            // positioned replaced element that has all four inset sides set
            // but no explicit size (falls back to a ~16.7M px degenerate
            // box). left/top + calc() width/height sidesteps that entirely.
            style={{
              left: PILL_INSET,
              top: PILL_INSET,
              width: `calc(100% - ${PILL_INSET * 2}px)`,
              height: `calc(100% - ${PILL_INSET * 2}px)`,
            }}
          />
        ) : (
          // Fill: the glowing green Logic-iPad draws for a track's own
          // volume, reusing the themed `success` token rather than a new
          // literal colour. Its right edge still lands on the knob's centre
          // (as before), just starting from the inset left edge instead of 0.
          <span
            aria-hidden="true"
            className="absolute rounded-full bg-success shadow-[0_0_6px_hsl(var(--success)/0.7)]"
            style={{
              left: PILL_INSET,
              top: PILL_INSET,
              bottom: PILL_INSET,
              width: `calc(${knobSize / 2 - PILL_INSET}px + (100% - ${knobSize}px) * ${fraction})`,
            }}
          />
        )}
        {/* Knob: a translucent grey disc, not an opaque plate — at this size
            (16-20px, inline in a row) an opaque cap the same secondary tone
            as the pill hid the meter bars directly beneath it, the one part
            of the level a knob sitting mid-travel would otherwise cover.
            `bg-foreground/35` lets the live bar/fill still read through the
            knob rather than blocking it. No inner grip line — at this size,
            translucent over a lit meter bar, it read as a stray hairline
            rather than a grip. */}
        <span
          aria-hidden="true"
          className="absolute top-1/2 -translate-y-1/2 rounded-full border border-foreground/25 bg-foreground/35 shadow-[0_1px_3px_rgba(0,0,0,0.35)]"
          style={{
            left: `calc((100% - ${knobSize}px) * ${fraction})`,
            width: knobSize,
            height: knobSize,
          }}
        />
      </div>
    </div>
  );
}
