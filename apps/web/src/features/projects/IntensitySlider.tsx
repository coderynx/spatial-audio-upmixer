import * as React from "react";
import { Droplets } from "lucide-react";
import { cn } from "@/lib/utils";

/** Overlay control for Haze/Elevation's melt intensity — styled as a glass
 * chip (translucent white on the always-dark instrument field, matching
 * HazeView's existing "Aggregate output" chip) rather than the app's
 * ordinary filled `Input`/`Slider` chrome, which would read as a hole in the
 * canvas rather than a control sitting on it. A plain native
 * `<input type="range">`, not `Pot`/`Fader`'s custom drag contract — this is
 * a single, low-precision "how much" preference, not a value read off a
 * scale, so the platform slider (built-in keyboard/drag/aria) is the right
 * weight of control for it. `Droplets` stands for the haze/melt effect
 * itself, the thing the slider turns up or down. */
export function IntensitySlider({
  value,
  onChange,
  label,
  className,
}: {
  value: number;
  onChange: (value: number) => void;
  label: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded-md border border-white/10 bg-white/5 px-1.5 py-1 backdrop-blur-sm",
        className,
      )}
    >
      <Droplets className="h-3 w-3 shrink-0 text-white/60" aria-hidden="true" />
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        aria-label={label}
        className={cn(
          "h-1 w-16 cursor-pointer appearance-none rounded-full bg-white/15",
          "[&::-webkit-slider-runnable-track]:h-1 [&::-webkit-slider-runnable-track]:rounded-full [&::-webkit-slider-runnable-track]:bg-white/15",
          "[&::-webkit-slider-thumb]:mt-[-3px] [&::-webkit-slider-thumb]:h-2.5 [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:shadow-[0_0_3px_rgba(0,0,0,0.6)]",
          "[&::-moz-range-track]:h-1 [&::-moz-range-track]:rounded-full [&::-moz-range-track]:bg-white/15",
          "[&::-moz-range-thumb]:h-2.5 [&::-moz-range-thumb]:w-2.5 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-white",
        )}
      />
    </div>
  );
}
