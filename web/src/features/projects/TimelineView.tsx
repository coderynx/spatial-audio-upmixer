import { GripVertical } from "lucide-react";
import * as React from "react";
import { HorizontalFader } from "@/components/ui/horizontal-fader";
import { getStemColor, getStemIcon } from "@/lib/stems";
import { useThemeTokens, withAlpha } from "@/lib/themeTokens";
import { cn } from "@/lib/utils";
import { STEM_GAIN_MAX_DB, STEM_GAIN_MIN_DB, STEM_GAIN_STEP_DB } from "./ChannelStrip";
import type { MeterLevel } from "./useStemPreview";
import type { TrackPeaks } from "./useTrackPeaks";

// Chrome, not an instrument display: follows the app theme via useThemeTokens,
// not canvasTheme. Two canvases: the lane canvas redraws on data/size change;
// the playhead is a separate per-frame overlay positioned from currentTimeRef
// (subscribing to currentTime state would re-render the page 60x/sec).

// Two-line row (name, then M/S/fader) plus a full-height instrument-icon
// swatch, matching iPad Logic's own track-header shape — grown from a
// single 44px line when that shape was adopted (see docs/web_ui_design.md
// §7.2, updated alongside this constant).
export const LANE_HEIGHT = 64;
export const RULER_HEIGHT = 22;
export const HEADER_WIDTH = 280;

const REGION_INSET = 3;
const REGION_RADIUS = 4;
const REGION_LABEL_HEIGHT = 11;
const MIN_TICK_SPACING_PX = 68;
const TICK_STEPS_SECONDS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];

