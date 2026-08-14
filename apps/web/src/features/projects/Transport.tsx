import * as React from "react";
import { Pause, Play, Repeat, Square, Volume2, VolumeX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HorizontalFader } from "@/components/ui/horizontal-fader";
import { canvasTheme } from "@/lib/canvasTheme";
import { formatFaderDb } from "@/lib/fader";
import { cn } from "@/lib/utils";
import type { MeterLevel } from "./useStemPreview";

function digits(seconds: number) {
  const clamped = Math.max(0, seconds || 0);
  const minutes = Math.floor(clamped / 60);
  const whole = Math.floor(clamped % 60);
  const tenths = Math.floor((clamped - Math.floor(clamped)) * 10);
  return `${String(minutes).padStart(2, "0")}:${String(whole).padStart(2, "0")}.${tenths}`;
}

function shortDigits(seconds: number) {
  const clamped = Math.max(0, seconds || 0);
  const minutes = Math.floor(clamped / 60);
  const whole = Math.floor(clamped % 60);
  return `${String(minutes).padStart(2, "0")}:${String(whole).padStart(2, "0")}`;
}

function LcdDisplay({ currentTime, duration, mode, onToggleMode }: { currentTime: number; duration: number; mode: "elapsed" | "remaining"; onToggleMode: () => void }) {
  const remaining = Math.max(0, duration - currentTime);
  const value = mode === "elapsed" ? digits(currentTime) : `-${digits(remaining)}`;
  return (
    <div
      className="flex h-10 shrink-0 items-stretch rounded-md border shadow-[inset_0_2px_5px_rgba(0,0,0,0.7)]"
      style={{ backgroundColor: canvasTheme.plotField, borderColor: canvasTheme.gridSoft }}
    >
      <button
        type="button"
        onClick={onToggleMode}
        aria-label={`Time display, showing ${mode === "elapsed" ? "elapsed" : "remaining"} time. Click to toggle.`}
        title="Click to toggle elapsed / remaining"
        className="group flex w-[88px] shrink-0 flex-col items-center justify-center px-1"
      >
        <span
          className="w-full whitespace-nowrap text-center font-mono text-base font-medium leading-tight tabular-nums"
          style={{ color: canvasTheme.labelStrong }}
        >
          {value}
        </span>
        <span
          className="w-full whitespace-nowrap text-center text-[8px] font-semibold uppercase leading-tight tracking-[0.2em] opacity-70 group-hover:opacity-100"
          style={{ color: canvasTheme.label }}
        >
          {mode === "elapsed" ? "Elapsed" : "Remaining"}
        </span>
      </button>
      <div className="w-px shrink-0 self-stretch" style={{ backgroundColor: canvasTheme.gridSoft }} />
      <div className="flex shrink-0 items-center px-1" title="Total duration">
        <span
          className="whitespace-nowrap text-center font-mono text-[10px] font-medium leading-none tabular-nums"
          style={{ color: canvasTheme.label }}
        >
          {shortDigits(duration)}
        </span>
      </div>
    </div>
  );
}

