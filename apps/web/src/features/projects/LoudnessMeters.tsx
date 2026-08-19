import * as React from "react";
import { canvasTheme } from "@/lib/canvasTheme";
import { cn } from "@/lib/utils";
import type { LoudnessSummary, MasterMeters, MeterLevel } from "./useStemPreview";
import type { EngineRef } from "./wasmEngine/engineTypes";

// The mastering readouts: EBU Tech 3341 M/S/I loudness with the delivery
// target beside it, the crest metrics derived from those same numbers, and
// one gain-reduction bar per dynamics stage. Numbers only — every value is
// measured in the core (see `MasterMeters`), this file just draws them.
// Appearance follows the strip idiom of docs/web_ui_design.md §6.4.

/** Deepest reduction a GR bar shows full-scale. */
export const GR_FULL_SCALE_DB = 12;

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

export type MasterReadout = MasterMeters & {
  /** Delivered sample peak over the short-term window, dBFS. */
  shortPeakDb: number;
};

const SILENT_READOUT: MasterReadout = {
  momentaryLkfs: SILENT_LKFS,
  shortTermLkfs: SILENT_LKFS,
  compGrDb: 0,
  limiterGrDb: 0,
  limiterLfeGrDb: 0,
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
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
    // Restarting on `active` keeps the loop tied to transport state the way
    // the strip meters' own loop is; the values it reads are refs, not props.
  }, [meters, output, active]);

  return readout;
}

function Cell({
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
    <div className="flex items-baseline gap-1" title={title}>
      <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span
        className={cn("text-[11px] font-medium tabular-nums", tone === "warn" && "text-warning")}
      >
        {value}
        {unit && <span className="text-muted-foreground">{unit}</span>}
      </span>
    </div>
  );
}

/**
 * The preview's loudness line: momentary and short-term off the live meters,
 * integrated and true peak off the measurement pass, the resolved delivery
 * target, and the crest metrics derived from them. The A/B's match gain shows
 * here too — a monitor-only offset the listener should know is applied.
 */
export function LoudnessReadout({
  loudness,
  masterMeters,
  headphoneLevels,
  active,
  bypassed,
  className,
}: {
  loudness: LoudnessSummary;
  masterMeters: EngineRef<MasterMeters>;
  headphoneLevels: EngineRef<{ left: MeterLevel; right: MeterLevel }>;
  active: boolean;
  bypassed: boolean;
  className?: string;
}) {
  const live = useMasterReadout(masterMeters, headphoneLevels, active);
  const overTarget = loudness.integratedLkfs > loudness.targetLkfs + 0.5;
  const overCeiling = loudness.truePeakDbtp > loudness.ceilingDbtp;

  return (
    <div
      className={cn(
        "flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-md border bg-muted/40 px-2 py-1",
        className,
      )}
      role="group"
      aria-label="Loudness"
    >
      <Cell label="M" value={formatLkfs(live.momentaryLkfs)} title="Momentary loudness, 400 ms (LKFS)" />
      <Cell label="S" value={formatLkfs(live.shortTermLkfs)} title="Short-term loudness, 3 s (LKFS)" />
      <Cell
        label="I"
        value={formatLkfs(loudness.integratedLkfs)}
        title="Integrated loudness of the whole programme (LKFS)"
        tone={overTarget ? "warn" : undefined}
      />
      <Cell
        label="TP"
        value={formatLkfs(loudness.truePeakDbtp)}
        title="Maximum true peak (dBTP)"
        tone={overCeiling ? "warn" : undefined}
      />
      <Cell
        label="Target"
        value={`${loudness.targetLkfs.toFixed(0)} / ${loudness.ceilingDbtp.toFixed(1)}`}
        title="Delivery target: integrated LKFS / true-peak ceiling dBTP"
      />
      <Cell
        label="PLR"
        value={formatLu(peakToLoudness(loudness.truePeakDbtp, loudness.integratedLkfs))}
        title="Peak-to-loudness ratio: true peak over integrated loudness"
      />
      <Cell
        label="PSR"
        value={formatLu(peakToShortTerm(live.shortPeakDb, live.shortTermLkfs))}
        title="Peak-to-short-term ratio: sample peak over short-term loudness, last 3 s"
      />
      {bypassed && (
        <Cell
          label="A/B"
          value={`${loudness.bypassMatchDb >= 0 ? "+" : ""}${loudness.bypassMatchDb.toFixed(1)}`}
          unit=" dB"
          title="Monitor gain matching the bypassed chain to the mastered one's loudness — monitoring only, never exported"
          tone="warn"
        />
      )}
    </div>
  );
}

function GrBar({ label, db, title }: { label: string; db: number; title: string }) {
  const fill = Math.min(1, db / GR_FULL_SCALE_DB);
  return (
    <div className="flex min-w-0 flex-1 flex-col items-center gap-0.5" title={title}>
      <div
        className="relative h-full w-full overflow-hidden rounded-[2px]"
        style={{ backgroundColor: canvasTheme.stripWell }}
        role="meter"
        aria-label={`${title} gain reduction`}
        aria-valuenow={Number(db.toFixed(1))}
        aria-valuemin={0}
        aria-valuemax={GR_FULL_SCALE_DB}
      >
        {/* Fills downward from the top, the direction gain reduction moves. */}
        <div
          className="absolute inset-x-0 top-0"
          style={{
            height: `${fill * 100}%`,
            backgroundColor: db > GR_FULL_SCALE_DB / 2 ? canvasTheme.meterHot : canvasTheme.meterWarn,
          }}
        />
      </div>
      <span className="text-[8px] font-semibold uppercase text-muted-foreground">{label}</span>
    </div>
  );
}

/** Compressor and limiter gain reduction, on the master strip beside the
 * level meter. The LFE bar is the limiter's own second curve — it is capped
 * independently of the mains (docs/standards/loudness_dsp_bs1770.md §"LFE and
 * true-peak"), so it reads separately or its reduction would look like the
 * mains'. */
export function GainReductionMeters({
  masterMeters,
  headphoneLevels,
  active,
  hasLfe,
  className,
}: {
  masterMeters: EngineRef<MasterMeters>;
  headphoneLevels: EngineRef<{ left: MeterLevel; right: MeterLevel }>;
  active: boolean;
  hasLfe: boolean;
  className?: string;
}) {
  const live = useMasterReadout(masterMeters, headphoneLevels, active);
  const deepest = Math.max(live.compGrDb, live.limiterGrDb, live.limiterLfeGrDb);
  return (
    <div className={cn("flex w-10 shrink-0 flex-col items-stretch gap-0.5", className)}>
      <span
        className="rounded-[3px] py-px text-center text-[10px] font-medium tabular-nums"
        style={{ backgroundColor: canvasTheme.stripWell, color: canvasTheme.meterWarn }}
        title="Deepest gain reduction across the master chain"
      >
        {deepest >= 0.05 ? `-${deepest.toFixed(1)}` : "0.0"}
      </span>
      <div className="flex min-h-0 flex-1 items-stretch gap-0.5">
        <GrBar label="Cm" db={live.compGrDb} title="Bus compressor" />
        <GrBar label="Lm" db={live.limiterGrDb} title="Limiter, mains" />
        {hasLfe && <GrBar label="Lf" db={live.limiterLfeGrDb} title="Limiter, LFE" />}
      </div>
    </div>
  );
}
