import * as React from "react";
import { cancelFrame, requestFrame } from "@/lib/animationFrame";
import { canvasTheme } from "@/lib/canvasTheme";
import {
  DB_TICKS,
  MULTI_CHANNEL_YELLOW_ZONE_DB,
  RED_ZONE_DB,
  SETTLE_EPSILON_DB,
  createMeterState,
  dbToY,
  drawMeterBar,
  levelToDb,
} from "@/lib/meterScale";
import { speakerDisplayLabel } from "@/lib/spatial";
import type { MeterLevel, OutputMode } from "./useStemPreview";

// Reads channelLevels/headphoneLevels refs in its own rAF loop rather than
// subscribing to React state, same pattern as HazeView/ElevationView.

export type ChannelMetersProps = {
  channels: string[];
  channelLevels: React.MutableRefObject<Map<string, MeterLevel>>;
  headphoneLevels: React.MutableRefObject<{ left: MeterLevel; right: MeterLevel }>;
  speakerEnabled: Record<string, boolean>;
  speakerSolo: ReadonlySet<string>;
  onToggleSpeaker?: (channel: string) => void;
  onSoloSpeaker?: (channel: string) => void;
  // Controls the trailing group: headphone bars for binaural, L/R for stereo,
  // nothing for native (per-layout bars already show every discrete channel).
  outputMode: OutputMode;
  // True while preview audio is live-updating the level refs (preview.playing).
  // On pause/stop the draw loop eases bars toward zero (smoothLevel) instead
  // of snapping, then stops once settled (SETTLE_FRAMES below).
  active: boolean;
  className?: string;
};

// Consecutive idle frames (bars unchanging) required before the draw loop
// stops scheduling itself. Not time-critical — just enough to let the peak
// decay animation visibly settle rather than freeze mid-motion.
const SETTLE_FRAMES = 20;

type HitTarget = { channel: string; x: number; width: number };

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

