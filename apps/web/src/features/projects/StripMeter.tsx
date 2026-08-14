import * as React from "react";
import { canvasTheme } from "@/lib/canvasTheme";
import {
  MULTI_CHANNEL_YELLOW_ZONE_DB,
  RED_ZONE_DB,
  STRIP_DB_TICKS,
  STRIP_METER_PALETTE,
  YELLOW_ZONE_DB,
  dbToY,
  drawMeterBar,
} from "@/lib/meterScale";
import { cn } from "@/lib/utils";

// Canvas rather than DOM: the bars repaint every frame from the audio hook's
// level refs, driven by one shared rAF loop in MixerView, so this component
// only exposes the geometry and the draw call. Appearance: web_ui_design §6.4.

export const SCALE_WIDTH = 17;
export const BAR_WIDTH = 5;
export const BAR_GAP = 2;
const BAR_RADIUS = 1.5;

/** Width a meter needs for `channels` bars plus the numeral column. */
export function stripMeterWidth(channels: number) {
  return SCALE_WIDTH + channels * BAR_WIDTH + (channels - 1) * BAR_GAP;
}

/** Paints the numeral column once per resize; the bars are painted over it
 * every frame by `drawStripMeterBars`. Kept separate so the text — by far the
 * most expensive part — is not re-rasterized 60 times a second. */
export function drawStripMeterScale(ctx: CanvasRenderingContext2D, width: number, height: number) {
  ctx.clearRect(0, 0, width, height);
  ctx.font = "500 7px system-ui, sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (const tick of STRIP_DB_TICKS) {
    const y = dbToY(tick, 0, height, STRIP_DB_TICKS);
    const clamped = Math.min(height - 3, Math.max(3, y));
    ctx.fillStyle = canvasTheme.label;
    ctx.fillText(String(Math.abs(tick)), SCALE_WIDTH - 5, clamped);
    ctx.fillStyle = canvasTheme.faderTick;
    ctx.fillRect(SCALE_WIDTH - 3, Math.round(clamped) - 0.5, 2, 1);
  }
}

export function drawStripMeterBars(
  ctx: CanvasRenderingContext2D,
  height: number,
  bars: { currentDb: number; peakDb: number; clipped: boolean }[],
  muted: boolean,
) {
  // A mono stem's strip (one bar) represents a single channel in isolation
  // and keeps the finer single-channel floor; a stereo/master strip (two
  // bars) represents multiple channels together and gets the later one —
  // same split `ChannelMeters` and `HorizontalFader` apply.
  const yellowZoneDb = bars.length >= 2 ? MULTI_CHANNEL_YELLOW_ZONE_DB : YELLOW_ZONE_DB;
  const redBottomY = dbToY(RED_ZONE_DB, 0, height, STRIP_DB_TICKS);
  const yellowBottomY = dbToY(yellowZoneDb, 0, height, STRIP_DB_TICKS);
  bars.forEach((bar, index) => {
    drawMeterBar(
      ctx, SCALE_WIDTH + index * (BAR_WIDTH + BAR_GAP), BAR_WIDTH, 0, height,
      redBottomY, yellowBottomY, bar.currentDb, bar.peakDb, muted, bar.clipped,
      {
        well: canvasTheme.stripWell,
        palette: STRIP_METER_PALETTE,
        ticks: STRIP_DB_TICKS,
        radius: BAR_RADIUS,
        yellowZoneDb,
      },
    );
  });
}

/** Two stacked canvases: a static scale and the live bars over it. */
export const StripMeter = React.forwardRef<HTMLCanvasElement, {
  channels: number;
  className?: string;
}>(function StripMeter({ channels, className }, barsRef) {
  const scaleRef = React.useRef<HTMLCanvasElement>(null);
  const width = stripMeterWidth(channels);

  React.useEffect(() => {
    const canvas = scaleRef.current;
    if (!canvas) return;
    const paint = () => {
      const ratio = window.devicePixelRatio || 1;
      const height = canvas.clientHeight;
      if (height <= 0) return;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      drawStripMeterScale(ctx, width, height);
    };
    paint();
    const observer = new ResizeObserver(paint);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [width]);

  return (
    <div className={cn("relative h-full shrink-0", className)} style={{ width }}>
      <canvas ref={scaleRef} aria-hidden="true" className="absolute inset-0 h-full w-full" />
      <canvas ref={barsRef} aria-hidden="true" className="absolute inset-0 h-full w-full" />
    </div>
  );
});
