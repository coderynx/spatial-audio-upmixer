import * as React from "react";
import { Fader } from "@/components/ui/fader";
import { canvasTheme } from "@/lib/canvasTheme";
import { getStemColor, getStemIcon } from "@/lib/stems";
import { cn } from "@/lib/utils";
import { StripMeter, stripMeterWidth } from "./StripMeter";
import { useStripMeterLoop } from "./useStripMeterLoop";
import type { MeterLevel } from "./useStemPreview";

// A stem's channel strip — nameplate, readouts, fader beside its meter,
// mute/solo, state line — shared verbatim by `MixerView`'s rack and the
// inspector's always-accessible copy of the selected stem (see
// `ProjectDetailPage.tsx`'s "Stem" group), so the two are provably the same
// control in two places rather than a copy that can drift from the original.

export const FADER_TRAVEL = 148;
export const FADER_WIDTH = 34;

/** Per-stem program gain, matching `mixing.stem_rebalance`'s range and the
 * inspector's gain slider — the two controls write the same manifest field.
 * Distinct from the master strip's monitor taper (`lib/fader.ts`), which
 * spans -60 dB to unity and never reaches the export. */
export const STEM_GAIN_MIN_DB = -12;
export const STEM_GAIN_MAX_DB = 6;
export const STEM_GAIN_STEP_DB = 0.1;
export const STEM_GAIN_TICKS = [6, 3, 0, -3, -6, -9, -12];

export function stripWidth(channels: number) {
  return FADER_WIDTH + stripMeterWidth(channels) + 20;
}

export const STRIP_EXTRA_MIN = 0;
export const STRIP_EXTRA_MAX = 96;

/** The drag target on a strip's trailing border — the line already
 * separating it from its neighbour — that widens or narrows *that one
 * strip*. Each strip in the rack resizes independently, the way dragging a
 * column border in a spreadsheet only moves that column. No separate grip
 * is drawn; the shared border itself is the handle, highlighted on
 * hover/focus so it's discoverable without adding a floating element.
 * Shared by every strip kind (stem, anchor, master) so the drag math can't
 * drift between them.
 *
 * `onChange` fires continuously while dragging, for the live width; `onCommit`
 * fires once at the end of the gesture, for the caller to persist. Requires a
 * `position: relative` ancestor to anchor against — every strip's root
 * carries `relative` for exactly this. Stops click/pointerdown propagation so
 * a resize drag never also selects the strip underneath it. */
export function StripResizeHandle({
  label, value, onChange, onCommit,
}: {
  label: string;
  value: number;
  onChange: (px: number) => void;
  onCommit: (px: number) => void;
}) {
  const drag = React.useRef<{ startX: number; startValue: number } | null>(null);
  const commit = (px: number) => Math.round(Math.min(STRIP_EXTRA_MAX, Math.max(STRIP_EXTRA_MIN, px)));

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.stopPropagation();
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = { startX: event.clientX, startValue: value };
  };
  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (!state) return;
    onChange(commit(state.startValue + (event.clientX - state.startX)));
  };
  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    drag.current = null;
    onCommit(value);
  };
  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, number> = { ArrowRight: 8, ArrowLeft: -8, PageUp: 24, PageDown: -24 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = commit(value + moves[event.key]);
    onChange(next);
    onCommit(next);
  };

  return (
    <div
      role="slider"
      aria-label={label}
      aria-orientation="horizontal"
      aria-valuemin={STRIP_EXTRA_MIN}
      aria-valuemax={STRIP_EXTRA_MAX}
      aria-valuenow={value}
      tabIndex={0}
      title="Drag this border to resize the strip"
      onClick={(event) => event.stopPropagation()}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={handleKeyDown}
      onDoubleClick={(event) => { event.stopPropagation(); onChange(0); onCommit(0); }}
      className="absolute inset-y-0 right-[-4px] z-10 w-2 shrink-0 cursor-col-resize touch-none outline-none"
    />
  );
}

/** The two numeric readouts Logic prints above a strip's fader: the fader's
 * own value, and the meter's held peak in warning yellow. */
export function StripReadouts({ value, peakDb }: { value: string; peakDb: number }) {
  return (
    <div className="flex w-full items-stretch gap-1">
      <span
        className="flex-1 rounded-[3px] py-px text-center text-[10px] font-medium tabular-nums"
        style={{ backgroundColor: canvasTheme.stripWell, color: canvasTheme.labelStrong }}
      >
        {value}
      </span>
      <span
        className="flex-1 rounded-[3px] py-px text-center text-[10px] font-medium tabular-nums"
        style={{ backgroundColor: canvasTheme.stripWell, color: canvasTheme.meterWarn }}
        title="Highest level reached"
      >
        {peakDb <= -60 ? "-∞" : peakDb.toFixed(1)}
      </span>
    </div>
  );
}

export function StripToggle({
  letter, active, label, onClick,
}: { letter: "M" | "S"; active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={label}
      onClick={onClick}
      className={cn(
        "flex h-6 flex-1 items-center justify-center rounded-[5px] text-[11px] font-bold leading-none transition-colors",
        active
          ? letter === "M" ? "bg-destructive text-destructive-foreground" : "bg-warning text-warning-foreground"
          : "text-muted-foreground hover:bg-accent",
      )}
      style={active ? undefined : { backgroundColor: canvasTheme.stripWell }}
    >
      <span className="leading-none">{letter}</span>
    </button>
  );
}

