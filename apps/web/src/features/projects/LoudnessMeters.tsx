import * as React from "react";
import { cancelFrame, requestFrame } from "@/lib/animationFrame";
import { canvasTheme } from "@/lib/canvasTheme";
import {
  DB_TICKS,
  LOUDNESS_METER_PALETTE,
  MULTI_CHANNEL_YELLOW_ZONE_DB,
  RED_ZONE_DB,
  SETTLE_EPSILON_DB,
  createMeterState,
  dbToY,
  drawMeterBar,
} from "@/lib/meterScale";
import { cn } from "@/lib/utils";
import type { LoudnessSummary, MasterMeters, MeterLevel, OutputMode } from "./useStemPreview";
import type { EngineRef } from "./wasmEngine/engineTypes";

// The mastering readouts: EBU Tech 3341 M/S/I loudness with the delivery
// target beside it and the crest metrics derived from those same numbers.
// Numbers only — every value is measured in the core (see `MasterMeters`),
// this file just draws them.
// Appearance follows docs/web_ui_controls.md.

/** Below this the meter is reading silence, not a level. */
const SILENT_LKFS = -70;

/** Rolling window the short-term crest factor is taken over, matching the
 * short-term loudness it is measured against. */
const PSR_WINDOW_MS = 3000;

/** Readable at a glance: one decimal, and an explicit floor rather than a
 * misleading "-70.0". */
export function formatLkfs(value: number): string {
  return value <= SILENT_LKFS ? "-∞" : value.toFixed(1);
}

export function formatLu(value: number): string {
  return Number.isFinite(value) ? value.toFixed(1) : "—";
}

/** Peak-to-loudness ratio: the delivered programme's crest over its whole
 * length, from the measurement pass's true peak and integrated loudness. */
export function peakToLoudness(truePeakDbtp: number, integratedLkfs: number): number {
  if (integratedLkfs <= SILENT_LKFS) return NaN;
  return truePeakDbtp - integratedLkfs;
}

/** Peak-to-short-term ratio: the same crest over the last three seconds.
 * `peakDb` is the delivered sample peak over that window, not a true peak —
 * the live meters carry sample peak only. */
export function peakToShortTerm(peakDb: number, shortTermLkfs: number): number {
  if (shortTermLkfs <= SILENT_LKFS) return NaN;
  return peakDb - shortTermLkfs;
}

/** Which programme the readout is measuring.
 *
 * The measurement pass follows whatever collapse the transport is auditioning,
 * and a native bed wider than 5.1 is measured on its 5.1 re-render
 * (docs/standards/loudness_dsp_bs1770.md §"Measurement programme"). Without
 * this label a stereo-fold reading is indistinguishable from the bed's. */
export function collapseModeLabel(mode: OutputMode, channelCount: number): string {
  if (mode === "stereo") return "Stereo fold";
  if (mode === "binaural") return "Binaural";
  if (mode === "transaural") return "Transaural";
  if (mode === "apple_spatial") return channelCount > 6 ? "Apple Spatial · pre-PHASE 5.1" : "Apple Spatial · pre-PHASE bed";
  return channelCount > 6 ? "5.1 re-render" : "Native bed";
}

export type MasterReadout = MasterMeters & {
  /** Delivered sample peak over the short-term window, dBFS. */
  shortPeakDb: number;
};

const SILENT_READOUT: MasterReadout = {
  momentaryLkfs: SILENT_LKFS,
  shortTermLkfs: SILENT_LKFS,
  shortPeakDb: -Infinity,
};

/**
 * Samples the engine's master-meter ref into React state at ~10 Hz.
 *
 * Text, unlike the level bars, is unreadable at frame rate and re-rendering
 * around the canvases at 60 Hz is exactly what the meter refs exist to avoid
 * — the same reasoning `useStripMeterLoop` applies to its own numeric
 * readout. The short-term peak is accumulated here rather than in the core:
 * every frame already carries the output pair's peak over its window, so the
 * rolling maximum over three seconds of them is bookkeeping, not DSP.
 */
export function useMasterReadout(
  meters: EngineRef<MasterMeters>,
  output: EngineRef<{ left: MeterLevel; right: MeterLevel }>,
  active: boolean,
): MasterReadout {
  const [readout, setReadout] = React.useState<MasterReadout>(SILENT_READOUT);
  const history = React.useRef<{ at: number; peak: number }[]>([]);

  React.useEffect(() => {
    let frame = 0;
    let last = 0;
    let flushed = false;
    const tick = (time: number) => {
      const pair = output.current;
      const peak = Math.max(pair.left.peak, pair.right.peak);
      history.current.push({ at: time, peak });
      const cutoff = time - PSR_WINDOW_MS;
      while (history.current.length > 0 && history.current[0].at < cutoff) {
        history.current.shift();
      }
      if (time - last >= 100) {
        last = time;
        const windowPeak = history.current.reduce((max, entry) => Math.max(max, entry.peak), 0);
        setReadout({
          ...meters.current,
          shortPeakDb: windowPeak > 0 ? 20 * Math.log10(windowPeak) : -Infinity,
        });
        flushed = true;
      }
      // A paused transport stops posting frames and the engine zeroes its
      // meters, so one flush settles the readout — holding a 60 Hz loop open
      // on a silent transport buys nothing.
      if (!active && flushed) return;
      frame = requestFrame(tick);
    };
    frame = requestFrame(tick);
    return () => cancelFrame(frame);
    // Restarting on `active` keeps the loop tied to transport state the way
    // the strip meters' own loop is; the values it reads are refs, not props.
  }, [meters, output, active]);

  return readout;
}

