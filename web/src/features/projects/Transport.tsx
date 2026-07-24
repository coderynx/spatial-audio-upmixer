import * as React from "react";
import { Pause, Play, Repeat, Square, Volume2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function digits(seconds: number) {
  const clamped = Math.max(0, seconds || 0);
  const minutes = Math.floor(clamped / 60);
  const whole = Math.floor(clamped % 60);
  const tenths = Math.floor((clamped - Math.floor(clamped)) * 10);
  return `${String(minutes).padStart(2, "0")}:${String(whole).padStart(2, "0")}.${tenths}`;
}

function LcdDisplay({ currentTime, duration, mode, onToggleMode }: { currentTime: number; duration: number; mode: "elapsed" | "remaining"; onToggleMode: () => void }) {
  const remaining = Math.max(0, duration - currentTime);
  const value = mode === "elapsed" ? digits(currentTime) : `-${digits(remaining)}`;
  return (
    <button
      type="button"
      onClick={onToggleMode}
      aria-label={`Time display, showing ${mode === "elapsed" ? "elapsed" : "remaining"} time. Click to toggle.`}
      title="Click to toggle elapsed / remaining"
      className="group flex shrink-0 flex-col items-center gap-0.5 rounded-md border border-black/60 bg-[#0b1410] px-3 py-1.5 shadow-[inset_0_2px_4px_rgba(0,0,0,0.6)]"
    >
      <span
        className="font-mono text-lg font-medium tabular-nums tracking-wider text-emerald-400"
        style={{ textShadow: "0 0 6px rgba(52,211,153,0.65), 0 0 1px rgba(52,211,153,0.9)" }}
      >
        {value}
      </span>
      <span className="text-[9px] font-semibold uppercase tracking-[0.2em] text-emerald-700 group-hover:text-emerald-500">
        {mode === "elapsed" ? "Elapsed" : "Remaining"}
      </span>
    </button>
  );
}

export function Transport({
  playing,
  currentTime,
  duration,
  volume,
  loop,
  disabled,
  onPlayPause,
  onStop,
  onToggleLoop,
  onSetVolume,
  onBeginScrub,
  onScrubTo,
  onCommitScrub,
}: {
  playing: boolean;
  currentTime: number;
  duration: number;
  volume: number;
  loop: boolean;
  disabled: boolean;
  onPlayPause: () => void;
  onStop: () => void;
  onToggleLoop: () => void;
  onSetVolume: (value: number) => void;
  onBeginScrub: () => void;
  onScrubTo: (value: number) => void;
  onCommitScrub: (value: number) => void;
}) {
  const [mode, setMode] = React.useState<"elapsed" | "remaining">("elapsed");
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-muted/20 p-2.5">
      <div className="flex items-center gap-1">
        <Button variant="outline" size="icon" aria-label="Stop" disabled={disabled} onClick={onStop}>
          <Square className="h-4 w-4" />
        </Button>
        <Button size="icon" aria-label={playing ? "Pause" : "Play"} disabled={disabled} onClick={onPlayPause}>
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </Button>
        <Button
          variant={loop ? "default" : "outline"}
          size="icon"
          aria-label="Toggle repeat"
          aria-pressed={loop}
          disabled={disabled}
          onClick={onToggleLoop}
        >
          <Repeat className="h-4 w-4" />
        </Button>
      </div>
      <LcdDisplay currentTime={currentTime} duration={duration} mode={mode} onToggleMode={() => setMode((current) => (current === "elapsed" ? "remaining" : "elapsed"))} />
      <input
        aria-label="Preview position"
        className={cn("h-1.5 min-w-32 flex-1 accent-primary", disabled && "opacity-50")}
        type="range"
        min={0}
        max={Math.max(duration, 0)}
        step={0.01}
        disabled={disabled}
        value={Math.min(currentTime, duration || 0)}
        onPointerDown={onBeginScrub}
        onPointerUp={(event) => onCommitScrub(Number(event.currentTarget.value))}
        onChange={(event) => onScrubTo(Number(event.target.value))}
      />
      <label className="flex shrink-0 items-center gap-2">
        <Volume2 className="h-4 w-4 text-muted-foreground" />
        <input
          aria-label="Preview volume"
          className="h-1.5 w-20 accent-primary"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={volume}
          onChange={(event) => onSetVolume(Number(event.target.value))}
        />
      </label>
    </div>
  );
}