function formatTick(seconds: number) {
  const safe = Math.max(0, seconds || 0);
  const minutes = Math.floor(safe / 60);
  const rest = Math.floor(safe % 60);
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function tickStepFor(duration: number, width: number) {
  const maxTicks = Math.max(1, Math.floor(width / MIN_TICK_SPACING_PX));
  const target = duration / maxTicks;
  return TICK_STEPS_SECONDS.find((step) => step >= target) ?? TICK_STEPS_SECONDS.at(-1)!;
}

export type TimelineViewProps = {
  stems: string[];
  peaks: TrackPeaks | null;
  loading: boolean;
  /** Set when the server is still computing this project's envelopes. */
  pending: boolean;
  /** Base stem names that are currently silent — muted outright, or implicitly
   * muted because a different stem is soloed. Drives waveform dimming only;
   * the M/S buttons below reflect explicit `enabled`/`solo` state instead, so
   * a stem silenced only by someone else's solo doesn't show as muted. */
  mutedStems: string[];
  /** Explicit per-stem mute, keyed same as `stem_enabled` — matches the stem
   * rail's own `StemRow` exactly, same manifest field. */
  enabled: Record<string, boolean>;
  solo: string[];
  onToggleMute: (stem: string) => void;
  onToggleSolo: (stem: string) => void;
  /** Per-stem program gain, same manifest field and range as the mixer
   * strip's vertical fader and the inspector's copy of it (`ChannelStrip.tsx`)
   * — a third home for the one value, not a parallel control (see
   * docs/web_ui_design.md §6.4, updated alongside this addition). */
  gains: Record<string, number>;
  onGain: (stem: string, value: number) => void;
  /** Live per-channel level, keyed same as `useStemPreview`'s `stemLevels` —
   * feeds the inline fader's live-level bars (`HorizontalFader`'s
   * `meterSource`), same ref the mixer strip's meter already reads. */
  stemLevels: React.MutableRefObject<Map<string, MeterLevel[]>>;
  /** Real source channel count per stem, same map `ChannelStrip`'s meter
   * uses — how many level bars the inline fader draws (capped at 2). */
  stemChannelCounts: Record<string, number>;
  /** Reorder drag state, shared with the stem rail's `StemRow` list so
   * dragging a lane in the timeline reorders the same `orderedStems` the rail
   * shows — one order, two views of it. */
  draggedStem: string | null;
  onDragStart: (stem: string) => void;
  onDragEnd: () => void;
  onDropOn: (stem: string) => void;
  selectedStem: string | null;
  onSelectStem: (stem: string) => void;
  duration: number;
  currentTime: number;
  currentTimeRef: React.MutableRefObject<number>;
  playing: boolean;
  disabled: boolean;
  onBeginScrub: () => void;
  onScrubTo: (value: number) => void;
  onCommitScrub: (value: number) => void;
  className?: string;
  style?: React.CSSProperties;
};

function TimelineViewImpl({
  stems,
  peaks,
  loading,
  pending,
  mutedStems,
  enabled,
  solo,
  onToggleMute,
  onToggleSolo,
  gains,
  onGain,
  stemLevels,
  stemChannelCounts,
  draggedStem,
  onDragStart,
  onDragEnd,
  onDropOn,
  selectedStem,
  onSelectStem,
  duration,
  currentTime,
  currentTimeRef,
  playing,
  disabled,
  onBeginScrub,
  onScrubTo,
  onCommitScrub,
  className,
  style,
}: TimelineViewProps) {
  const laneAreaRef = React.useRef<HTMLDivElement>(null);
  const waveCanvas = React.useRef<HTMLCanvasElement>(null);
  const playheadCanvas = React.useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = React.useState(0);
  const scrubbing = React.useRef(false);
  // Native HTML5 drag fires `dragstart` with `event.target` set to the
  // draggable node itself (the row), never the descendant actually under the
  // pointer — so gating the drag source on `dragstart`'s own target can never
  // see which part of the row a mousedown landed on. `mousedown` fires with
  // the real hit-tested target first, so it — not `dragstart` — is where
  // "did this gesture start on the grip handle" has to be decided; only one
  // row can be mid-gesture at a time, so a single ref is enough.
  const dragFromHandle = React.useRef(false);
  const tokens = useThemeTokens();

  const mutedKey = mutedStems.join(" ");
  const stemsKey = stems.join(" ");
  const laneHeight = RULER_HEIGHT + stems.length * LANE_HEIGHT;

  // One stable getter per stem, keyed off `stemsKey` rather than `stems`
  // itself — `stems` is a fresh array every render (see the waveform effect
  // below), and a fresh closure per render would tear down and restart each
  // row's meter rAF loop on every render instead of just when the stem list
  // actually changes.
  const meterSources = React.useMemo(() => {
    const map: Record<string, () => MeterLevel[]> = {};
    for (const stem of stems) map[stem] = () => stemLevels.current.get(stem) ?? [];
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on stemsKey, same reasoning as the waveform effect below
  }, [stemsKey, stemLevels]);

  React.useEffect(() => {
    const node = laneAreaRef.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Lanes, regions and waveforms. Redrawn only on a real change, never per
  // frame.
  React.useEffect(() => {
    const canvas = waveCanvas.current;
    if (!canvas || width <= 0) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(laneHeight * ratio);
    canvas.style.height = `${laneHeight}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, laneHeight);

    ctx.fillStyle = tokens.muted;
    ctx.fillRect(0, 0, width, RULER_HEIGHT);
    ctx.fillStyle = tokens.background;
    ctx.fillRect(0, RULER_HEIGHT, width, laneHeight - RULER_HEIGHT);

    if (duration > 0) {
      const step = tickStepFor(duration, width);
      ctx.font = "500 10px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      for (let at = 0; at <= duration; at += step) {
        const x = Math.round((at / duration) * width) + 0.5;
        ctx.fillStyle = withAlpha(tokens.border, 0.7);
        ctx.fillRect(x, RULER_HEIGHT, 1, laneHeight - RULER_HEIGHT);
        ctx.fillRect(x, RULER_HEIGHT - 6, 1, 6);
        if (at > 0) {
          ctx.fillStyle = tokens["muted-foreground"];
          ctx.fillText(formatTick(at), x + 4, RULER_HEIGHT / 2);
        }
      }
    }
    ctx.fillStyle = tokens.border;
    ctx.fillRect(0, RULER_HEIGHT - 1, width, 1);

    const muted = new Set(mutedStems);
    stems.forEach((stem, index) => {
      const laneTop = RULER_HEIGHT + index * LANE_HEIGHT;
      ctx.fillStyle = tokens.border;
      ctx.fillRect(0, laneTop + LANE_HEIGHT - 1, width, 1);

      const envelope = peaks?.stems.get(stem);
      if (!envelope) return;
      const isMuted = muted.has(stem);
      const dimmed = Boolean(selectedStem) && selectedStem !== stem;
      const color = getStemColor(stem);
      const regionTop = laneTop + REGION_INSET;
      const regionHeight = LANE_HEIGHT - REGION_INSET * 2 - 1;

      // Logic draws the arrangement as colour-coded regions with the waveform
      // inside, not as a bare trace on the lane background — the block is
      // what makes a dozen stacked lanes scannable.
      ctx.save();
      ctx.globalAlpha = isMuted ? 0.3 : dimmed ? 0.55 : 1;
      ctx.beginPath();
      ctx.roundRect(0, regionTop, width, regionHeight, REGION_RADIUS);
      ctx.fillStyle = tokens.card;
      ctx.fill();
      ctx.fillStyle = `${color}38`;
      ctx.fill();
      ctx.strokeStyle = `${color}99`;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.clip();

      ctx.fillStyle = `${color}2E`;
      ctx.fillRect(0, regionTop, width, REGION_LABEL_HEIGHT);
      ctx.font = "600 9px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillStyle = tokens.foreground;
      ctx.globalAlpha *= 0.75;
      ctx.fillText(stem, 5, regionTop + REGION_LABEL_HEIGHT / 2 + 0.5);
      ctx.globalAlpha = isMuted ? 0.3 : dimmed ? 0.55 : 1;

      const waveTop = regionTop + REGION_LABEL_HEIGHT;
      const waveHeight = regionHeight - REGION_LABEL_HEIGHT;
      const midline = waveTop + waveHeight / 2;
      const halfHeight = waveHeight / 2 - 1;
      ctx.fillStyle = color;
      // One column per CSS pixel, each taking the extremes of every envelope
      // bin inside it — point-sampling a downsampled envelope would drop
      // exactly the transients a waveform exists to show.
      const columns = Math.max(1, Math.round(width));
      const perColumn = envelope.min.length / columns;
      for (let column = 0; column < columns; column += 1) {
        const from = Math.floor(column * perColumn);
        const to = Math.max(from + 1, Math.floor((column + 1) * perColumn));
        let low = 0;
        let high = 0;
        for (let bin = from; bin < to && bin < envelope.min.length; bin += 1) {
          if (envelope.min[bin] < low) low = envelope.min[bin];
          if (envelope.max[bin] > high) high = envelope.max[bin];
        }
        const topY = midline - (high / 127) * halfHeight;
        const bottomY = midline - (low / 127) * halfHeight;
        ctx.fillRect(column, topY, 1, Math.max(1, bottomY - topY));
      }
      ctx.restore();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `stems`/`mutedStems` are fresh arrays every render; the joined keys stand in for their contents so a redraw only happens on a real change
  }, [duration, laneHeight, mutedKey, peaks, selectedStem, stemsKey, tokens, width]);

  const drawPlayhead = React.useCallback((at: number) => {
    const canvas = playheadCanvas.current;
    if (!canvas || width <= 0) return;
    const ratio = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(laneHeight * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(laneHeight * ratio);
      canvas.style.height = `${laneHeight}px`;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, laneHeight);
    if (duration <= 0) return;
    const x = Math.round((Math.min(at, duration) / duration) * width) + 0.5;
    ctx.fillStyle = tokens.foreground;
    ctx.fillRect(x, 0, 1, laneHeight);
    ctx.beginPath();
    ctx.moveTo(x - 4.5, 0);
    ctx.lineTo(x + 4.5, 0);
    ctx.lineTo(x, 7);
    ctx.closePath();
    ctx.fill();
  }, [duration, laneHeight, tokens.foreground, width]);

  React.useEffect(() => {
    if (!playing) {
      drawPlayhead(currentTime);
      return;
    }
    let frame: number;
    const loop = () => {
      drawPlayhead(currentTimeRef.current);
      frame = window.requestAnimationFrame(loop);
    };
    frame = window.requestAnimationFrame(loop);
    return () => window.cancelAnimationFrame(frame);
  }, [currentTime, currentTimeRef, drawPlayhead, playing]);

  const timeForClientX = (clientX: number) => {
    const node = laneAreaRef.current;
    if (!node || duration <= 0) return 0;
    const rect = node.getBoundingClientRect();
    const fraction = (clientX - rect.left) / Math.max(1, rect.width);
    return Math.min(duration, Math.max(0, fraction * duration));
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (disabled || event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    scrubbing.current = true;
    onBeginScrub();
    onScrubTo(timeForClientX(event.clientX));
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!scrubbing.current) return;
    onScrubTo(timeForClientX(event.clientX));
  };

  const endScrub = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!scrubbing.current) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    scrubbing.current = false;
    onCommitScrub(timeForClientX(event.clientX));
  };

  const seekTo = (target: number) => {
    const clamped = Math.min(duration, Math.max(0, target));
    onBeginScrub();
    onScrubTo(clamped);
    onCommitScrub(clamped);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (disabled || duration <= 0) return;
    const moves: Record<string, number> = {
      ArrowRight: 1, ArrowLeft: -1, PageUp: 10, PageDown: -10,
    };
    if (event.key in moves) {
      event.preventDefault();
      seekTo(currentTime + moves[event.key]);
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      seekTo(0);
    } else if (event.key === "End") {
      event.preventDefault();
      seekTo(duration);
    }
  };

  const waveformsMissing = !peaks && !loading;

  return (
    <div className={cn("relative flex min-h-0 flex-col overflow-auto bg-background", className)} style={style}>
      <div className="flex min-w-0 flex-1">
        <div className="sticky left-0 z-10 shrink-0 border-r bg-card" style={{ width: HEADER_WIDTH }}>
          <div className="border-b bg-muted" style={{ height: RULER_HEIGHT }} />
          {stems.map((stem) => {
            const StemIcon = getStemIcon(stem);
            const isMuted = enabled[stem] === false;
            const isSoloed = solo.includes(stem);
            const isDragging = draggedStem === stem;
            return (
              <div
                key={stem}
                draggable
                onMouseDown={(event) => {
                  dragFromHandle.current = Boolean((event.target as HTMLElement).closest("[data-drag-handle]"));
                }}
                onDragStart={(event) => {
                  // Restricted to the grip icon: native drag would otherwise steal
                  // events from the fader's own pointermove-based dragging. Gated on
                  // dragFromHandle since native drag always sets event.target to the
                  // row itself, never the descendant the pointer landed on.
                  if (!dragFromHandle.current) { event.preventDefault(); return; }
                  event.dataTransfer.effectAllowed = "move"; onDragStart(stem);
                }}
                onDragEnd={onDragEnd}
                onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "move"; }}
                onDrop={(event) => { event.preventDefault(); onDropOn(stem); }}
                onClick={() => onSelectStem(stem)}
                title={stem}
                className={cn(
                  "flex cursor-pointer items-stretch gap-2 border-b border-l-[3px] px-2 py-2 transition-colors",
                  selectedStem === stem ? "bg-primary/15" : "hover:bg-accent/50",
                  isDragging && "opacity-40",
                )}
                style={{ height: LANE_HEIGHT, borderLeftColor: getStemColor(stem) }}
              >
                <GripVertical data-drag-handle className="h-3.5 w-3.5 shrink-0 cursor-grab self-center text-muted-foreground/60" aria-hidden="true" />
                {/* Name on its own line, M/S/gain below — the two-line shape
                    iPad Logic's own track header uses, wide enough now that
                    the row no longer has to fit everything on one line. */}
                <div className="flex min-w-0 flex-1 flex-col justify-center gap-1.5">
                  <span
                    className={cn(
                      "truncate text-[11px] font-medium",
                      isMuted ? "text-muted-foreground line-through" : selectedStem === stem ? "text-primary" : "text-foreground",
                    )}
                  >
                    {stem}
                  </span>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      aria-pressed={isMuted}
                      aria-label={`${isMuted ? "Enable" : "Mute"} ${stem}`}
                      onClick={(event) => { event.stopPropagation(); onToggleMute(stem); }}
                      className={cn(
                        "flex h-6 w-7 shrink-0 items-center justify-center rounded-[5px] text-[10px] font-bold leading-none transition-colors",
                        isMuted ? "bg-destructive text-destructive-foreground" : "bg-secondary text-muted-foreground hover:bg-accent hover:text-foreground",
                      )}
                    >
                      <span className="leading-none">M</span>
                    </button>
                    <button
                      type="button"
                      aria-pressed={isSoloed}
                      aria-label={`${isSoloed ? "Clear solo" : "Solo"} ${stem}`}
                      onClick={(event) => { event.stopPropagation(); onToggleSolo(stem); }}
                      className={cn(
                        "flex h-6 w-7 shrink-0 items-center justify-center rounded-[5px] text-[10px] font-bold leading-none transition-colors",
                        isSoloed ? "bg-warning text-warning-foreground" : "bg-secondary text-muted-foreground hover:bg-accent hover:text-foreground",
                      )}
                    >
                      <span className="leading-none">S</span>
                    </button>
                    {/* Dragging this also selects the row (the click bubbles
                        to the row's own onClick, same as clicking anywhere
                        else on it) — adjusting a stem's gain while it becomes
                        the selected stem is the expected outcome, not a side
                        effect to suppress, unlike M/S's quick toggle above.
                        The live-level bars are the stem's actual playback
                        level (`stemLevels`), independent of the knob's own
                        gain value — see `horizontal-fader.tsx`. */}
                    <HorizontalFader
                      label={`${stem} gain`}
                      value={gains[stem] ?? 0}
                      min={STEM_GAIN_MIN_DB}
                      max={STEM_GAIN_MAX_DB}
                      step={STEM_GAIN_STEP_DB}
                      valueText={`${(gains[stem] ?? 0).toFixed(1)} dB`}
                      onChange={(next) => onGain(stem, next)}
                      onReset={() => onGain(stem, 0)}
                      knobSize={20}
                      meterChannels={Math.min(2, Math.max(1, stemChannelCounts[stem] ?? 1))}
                      meterSource={meterSources[stem]}
                      meterActive={playing}
                      className="min-w-0 flex-1"
                    />
                  </div>
                </div>
                {/* Bare instrument icon, trailing edge — the same per-stem
                    icon this app already draws small inline before the name
                    (`getStemIcon`), sized up and given room on its own,
                    matching iPad Logic's plain (no swatch/background) icon
                    treatment for this row. */}
                <div className="flex shrink-0 items-center self-center pr-1">
                  <StemIcon
                    className={cn("h-6 w-6 shrink-0", isMuted && "opacity-30")}
                    style={{ color: getStemColor(stem) }}
                    aria-hidden="true"
                  />
                </div>
              </div>
            );
          })}
        </div>
        <div
          ref={laneAreaRef}
          role="slider"
          aria-label="Timeline position"
          aria-valuemin={0}
          aria-valuemax={Math.max(duration, 0)}
          aria-valuenow={Math.min(currentTime, duration || 0)}
          aria-valuetext={`${formatTick(currentTime)} of ${formatTick(duration)}`}
          aria-orientation="horizontal"
          aria-disabled={disabled || undefined}
          tabIndex={disabled ? -1 : 0}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endScrub}
          onPointerCancel={endScrub}
          onKeyDown={handleKeyDown}
          className={cn(
            "relative min-w-0 flex-1 touch-none outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60",
            disabled ? "pointer-events-none opacity-50" : "cursor-text",
          )}
        >
          <canvas ref={waveCanvas} className="absolute left-0 top-0 w-full" aria-hidden="true" />
          <canvas ref={playheadCanvas} className="pointer-events-none absolute left-0 top-0 w-full" aria-hidden="true" />
        </div>
      </div>
      {(loading || pending || waveformsMissing) && (
        <div className="pointer-events-none absolute inset-x-0 top-1/2 flex -translate-y-1/2 justify-center">
          <span className="rounded-md border bg-card/90 px-2 py-1 text-[11px] text-muted-foreground shadow-sm">
            {pending || loading
              ? "Preparing waveforms…"
              : "Waveforms unavailable for this project."}
          </span>
        </div>
      )}
    </div>
  );
}

export const TimelineView = React.memo(TimelineViewImpl);