/** The loudness field stays dark in both app themes, same as `ChannelMeters`
 * (see `canvasTheme`'s own note) — its readout below the bars uses the fixed
 * canvas colours rather than the `text-muted-foreground`/theme tokens the
 * rest of the app reads, or the text would vanish against the fixed-dark
 * field in light mode. */
function StackCell({
  label,
  value,
  unit,
  title,
  tone,
}: {
  label: string;
  value: string;
  unit?: string;
  title: string;
  tone?: "warn";
}) {
  return (
    <div className="flex flex-col items-center text-center" title={title}>
      <span className="text-[8px] font-semibold uppercase tracking-wide" style={{ color: canvasTheme.label }}>
        {label}
      </span>
      <span
        className="text-[10px] font-medium tabular-nums"
        style={{ color: tone === "warn" ? canvasTheme.meterWarn : canvasTheme.labelStrong }}
      >
        {value}
        {unit && <span style={{ color: canvasTheme.label }}>{unit}</span>}
      </span>
    </div>
  );
}

/** Consecutive idle frames (bars unchanging) required before the draw loop
 * stops scheduling itself, same convention as `ChannelMeters`. */
const SETTLE_FRAMES = 20;

/** Same 40px gutter `ChannelMeters` uses on every side, so the loudness
 * bars' 0dB row sits at the identical pixel height as the level meters'. */
const LOUDNESS_PAD = 40;

/**
 * The live loudness bars: momentary and short-term, on the same dB scale and
 * drawn with the same primitives as the level meters, but in the loudness
 * meter's own hue (`LOUDNESS_METER_PALETTE`) so the two never read as one
 * meter. Read straight off the meters ref every frame, same as
 * `ChannelMeters` reads its channel levels — `useMasterReadout` samples at
 * 10 Hz for text, too coarse for a smooth bar.
 */
function LoudnessLevelBars({
  masterMeters,
  active,
  className,
}: {
  masterMeters: EngineRef<MasterMeters>;
  active: boolean;
  className?: string;
}) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const frame = React.useRef<number | null>(null);
  const meterState = React.useRef(createMeterState());
  const lastTime = React.useRef<number | null>(null);
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

    // `createMeterState` defaults an unseen key's eased level to 0 — correct
    // for `ChannelMeters`, whose "level" is linear (0 = silence), but read
    // directly as dB here 0 means the loudest point on the scale. Without
    // this the bars render at the top on mount and audibly sweep down to
    // silence over the first ~second, even with nothing playing.
    meterState.current.smoothLevel("M", SILENT_LKFS, 1);
    meterState.current.smoothLevel("S", SILENT_LKFS, 1);
    meterState.current.updatePeak("M", SILENT_LKFS, 1);
    meterState.current.updatePeak("S", SILENT_LKFS, 1);

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(container.clientWidth * dpr));
      canvas.height = Math.max(1, Math.round(container.clientHeight * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(() => {
      resize();
      wakeRef.current();
    });
    observer.observe(container);

    const draw = (time: number) => {
      const deltaSec = lastTime.current === null ? 0 : Math.min(0.25, (time - lastTime.current) / 1000);
      lastTime.current = time;
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.width / dpr;
      const height = canvas.height / dpr;
      ctx.clearRect(0, 0, width, height);
      const field = ctx.createLinearGradient(0, 0, 0, height);
      field.addColorStop(0, canvasTheme.plotField);
      field.addColorStop(1, canvasTheme.plotFieldCore);
      ctx.fillStyle = field;
      ctx.fillRect(0, 0, width, height);

      const padLeft = LOUDNESS_PAD;
      const padRight = LOUDNESS_PAD;
      const meterTop = LOUDNESS_PAD;
      const meterBottom = height - LOUDNESS_PAD;
      const plotWidth = Math.max(1, width - padLeft - padRight);

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

      const bars: [string, number][] = [
        ["M", masterMeters.current.momentaryLkfs],
        ["S", masterMeters.current.shortTermLkfs],
      ];
      const pitch = plotWidth / bars.length;
      const barWidth = Math.max(6, pitch * 0.45);
      const redBottomY = dbToY(RED_ZONE_DB, meterTop, meterBottom);
      const yellowBottomY = dbToY(MULTI_CHANNEL_YELLOW_ZONE_DB, meterTop, meterBottom);
      let settled = true;

      bars.forEach(([key, lkfs], index) => {
        const centerX = padLeft + (index + 0.5) * pitch;
        const barX = centerX - barWidth / 2;
        const currentDb = meterState.current.smoothLevel(key, lkfs, deltaSec);
        const peakDb = meterState.current.updatePeak(key, currentDb, deltaSec);
        if (peakDb - currentDb > SETTLE_EPSILON_DB) settled = false;

        drawMeterBar(
          ctx, barX, barWidth, meterTop, meterBottom, redBottomY, yellowBottomY,
          currentDb, peakDb, false, false,
          { palette: LOUDNESS_METER_PALETTE, yellowZoneDb: MULTI_CHANNEL_YELLOW_ZONE_DB },
        );

        ctx.fillStyle = canvasTheme.labelStrong;
        ctx.font = "700 9px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(key, centerX, meterBottom + 3);
      });

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
  }, [masterMeters]);

  React.useEffect(() => {
    wakeRef.current();
  }, [active]);

  return (
    <div ref={containerRef} className={cn("min-h-0", className)}>
      <canvas ref={canvasRef} className="h-full w-full" role="img" aria-label="Momentary and short-term loudness" />
    </div>
  );
}

