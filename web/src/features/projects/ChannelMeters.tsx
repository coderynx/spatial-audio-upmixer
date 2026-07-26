import * as React from "react";
import { speakerDisplayLabel } from "@/lib/spatial";
import type { OutputMode } from "./useStemPreview";

// Vertical dB-scale level meters beside the Haze view: one bar per channel of
// the project's selected speaker layout (in `channels` order, LFE last),
// plus a separated "Headphones" group showing the L/R signal actually
// reaching the binaural preview output. Reads `channelLevels`/
// `headphoneLevels` refs in its own rAF loop rather than subscribing to
// React state, same pattern as HazeView/ElevationView reading `stemSpectrum`.

export type ChannelMetersProps = {
  channels: string[];
  channelLevels: React.MutableRefObject<Map<string, number>>;
  headphoneLevels: React.MutableRefObject<{ left: number; right: number }>;
  speakerEnabled: Record<string, boolean>;
  onToggleSpeaker?: (channel: string) => void;
  // Which of the three preview output modes is live — controls the trailing
  // group: two bars with a headphone glyph for binaural, two bars labeled
  // L/R for stereo, or nothing (the per-layout bars already show every
  // discrete channel) for native.
  outputMode: OutputMode;
  // True while preview audio is live-updating `channelLevels`/
  // `headphoneLevels` (i.e. `preview.playing`). On pause/stop those refs are
  // cleared to zero (see useStemPreview.ts's `stopSources`), and this
  // component eases its displayed bars down toward that zero (`smoothLevel`)
  // rather than snapping instantly, so the meters dissolve out on the same
  // timing as HazeView/ElevationView. While inactive, the draw loop keeps
  // running only until the bars + peak markers settle (see `SETTLE_FRAMES`
  // below), then stops.
  active: boolean;
  className?: string;
};

// Consecutive idle frames (bars unchanging) required before the draw loop
// stops scheduling itself. Not time-critical — just enough to let the peak
// decay animation visibly settle rather than freeze mid-motion.
const SETTLE_FRAMES = 20;
const SETTLE_EPSILON_DB = 0.05;
// Same exponential rate HazeView/ElevationView smooth their per-stem level
// toward (see those files' `previous.level + (level - previous.level) *
// Math.min(1, delta * 8)`) — keeps the meters' play/stop ramp visually in
// sync with the haze blobs' and elevation dots' dissolve in/out.
const LEVEL_SMOOTHING_RATE = 8;

type HitTarget = { channel: string; x: number; width: number };

// Non-linear scale (equal pixel spacing per tick regardless of dB gap) —
// compresses the quiet end so a meter's day-to-day range (roughly -20..0dB)
// gets most of the vertical resolution, matching common DAW meter scales.
const DB_TICKS = [0, -5, -10, -15, -20, -30, -45, -60];
const RED_ZONE_DB = -5;
const YELLOW_ZONE_DB = -20;
const CLIP_DB = -1;
const PEAK_DECAY_DB_PER_SEC = 14;

function levelToDb(level: number): number {
  const db = level > 0.0001 ? 20 * Math.log10(level) : -60;
  return Math.max(-60, Math.min(0, db));
}

function dbToY(db: number, top: number, bottom: number): number {
  const clamped = Math.max(-60, Math.min(0, db));
  for (let i = 0; i < DB_TICKS.length - 1; i++) {
    const hi = DB_TICKS[i];
    const lo = DB_TICKS[i + 1];
    if (clamped <= hi && clamped >= lo) {
      const t = (hi - clamped) / (hi - lo);
      const segmentFraction = (i + t) / (DB_TICKS.length - 1);
      return top + segmentFraction * (bottom - top);
    }
  }
  return bottom;
}

function drawLabel(ctx: CanvasRenderingContext2D, text: string, centerX: number, topY: number, color: string, maxWidth: number) {
  ctx.fillStyle = color;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  let fontSize = 12;
  ctx.font = `700 ${fontSize}px system-ui, sans-serif`;
  while (fontSize > 8 && ctx.measureText(text).width > maxWidth) {
    fontSize -= 1;
    ctx.font = `700 ${fontSize}px system-ui, sans-serif`;
  }
  ctx.fillText(text, centerX, topY);
}