export type StemChannelStripProps = {
  stem: string;
  /** Raw source channel count — clamped to the 1-2 bars a strip can show. */
  channels: number;
  gain: number;
  onGain: (value: number) => void;
  /** Explicit `stem_enabled === false` — drives the M button and the fader's
   * disabled look. */
  muted: boolean;
  soloed: boolean;
  /** `muted`, or silenced because a different stem is soloed — drives the
   * meter's muted well and the state line. Colour alone can't carry this
   * distinction (design spec §8), so it also gets its own word. */
  silent: boolean;
  onToggleMute: () => void;
  onToggleSolo: () => void;
  meterSource: () => MeterLevel[];
  active: boolean;
  /** Omit where the strip has nothing to select into — the inspector's copy
   * is already showing the selected stem, so its nameplate is a label, not a
   * button. */
  selected?: boolean;
  onSelect?: () => void;
  disabled?: boolean;
  /** What the fader and M/S buttons call this strip in their accessible
   * names — defaults to `stem`. The mixer rack holds one strip per stem, so
   * `stem` is already unique there; the inspector's copy is a second strip
   * for the *same* stem rendered elsewhere on the page at the same time, and
   * two controls sharing one accessible name is exactly the query ambiguity
   * design spec §8 rules out. Pass e.g. "Selected stem" to keep the name
   * unique without it reading as a duplicate of the rack's own strip. */
  subjectName?: string;
  /** Hides the strip's own nameplate row — for the inspector's copy, whose
   * container already carries a stem title above the whole section (see
   * `ProjectDetailPage.tsx`'s "Stem" group), so repeating it here would be a
   * second, redundant header for the same value one scroll away from the
   * first. */
  showNameplate?: boolean;
  /** Extra pixels added on top of the computed minimum width — this strip's
   * own independent resize state, dragged via `StripResizeHandle`. */
  extraWidth?: number;
  /** Omit both to render without a resize handle at all — the inspector's
   * lone, centered copy of the selected stem has no row of neighbours to
   * widen at their expense, so resizing it buys nothing a container width
   * doesn't already give it. */
  onExtraWidthChange?: (px: number) => void;
  onExtraWidthCommit?: (px: number) => void;
  className?: string;
  style?: React.CSSProperties;
};

export function StemChannelStrip({
  stem,
  channels,
  gain,
  onGain,
  muted,
  soloed,
  silent,
  onToggleMute,
  onToggleSolo,
  meterSource,
  active,
  selected = false,
  onSelect,
  disabled = false,
  subjectName = stem,
  showNameplate = true,
  extraWidth = 0,
  onExtraWidthChange,
  onExtraWidthCommit,
  className,
  style,
}: StemChannelStripProps) {
  const StemIcon = getStemIcon(stem);
  const meterChannels = Math.min(2, Math.max(1, channels));
  const { register, peakDb } = useStripMeterLoop(meterSource, silent, active);

  const nameplateClassName = cn(
    "flex w-full items-center gap-1 rounded-sm border-b-2 px-0.5 pb-1 text-[11px] font-medium transition-colors",
    selected ? "text-primary" : "text-muted-foreground",
    onSelect && !selected && "hover:text-foreground",
  );
  const nameplateStyle = { borderBottomColor: silent ? "transparent" : getStemColor(stem) };
  const nameplateContent = (
    <>
      <StemIcon
        className={cn("h-3 w-3 shrink-0", silent && "opacity-40")}
        style={{ color: getStemColor(stem) }}
        aria-hidden="true"
      />
      <span className="min-w-0 flex-1 truncate text-left">{stem}</span>
    </>
  );

  return (
    <div
      className={cn(
        "relative flex shrink-0 flex-col items-center gap-1.5 transition-colors",
        onSelect && !selected && "cursor-pointer",
        className,
      )}
      style={{ width: stripWidth(meterChannels) + extraWidth, ...style }}
      onClick={onSelect}
    >
      {onExtraWidthChange && onExtraWidthCommit && (
        <StripResizeHandle
          label={`Resize ${subjectName} strip`}
          value={extraWidth}
          onChange={onExtraWidthChange}
          onCommit={onExtraWidthCommit}
        />
      )}
      {showNameplate && (onSelect ? (
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          title={`${stem} — ${meterChannels === 2 ? "stereo" : "mono"}`}
          className={nameplateClassName}
          style={nameplateStyle}
        >
          {nameplateContent}
        </button>
      ) : (
        <div className={nameplateClassName} style={nameplateStyle} title={`${stem} — ${meterChannels === 2 ? "stereo" : "mono"}`}>
          {nameplateContent}
        </div>
      ))}

      <StripReadouts value={gain > 0 ? `+${gain.toFixed(1)}` : gain.toFixed(1)} peakDb={peakDb} />

      <div className="flex items-stretch gap-1" style={{ height: FADER_TRAVEL }}>
        <Fader
          label={`${subjectName} gain`}
          value={gain}
          min={STEM_GAIN_MIN_DB}
          max={STEM_GAIN_MAX_DB}
          step={STEM_GAIN_STEP_DB}
          detent={0}
          ticks={STEM_GAIN_TICKS}
          valueText={`${gain.toFixed(1)} dB`}
          onChange={onGain}
          onReset={() => onGain(0)}
          disabled={disabled}
          style={{ width: FADER_WIDTH }}
        />
        <StripMeter channels={meterChannels} ref={register} />
      </div>

      <div className="flex w-full gap-1">
        <StripToggle letter="M" active={muted} label={`${muted ? "Enable" : "Mute"} ${subjectName}`} onClick={onToggleMute} />
        <StripToggle letter="S" active={soloed} label={`${soloed ? "Clear solo" : "Solo"} ${subjectName}`} onClick={onToggleSolo} />
      </div>
      <span className="h-3 text-[9px] uppercase tracking-[.08em] text-destructive">
        {muted ? "Muted" : silent ? "Silent" : ""}
      </span>
    </div>
  );
}