/**
 * The loudness meter: live momentary/short-term bars in the loudness
 * meter's own hue, plus every other loudness figure the old text readout
 * carried — integrated, true peak, delivery target, crest ratios, and the
 * A/B monitor offset — stacked below as a compact readout. Sits beside
 * `ChannelMeters` as its own instrument rather than a full-width status line.
 */
export function LoudnessMeterPanel({
  loudness,
  masterMeters,
  headphoneLevels,
  active,
  bypassed,
  outputMode,
  channelCount,
  className,
}: {
  loudness: LoudnessSummary;
  masterMeters: EngineRef<MasterMeters>;
  headphoneLevels: EngineRef<{ left: MeterLevel; right: MeterLevel }>;
  active: boolean;
  bypassed: boolean;
  outputMode: OutputMode;
  channelCount: number;
  className?: string;
}) {
  const live = useMasterReadout(masterMeters, headphoneLevels, active);
  const overTarget = loudness.integratedLkfs > loudness.targetLkfs + 0.5;
  const overCeiling = loudness.truePeakDbtp > loudness.ceilingDbtp;

  return (
    <div
      className={cn("flex flex-col overflow-hidden rounded-lg border", className)}
      style={{ backgroundColor: canvasTheme.plotField }}
      role="group"
      aria-label="Loudness"
    >
      <LoudnessLevelBars masterMeters={masterMeters} active={active} className="min-h-0 flex-1" />
      <div
        className="flex shrink-0 flex-col gap-1 px-1 py-1"
        style={{ backgroundColor: canvasTheme.plotFieldCore, borderTop: `1px solid ${canvasTheme.grid}` }}
      >
        <span
          className="truncate text-center text-[9px] font-medium"
          style={{ color: canvasTheme.labelStrong }}
          title="Programme the loudness readout is measured on — it follows the active output mode, and a native bed wider than 5.1 is measured on its 5.1 re-render"
        >
          {collapseModeLabel(outputMode, channelCount)}
        </span>
        <div className="grid grid-cols-3 gap-x-1 gap-y-1">
          <StackCell
            label="I"
            value={formatLkfs(loudness.integratedLkfs)}
            title="Integrated loudness of the whole programme (LKFS)"
            tone={overTarget ? "warn" : undefined}
          />
          <StackCell
            label="TP"
            value={formatLkfs(loudness.truePeakDbtp)}
            title="Maximum true peak (dBTP)"
            tone={overCeiling ? "warn" : undefined}
          />
          <StackCell
            label="Tgt"
            value={loudness.targetLkfs.toFixed(0)}
            title="Delivery target: integrated loudness (LKFS)"
          />
          <StackCell
            label="PLR"
            value={formatLu(peakToLoudness(loudness.truePeakDbtp, loudness.integratedLkfs))}
            title="Peak-to-loudness ratio: true peak over integrated loudness"
          />
          <StackCell
            label="PSR"
            value={formatLu(peakToShortTerm(live.shortPeakDb, live.shortTermLkfs))}
            title="Peak-to-short-term ratio: sample peak over short-term loudness, last 3 s"
          />
          <StackCell
            label="Ceil"
            value={loudness.ceilingDbtp.toFixed(1)}
            title="Delivery target: true-peak ceiling (dBTP)"
          />
        </div>
        {bypassed && (
          <StackCell
            label="A/B"
            value={`${loudness.bypassMatchDb >= 0 ? "+" : ""}${loudness.bypassMatchDb.toFixed(1)}`}
            unit=" dB"
            title="Monitor gain matching the bypassed chain to the mastered one's loudness — monitoring only, never exported"
            tone="warn"
          />
        )}
      </div>
    </div>
  );
}