// Small canvas-path headphone glyph (headband arc + two ear cups), drawn
// centered under the binaural group in place of "L"/"R" text — a canvas
// path keeps it trivially aligned to the bars' coordinates rather than
// syncing a DOM icon on top of the canvas.
function drawHeadphoneIcon(ctx: CanvasRenderingContext2D, centerX: number, topY: number, color: string) {
  const radius = 6;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(centerX, topY + radius, radius, Math.PI, 0, false);
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.roundRect(centerX - radius - 1.5, topY + radius - 1, 3, 5, 1.5);
  ctx.fill();
  ctx.beginPath();
  ctx.roundRect(centerX + radius - 1.5, topY + radius - 1, 3, 5, 1.5);
  ctx.fill();
}

function drawZoneBar(
  ctx: CanvasRenderingContext2D,
  barX: number,
  barWidth: number,
  meterTop: number,
  meterBottom: number,
  redBottomY: number,
  yellowBottomY: number,
  currentDb: number,
  peakDb: number,
  muted: boolean,
) {
  if (muted) {
    ctx.fillStyle = "#111827";
    ctx.fillRect(barX, meterTop, barWidth, meterBottom - meterTop);
    ctx.strokeStyle = "#ef4444";
    ctx.lineWidth = 1;
    ctx.strokeRect(barX + 0.5, meterTop + 0.5, Math.max(0, barWidth - 1), meterBottom - meterTop - 1);
    return;
  }

  // Full zone-colored track, always drawn — the "unlit" overlay below is
  // what makes the current level visible as a fill line within it.
  ctx.fillStyle = "#7c2d12";
  ctx.fillRect(barX, meterTop, barWidth, redBottomY - meterTop);
  ctx.fillStyle = "#854d0e";
  ctx.fillRect(barX, redBottomY, barWidth, yellowBottomY - redBottomY);
  ctx.fillStyle = "#14532d";
  ctx.fillRect(barX, yellowBottomY, barWidth, meterBottom - yellowBottomY);

  const fillTopY = dbToY(currentDb, meterTop, meterBottom);
  ctx.fillStyle = "rgba(2, 6, 23, 0.88)";
  ctx.fillRect(barX, meterTop, barWidth, Math.max(0, fillTopY - meterTop));

  ctx.strokeStyle = "#1e293b";
  ctx.lineWidth = 1;
  ctx.strokeRect(barX + 0.5, meterTop + 0.5, Math.max(0, barWidth - 1), meterBottom - meterTop - 1);

  // Peak-hold indicator above the bar: dim by default, bright red once the
  // held peak is within a hair of clipping.
  const clipping = peakDb >= CLIP_DB;
  ctx.fillStyle = clipping ? "#ef4444" : "#7f1d1d";
  ctx.fillRect(barX, meterTop - 9, barWidth, 5);
}