function TransportImpl({
  playing,
  currentTime,
  currentTimeRef,
  duration,
  volume,
  muted,
  loop,
  disabled,
  onPlayPause,
  onStop,
  onToggleLoop,
  onSetVolume,
  onToggleMute,
  headphoneLevels,
  children,
  leading,
}: {
  playing: boolean;
  currentTime: number;
  currentTimeRef: React.MutableRefObject<number>;
  duration: number;
  volume: number;
  muted: boolean;
  loop: boolean;
  disabled: boolean;
  onPlayPause: () => void;
  onStop: () => void;
  onToggleLoop: () => void;
  onSetVolume: (value: number) => void;
  onToggleMute: () => void;
  /** Live L/R signal reaching the monitor output — drives the volume
   * fader's live-level bars (`HorizontalFader`'s `meterSource`), independent
   * of the fader's own value the same way every other meter in the app is. */
  headphoneLevels: React.MutableRefObject<{ left: MeterLevel; right: MeterLevel }>;
  // Extra controls (e.g. the output-mode picker) rendered in the same card,
  // after the volume control, so the whole row shares the card's full width.
  children?: React.ReactNode;
  /** Column 1 content — the page's own stage tabs/settings, merged into this
   * bar rather than a separate `Toolbar` row above it. Left-aligned, not
   * centred: col 1 and col 3 are equal `1fr` shares, which is what keeps the
   * transport pod in col 2 sitting at the row's true centre regardless of
   * how much (or little) sits in col 1 — the same reasoning that used to
   * keep col 1 deliberately empty, just no longer requiring it to be. */
  leading?: React.ReactNode;
}) {
  const [mode, setMode] = React.useState<"elapsed" | "remaining">("elapsed");
  // Stable identity (headphoneLevels is a ref, not state) — the volume
  // fader's own rAF loop calls this every frame, so a fresh function here
  // would tear down and restart that loop on every render.
  const meterSource = React.useCallback(
    () => [headphoneLevels.current.left, headphoneLevels.current.right],
    [headphoneLevels],
  );
  // While playing, `currentTime` (React state on the shared preview hook) is
  // deliberately not updated every frame — that used to re-render the whole
  // page ~60x/sec. Instead this component polls `currentTimeRef` in its own
  // small rAF loop, scoping the per-frame re-render to just the LCD/slider.
  // Paused/idle, `currentTime` state is authoritative and already correct.
  const [liveTime, setLiveTime] = React.useState(currentTime);
  React.useEffect(() => {
    if (!playing) return;
    let frame: number;
    const loop = () => {
      setLiveTime(currentTimeRef.current);
      frame = window.requestAnimationFrame(loop);
    };
    frame = window.requestAnimationFrame(loop);
    return () => window.cancelAnimationFrame(frame);
  }, [playing, currentTimeRef]);
  const displayTime = playing ? liveTime : currentTime;
  return (
    // Three-column true centring, see docs/web_ui_design.md §6.6.
    <div className="grid h-18 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 border-b bg-card px-2 py-4">
      <div className="flex min-w-0 items-center gap-2 justify-self-start overflow-hidden">{leading}</div>
      <div className="flex items-center gap-2 justify-self-center">
        {/* Sized up from the app's ordinary h-7 icon button (§6) on purpose:
            transport is the control the user's hand returns to constantly
            during a mix, and Apple gives its own transport clusters the same
            emphasis over surrounding utility icons (mute/volume stay at the
            ordinary size below). Scoped to this cluster via className, not a
            change to Button's shared `icon` size. */}
        <div className="flex items-center gap-1.5 rounded-lg bg-muted p-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-foreground hover:bg-accent hover:text-foreground [&_svg]:size-4"
            aria-label="Stop"
            disabled={disabled}
            onClick={onStop}
          >
            <Square className="fill-current" />
          </Button>
          <Button
            variant={playing ? "success" : "secondary"}
            size="icon"
            className="h-8 w-8 [&_svg]:size-4"
            aria-label={playing ? "Pause" : "Play"}
            aria-pressed={playing}
            disabled={disabled}
            onClick={onPlayPause}
          >
            {playing ? <Pause className="fill-current" /> : <Play className="fill-current" />}
          </Button>
          <Button
            variant={loop ? "warning" : "ghost"}
            size="icon"
            className={cn("h-8 w-8 [&_svg]:size-4", !loop && "text-foreground hover:bg-accent hover:text-foreground")}
            aria-label="Toggle repeat"
            aria-pressed={loop}
            disabled={disabled}
            onClick={onToggleLoop}
          >
            <Repeat />
          </Button>
        </div>
        <LcdDisplay currentTime={displayTime} duration={duration} mode={mode} onToggleMode={() => setMode((current) => (current === "elapsed" ? "remaining" : "elapsed"))} />
      </div>
      {/* No seek bar here: the timeline pane's playhead is the transport
          position control, and two scrub surfaces for one value is exactly
          the duplication the design spec's "one control per idea" rule
          rejects. */}
      {/* Packed as one block anchored to col 3's right edge, same as the
          transport cluster and the rest of the app's control clusters —
          mute/fader/dB stay glued to the output-mode picker rather than
          drifting to the track's left edge with a dead gap in between.
          Nothing here changes width across output modes (see
          OutputModeSelect's own reserved-width trigger and device slot), so
          this block's total width — and therefore every control's pixel
          position — stays constant when the mode changes. */}
      <div className="flex shrink-0 items-center gap-2 justify-self-end">
        {/* Matches the transport cluster's h-8 bump (see above) — volume and
            output mode are read and touched just as constantly while a
            preview is playing, so they get the same emphasis rather than
            reading as an afterthought next to it. */}
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 [&_svg]:size-4"
          aria-label={muted ? "Unmute" : "Mute"}
          aria-pressed={muted}
          disabled={disabled}
          onClick={onToggleMute}
        >
          {muted ? <VolumeX className="text-destructive" /> : <Volume2 />}
        </Button>
        {/* Logic-iPad-style horizontal fader (components/ui/
            horizontal-fader.tsx), sized up from an ordinary inline slider —
            monitor volume is read and adjusted constantly while a preview
            plays, so it gets a wider track for finer drag resolution instead
            of the cramped native <input type=range> this replaces. The two
            live-level bars are the actual L/R signal reaching the monitor,
            independent of the knob's own gain position — see
            `horizontal-fader.tsx`'s doc comment. */}
        <HorizontalFader
          label="Preview monitor volume"
          value={volume}
          min={0}
          max={1}
          step={0.01}
          onChange={onSetVolume}
          onReset={() => onSetVolume(1)}
          valueText={formatFaderDb(volume)}
          knobSize={18}
          meterChannels={2}
          meterSource={meterSource}
          meterActive={playing}
          className="w-32"
        />
        {/* dB-tapered monitor gain readout (lib/fader.ts) — unity (0.0 dB) at
            max is the render itself; there is no gain above it to give up
            reading. See useStemPreview.ts's PROGRAM/MONITOR gain split. */}
        <span className="w-11 shrink-0 text-right text-[10px] font-medium tabular-nums text-muted-foreground" aria-hidden="true">
          {formatFaderDb(volume)}
        </span>
        {children}
      </div>
    </div>
  );
}

export const Transport = React.memo(TransportImpl);