function ChannelMetersImpl({
  channels,
  channelLevels,
  headphoneLevels,
  speakerEnabled,
  speakerSolo,
  onToggleSpeaker,
  onSoloSpeaker,
  outputMode,
  active,
  className,
}: ChannelMetersProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const frame = React.useRef<number | null>(null);
  const hitTargets = React.useRef<HitTarget[]>([]);
  // Eased level + decay-held peak per bar, from the shared meter state so
  // this display and the mixer's strip meters cannot drift apart.
  const meterState = React.useRef(createMeterState());
  const lastTime = React.useRef<number | null>(null);
  const propsRef = React.useRef({ channels, speakerEnabled, speakerSolo, outputMode });
  propsRef.current = { channels, speakerEnabled, speakerSolo, outputMode };
  const activeRef = React.useRef(active);
  activeRef.current = active;
  const idleFrames = React.useRef(0);
  const wakeRef = React.useRef<() => void>(() => {});

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
    // Repaints synchronously rather than scheduling a frame: a resize clears the
    // canvas, and scheduling would paint the cleared buffer during an animated resize.
    const observer = new ResizeObserver(() => {
      resize();
      wakeRef.current();
    });
    observer.observe(container);

    const draw = (time: number) => {
      const deltaSec = lastTime.current === null ? 0 : Math.min(0.25, (time - lastTime.current) / 1000);
      lastTime.current = time;
      const { channels: currentChannels, speakerEnabled: currentEnabled, speakerSolo: currentSolo, outputMode: currentMode } = propsRef.current;
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.width / dpr;
      const height = canvas.height / dpr;
      ctx.clearRect(0, 0, width, height);
      // Same deep-navy field and systemBlue wash as the Haze and Elevation
      // displays, so the three panels read as one instrument surface rather
      // than a black box sitting beside two lit graphs. The wash rises from
      // the bottom, matching the direction the bars themselves fill.
      const field = ctx.createLinearGradient(0, 0, 0, height);
      field.addColorStop(0, canvasTheme.plotField);
      field.addColorStop(1, canvasTheme.plotFieldCore);
      ctx.fillStyle = field;
      ctx.fillRect(0, 0, width, height);
      const shade = ctx.createLinearGradient(0, height, 0, 0);
      shade.addColorStop(0, canvasTheme.plotShadeStrong);
      shade.addColorStop(0.45, canvasTheme.plotShade);
      shade.addColorStop(1, "rgba(10, 132, 255, 0)");
      ctx.fillStyle = shade;
      ctx.fillRect(0, 0, width, height);

      // Same uniform 40px gutter as Haze/Elevation (§4.1) — bars stop at
      // `pad` from every edge, with the channel-name row living inside the
      // bottom gutter the same way those two views draw their own axis
      // labels inside their padding rather than in a separate strip.
      const pad = 40;
      const padLeft = pad;
      const padRight = pad;
      const padTop = pad;
      const meterTop = padTop;
      const meterBottom = height - pad;
      const plotWidth = Math.max(1, width - padLeft - padRight);

      // dB scale: numeral in the left gutter plus a hairline ruled across the
      // whole field, as Logic's Level Meter draws it. Painted before the bars
      // — which are opaque — so the rules read only through the gaps between
      // columns and never cut across a lit bar. 0dB sits forward of the rest.
      ctx.font = "500 9px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.lineWidth = 1;
      for (const tick of DB_TICKS) {
        const y = dbToY(tick, meterTop, meterBottom);
        ctx.strokeStyle = tick === 0 ? canvasTheme.grid : canvasTheme.gridSoft;
        ctx.beginPath();
        ctx.moveTo(padLeft, Math.round(y) + 0.5);
        ctx.lineTo(width - padRight, Math.round(y) + 0.5);
        ctx.stroke();
        ctx.fillStyle = canvasTheme.label;
        ctx.fillText(String(tick), padLeft - 5, y);
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
      const barWidth = Math.max(6, pitch * 0.7);
      const nextHits: HitTarget[] = [];
      // Tracks whether every bar's peak marker has caught up to its current
      // fill level this frame — see the `active`-gating comment above.
      let settled = true;
      order.forEach((channel, index) => {
        const centerX = padLeft + (index + 0.5) * pitch;
        const barX = centerX - barWidth / 2;
        const muted = currentEnabled[channel] === false;
        const soloed = currentSolo.has(channel);
        const silent = muted || (currentSolo.size > 0 && !soloed);
        const meterLevel = channelLevels.current.get(channel);
        const level = meterState.current.smoothLevel(channel, meterLevel?.rms ?? 0, deltaSec);
        const currentDb = silent ? -60 : levelToDb(level);
        // Peak-hold tracks the smoothed RMS bar (decay-held), not the raw
        // instantaneous peak — real music's crest factor made the tick read
        // as detached, floating off the bar. The 0dBFS clip latch below still
        // uses the true instantaneous peak.
        const peakDb = meterState.current.updatePeak(channel, currentDb, deltaSec);
        if (peakDb - currentDb > SETTLE_EPSILON_DB) settled = false;
        const redBottomY = dbToY(RED_ZONE_DB, meterTop, meterBottom);
        // Always multi-channel here, so the later multi-channel yellow floor applies.
        const yellowBottomY = dbToY(MULTI_CHANNEL_YELLOW_ZONE_DB, meterTop, meterBottom);

        drawMeterBar(
          ctx, barX, barWidth, meterTop, meterBottom, redBottomY, yellowBottomY,
          currentDb, peakDb, muted, !muted && (meterLevel?.clipped ?? false),
          { yellowZoneDb: MULTI_CHANNEL_YELLOW_ZONE_DB },
        );

        const label = channel === "LFE" ? "LFE" : speakerDisplayLabel(channel, currentChannels);
        drawLabel(ctx, label, centerX, meterBottom + 3, muted ? canvasTheme.muteLabel : soloed ? canvasTheme.meterWarn : silent ? canvasTheme.label : canvasTheme.labelStrong, pitch);
        nextHits.push({ channel, x: barX, width: barWidth });
      });
      hitTargets.current = nextHits;

      // Separator + trailing group: reads the actual final-output signal
      // (binaural or stereo downmix — whichever is live), independent of any
      // speaker mute. Native has no trailing group at all: the per-layout
      // bars above already are the discrete output.
      if (currentMode !== "native") {
        const dividerX = padLeft + (order.length + 0.5) * pitch;
        ctx.strokeStyle = canvasTheme.grid;
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
          const meterLevel = label === "L" ? headphones.left : headphones.right;
          const level = meterState.current.smoothLevel(`hp:${label}`, meterLevel.rms, deltaSec);
          const currentDb = levelToDb(level);
          // Peak-hold tracks the RMS bar (decay-held), same as the channel
          // bars above — see that comment.
          const peakDb = meterState.current.updatePeak(`hp:${label}`, currentDb, deltaSec);
          if (peakDb - currentDb > SETTLE_EPSILON_DB) settled = false;
          const redBottomY = dbToY(RED_ZONE_DB, meterTop, meterBottom);
          // The binaural/stereo trailing group is a two-channel downmix,
          // same multi-channel floor as the per-speaker bars above.
          const yellowBottomY = dbToY(MULTI_CHANNEL_YELLOW_ZONE_DB, meterTop, meterBottom);

          drawMeterBar(
            ctx, barX, barWidth, meterTop, meterBottom, redBottomY, yellowBottomY,
            currentDb, peakDb, false, meterLevel.clipped,
            { yellowZoneDb: MULTI_CHANNEL_YELLOW_ZONE_DB },
          );
          if (currentMode === "stereo") drawLabel(ctx, label, centerX, meterBottom + 3, canvasTheme.headphone, pitch);
        });
        if (currentMode === "binaural") {
          const groupCenterX = (barCenters[0] + barCenters[1]) / 2;
          drawHeadphoneIcon(ctx, groupCenterX, meterBottom + 3, canvasTheme.headphone);
        }
      }

      idleFrames.current = !activeRef.current && settled ? idleFrames.current + 1 : 0;
      if (activeRef.current || idleFrames.current < SETTLE_FRAMES) {
        frame.current = requestFrame(draw);
      } else {
        frame.current = null;
      }
    };
    frame.current = requestFrame(draw);
    wakeRef.current = () => {
      if (frame.current !== null) cancelFrame(frame.current);
      frame.current = null;
      idleFrames.current = 0;
      draw(performance.now());
    };

    return () => {
      observer.disconnect();
      if (frame.current !== null) cancelFrame(frame.current);
    };
  }, [channelLevels, headphoneLevels]);

  React.useEffect(() => {
    wakeRef.current();
  }, [active, channels, speakerEnabled, speakerSolo, outputMode]);

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!onToggleSpeaker || (event.altKey && !onSoloSpeaker)) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const hit = hitTargets.current.find((target) => x >= target.x && x <= target.x + target.width);
    if (!hit) return;
    if (event.altKey) onSoloSpeaker?.(hit.channel);
    else onToggleSpeaker(hit.channel);
  };

  // Sizing is the caller's call (passes w-full h-full) — a fixed internal
  // width cap fought the caller's own explicit width once Haze/Elevation/Meters
  // became user-resizable.
  return (
    <div
      className={`relative flex flex-col overflow-hidden rounded-lg border ${className || ""}`}
      style={{ backgroundColor: canvasTheme.plotField }}
    >
      <div ref={containerRef} className="min-h-0 flex-1">
        <canvas ref={canvasRef} className="h-full w-full cursor-pointer" onPointerDown={handlePointerDown} />
      </div>
    </div>
  );
}

export default React.memo(ChannelMetersImpl);