function ChannelMetersImpl({
  channels,
  channelLevels,
  headphoneLevels,
  speakerEnabled,
  onToggleSpeaker,
  outputMode,
  active,
  className,
}: ChannelMetersProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const frame = React.useRef<number | null>(null);
  const hitTargets = React.useRef<HitTarget[]>([]);
  const peaks = React.useRef<Map<string, number>>(new Map());
  // Eased-toward-target level per bar (keyed same as `peaks`), so a fill
  // ramps up/down over the same visible duration as the haze/elevation
  // dissolve instead of jumping straight to the raw (possibly just-cleared)
  // `channelLevels`/`headphoneLevels` value.
  const displayLevels = React.useRef<Map<string, number>>(new Map());
  const lastTime = React.useRef<number | null>(null);
  const propsRef = React.useRef({ channels, speakerEnabled, outputMode });
  propsRef.current = { channels, speakerEnabled, outputMode };
  const activeRef = React.useRef(active);
  activeRef.current = active;
  const idleFrames = React.useRef(0);
  const wakeRef = React.useRef<() => void>(() => {});

  const smoothLevel = React.useCallback((key: string, target: number, deltaSec: number) => {
    const previous = displayLevels.current.get(key) ?? 0;
    const next = previous + (target - previous) * Math.min(1, deltaSec * LEVEL_SMOOTHING_RATE);
    displayLevels.current.set(key, next);
    return next;
  }, []);

  const updatePeak = React.useCallback((key: string, currentDb: number, deltaSec: number) => {
    const previous = peaks.current.get(key) ?? -60;
    const decayed = previous - PEAK_DECAY_DB_PER_SEC * deltaSec;
    const next = Math.max(currentDb, decayed);
    peaks.current.set(key, next);
    return next;
  }, []);

  React.useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(container.clientWidth * dpr));
      canvas.height = Math.max(1, Math.round(container.clientHeight * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    // Resizing a canvas clears its pixel buffer. While the draw loop is
    // idle (see `SETTLE_FRAMES`), a layout shift — e.g. the "Preparing
    // preview…" banner disappearing once ready — fires this observer after
    // the loop already stopped, clearing the canvas with nothing left to
    // redraw it. Waking the loop here (a no-op if it's already running)
    // guarantees at least one fresh frame after every resize.
    const observer = new ResizeObserver(() => {
      resize();
      wakeRef.current();
    });
    observer.observe(container);

    const draw = (time: number) => {
      const deltaSec = lastTime.current === null ? 0 : Math.min(0.25, (time - lastTime.current) / 1000);
      lastTime.current = time;
      const { channels: currentChannels, speakerEnabled: currentEnabled, outputMode: currentMode } = propsRef.current;
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.width / dpr;
      const height = canvas.height / dpr;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#020617";
      ctx.fillRect(0, 0, width, height);

      const padLeft = 26;
      const padRight = 10;
      const padTop = 16;
      const labelHeight = 20;
      const meterTop = padTop;
      const meterBottom = height - labelHeight;
      const plotWidth = Math.max(1, width - padLeft - padRight);

      // dB scale: gridlines + labels shared down the left edge.
      ctx.font = "600 9px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.strokeStyle = "rgba(148, 163, 184, 0.15)";
      ctx.lineWidth = 1;
      for (const tick of DB_TICKS) {
        const y = dbToY(tick, meterTop, meterBottom);
        ctx.beginPath();
        ctx.moveTo(padLeft, y);
        ctx.lineTo(width - padRight, y);
        ctx.stroke();
        ctx.fillStyle = "#64748b";
        ctx.fillText(String(tick), padLeft - 4, y);
      }

      const order = [
        ...currentChannels.filter((channel) => channel !== "LFE"),
        ...(currentChannels.includes("LFE") ? ["LFE"] : []),
      ];
      // Native mode has no trailing group — the per-layout bars above
      // already show every discrete channel reaching the device. Binaural
      // and stereo both reserve +1 slot for the gap plus +2 for the group's
      // two bars.
      const slots = currentMode === "native" ? order.length : order.length + 1 + 2;
      const pitch = plotWidth / slots;
      const barWidth = Math.max(6, pitch * 0.6);
      const nextHits: HitTarget[] = [];
      // Tracks whether every bar's peak marker has caught up to its current
      // fill level this frame — see the `active`-gating comment above.
      let settled = true;
      order.forEach((channel, index) => {
        const centerX = padLeft + (index + 0.5) * pitch;
        const barX = centerX - barWidth / 2;
        const muted = currentEnabled[channel] === false;
        const rawLevel = channelLevels.current.get(channel) ?? 0;
        const level = smoothLevel(channel, rawLevel, deltaSec);
        const currentDb = muted ? -60 : levelToDb(level);
        const peakDb = updatePeak(channel, currentDb, deltaSec);
        if (peakDb - currentDb > SETTLE_EPSILON_DB) settled = false;
        const redBottomY = dbToY(RED_ZONE_DB, meterTop, meterBottom);
        const yellowBottomY = dbToY(YELLOW_ZONE_DB, meterTop, meterBottom);

        drawZoneBar(ctx, barX, barWidth, meterTop, meterBottom, redBottomY, yellowBottomY, currentDb, peakDb, muted);

        const label = channel === "LFE" ? "LFE" : speakerDisplayLabel(channel, currentChannels);
        drawLabel(ctx, label, centerX, meterBottom + 3, muted ? "#f87171" : "#cbd5e1", pitch);
        nextHits.push({ channel, x: barX, width: barWidth });
      });
      hitTargets.current = nextHits;

      // Separator + trailing group: reads the actual final-output signal
      // (binaural or stereo downmix — whichever is live), independent of any
      // speaker mute. Native has no trailing group at all: the per-layout
      // bars above already are the discrete output.
      if (currentMode !== "native") {
        const dividerX = padLeft + (order.length + 0.5) * pitch;
        ctx.strokeStyle = "#1e293b";
        ctx.beginPath();
        ctx.moveTo(dividerX, meterTop);
        ctx.lineTo(dividerX, meterBottom);
        ctx.stroke();

        const headphones = headphoneLevels.current;
        const barCenters: number[] = [];
        (["L", "R"] as const).forEach((label, index) => {
          const slotIndex = order.length + 1 + index;
          const centerX = padLeft + (slotIndex + 0.5) * pitch;
          barCenters.push(centerX);
          const barX = centerX - barWidth / 2;
          const rawLevel = label === "L" ? headphones.left : headphones.right;
          const level = smoothLevel(`hp:${label}`, rawLevel, deltaSec);
          const currentDb = levelToDb(level);
          const peakDb = updatePeak(`hp:${label}`, currentDb, deltaSec);
          if (peakDb - currentDb > SETTLE_EPSILON_DB) settled = false;
          const redBottomY = dbToY(RED_ZONE_DB, meterTop, meterBottom);
          const yellowBottomY = dbToY(YELLOW_ZONE_DB, meterTop, meterBottom);

          drawZoneBar(ctx, barX, barWidth, meterTop, meterBottom, redBottomY, yellowBottomY, currentDb, peakDb, false);
          if (currentMode === "stereo") drawLabel(ctx, label, centerX, meterBottom + 3, "#7dd3fc", pitch);
        });
        if (currentMode === "binaural") {
          const groupCenterX = (barCenters[0] + barCenters[1]) / 2;
          drawHeadphoneIcon(ctx, groupCenterX, meterBottom + 3, "#7dd3fc");
        }
      }

      idleFrames.current = !activeRef.current && settled ? idleFrames.current + 1 : 0;
      if (activeRef.current || idleFrames.current < SETTLE_FRAMES) {
        frame.current = window.requestAnimationFrame(draw);
      } else {
        frame.current = null;
      }
    };
    frame.current = window.requestAnimationFrame(draw);
    wakeRef.current = () => {
      if (frame.current === null) {
        idleFrames.current = 0;
        frame.current = window.requestAnimationFrame(draw);
      }
    };

    return () => {
      observer.disconnect();
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    };
  }, [channelLevels, headphoneLevels, updatePeak]);

  React.useEffect(() => {
    wakeRef.current();
  }, [active, channels, speakerEnabled, outputMode]);

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!onToggleSpeaker) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const hit = hitTargets.current.find((target) => x >= target.x && x <= target.x + target.width);
    if (hit) onToggleSpeaker(hit.channel);
  };

  return (
    <div className={`relative flex min-w-[180px] max-w-[480px] flex-1 flex-col overflow-hidden rounded-lg border bg-slate-950 text-slate-100 ${className || ""}`}>
      <div className="pointer-events-none px-2 pt-2 text-xs text-slate-400">Levels</div>
      <div ref={containerRef} className="min-h-0 flex-1">
        <canvas ref={canvasRef} className="h-full w-full cursor-pointer" onPointerDown={handlePointerDown} />
      </div>
    </div>
  );
}

export default React.memo(ChannelMetersImpl);
