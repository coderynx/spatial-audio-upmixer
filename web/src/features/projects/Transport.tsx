import * as React from "react";
import { Pause, Play, Repeat, Square, Volume2, VolumeX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { canvasTheme } from "@/lib/canvasTheme";
import { formatFaderDb } from "@/lib/fader";
import { cn } from "@/lib/utils";

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
      className="flex h-9 shrink-0 items-stretch rounded-md border shadow-[inset_0_2px_5px_rgba(0,0,0,0.7)]"
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
  onBeginScrub,
  onScrubTo,
  onCommitScrub,
  children,
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
  onBeginScrub: () => void;
  onScrubTo: (value: number) => void;
  onCommitScrub: (value: number) => void;
  // Extra controls (e.g. the output-mode picker) rendered in the same card,
  // after the volume control, so the whole row shares the card's full width.
  children?: React.ReactNode;
}) {
  const [mode, setMode] = React.useState<"elapsed" | "remaining">("elapsed");
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
    <div className="flex h-12 shrink-0 items-center gap-2 border-b bg-card px-2">
      <div className="flex items-center gap-1.5 rounded-lg bg-muted p-1">
        <Button
          variant="ghost"
          size="icon"
          className="text-foreground hover:bg-accent hover:text-foreground"
          aria-label="Stop"
          disabled={disabled}
          onClick={onStop}
        >
          <Square className="fill-current" />
        </Button>
        <Button
          variant={playing ? "success" : "secondary"}
          size="icon"
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
          className={cn(!loop && "text-foreground hover:bg-accent hover:text-foreground")}
          aria-label="Toggle repeat"
          aria-pressed={loop}
          disabled={disabled}
          onClick={onToggleLoop}
        >
          <Repeat />
        </Button>
      </div>
      <LcdDisplay currentTime={displayTime} duration={duration} mode={mode} onToggleMode={() => setMode((current) => (current === "elapsed" ? "remaining" : "elapsed"))} />
      <input
        aria-label="Preview position"
        className={cn("h-1 min-w-0 flex-1 accent-primary", disabled && "opacity-40")}
        type="range"
        min={0}
        max={Math.max(duration, 0)}
        step={0.01}
        disabled={disabled}
        value={Math.min(displayTime, duration || 0)}
        onPointerDown={onBeginScrub}
        onPointerUp={(event) => onCommitScrub(Number(event.currentTarget.value))}
        onChange={(event) => onScrubTo(Number(event.target.value))}
      />
      <div className="flex shrink-0 items-center gap-1.5">
        <Button
          variant="ghost"
          size="icon"
          aria-label={muted ? "Unmute" : "Mute"}
          aria-pressed={muted}
          disabled={disabled}
          onClick={onToggleMute}
        >
          {muted ? <VolumeX className="text-destructive" /> : <Volume2 />}
        </Button>
        <input
          aria-label="Preview monitor volume"
          aria-valuetext={formatFaderDb(volume)}
          className="h-1 w-14 accent-primary"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={volume}
          onChange={(event) => onSetVolume(Number(event.target.value))}
        />
        {/* dB-tapered monitor gain readout (lib/fader.ts) — unity (0.0 dB) at
            max is the render itself; there is no gain above it to give up
            reading. See useStemPreview.ts's PROGRAM/MONITOR gain split. */}
        <span className="w-11 shrink-0 text-right text-[10px] font-medium tabular-nums text-muted-foreground" aria-hidden="true">
          {formatFaderDb(volume)}
        </span>
      </div>
      {children}
    </div>
  );
}

export const Transport = React.memo(TransportImpl);
