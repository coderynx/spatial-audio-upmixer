import * as React from "react";
import { ChevronDown, Building2, Headphones as HeadphonesIcon, Sofa } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SpatialProfile } from "./masteringProfiles";

// Studio/Listening/Flat picker for the Spatial Audio Engine binaural render
// (docs/standards/spatial_audio_engine.md). Same icon-dropdown pattern as
// OutputModeSelect — only meaningful in "binaural" output mode.
const PROFILE_OPTIONS: { value: SpatialProfile; label: string; hint: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "studio", label: "Studio", hint: "Neutral spatial-audio mixing room monitor — no added coloration.", icon: Building2 },
  { value: "listening", label: "Listening", hint: "Consumer room + Apple Music Atmos-style enhance (bass/air lift, crossfeed, widen).", icon: Sofa },
  { value: "flat", label: "Flat", hint: "Anechoic reference — zero room, zero voicing.", icon: HeadphonesIcon },
];

export function SpatialProfileSelect({
  value,
  onChange,
  disabled = false,
}: {
  value: SpatialProfile;
  onChange: (profile: SpatialProfile) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const current = PROFILE_OPTIONS.find((option) => option.value === value) ?? PROFILE_OPTIONS[0];
  const CurrentIcon = current.icon;

  return (
    <div ref={containerRef} className="relative flex shrink-0 items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        title={`Spatial Audio Engine profile: ${current.label}`}
        aria-label={`Spatial Audio Engine profile: ${current.label}`}
        aria-expanded={open}
        onClick={() => setOpen((next) => !next)}
        className="shrink-0 gap-1 px-2.5"
      >
        <CurrentIcon className="h-4 w-4 shrink-0" />
        <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
      </Button>
      {open && (
        <div className="absolute left-0 top-full z-10 mt-1 w-72 rounded-md border bg-popover p-1 shadow-md">
          {PROFILE_OPTIONS.map((option) => {
            const Icon = option.icon;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground",
                  option.value === value && "bg-accent/60",
                )}
              >
                <Icon className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  <span className="block font-medium">{option.label}</span>
                  <span className="block text-xs text-muted-foreground">{option.hint}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
