import * as React from "react";
import { Grid3x3, Headphones, Waves } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { OutputMode } from "./useStemPreview";

const MODE_OPTIONS: { value: OutputMode; label: string; hint: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "binaural", label: "Binaural", hint: "Headphone-virtualized render of the channel bed.", icon: Headphones },
  { value: "stereo", label: "Stereo mixdown", hint: "ITU-R BS.775 2/0 downmix of the channel bed.", icon: Waves },
  { value: "native", label: "Native", hint: "Discrete channels of the selected layout, sent to a system output device.", icon: Grid3x3 },
];

// Icon dropdown for the preview box's output-mode picker — plain <select>
// (SelectField, components/forms/fields.tsx) can't render per-option icons,
// so this is a small custom popover instead of a native <select>. Includes
// a secondary system-device picker, shown only once native mode is chosen.
export function OutputModeSelect({
  value,
  onChange,
  nativeSupported,
  devices,
  deviceId,
  onDeviceChange,
}: {
  value: OutputMode;
  onChange: (mode: OutputMode) => void;
  nativeSupported: boolean;
  devices: MediaDeviceInfo[];
  deviceId: string;
  onDeviceChange: (deviceId: string) => void;
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

  const current = MODE_OPTIONS.find((option) => option.value === value) ?? MODE_OPTIONS[0];
  const CurrentIcon = current.icon;

  return (
    <div ref={containerRef} className="relative flex shrink-0 items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-label="Preview output mode"
        aria-expanded={open}
        onClick={() => setOpen((next) => !next)}
        className="gap-1.5"
      >
        <CurrentIcon className="h-4 w-4" />
        {current.label}
      </Button>
      {open && (
        <div className="absolute left-0 top-full z-10 mt-1 w-64 rounded-md border bg-popover p-1 shadow-md">
          {MODE_OPTIONS.map((option) => {
            const Icon = option.icon;
            const disabled = option.value === "native" && !nativeSupported;
            return (
              <button
                key={option.value}
                type="button"
                disabled={disabled}
                title={disabled ? "Current output device doesn't support this layout's discrete channel count." : undefined}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50",
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
      {value === "native" && devices.length > 0 && (
        <select
          aria-label="Output device"
          value={deviceId}
          onChange={(event) => onDeviceChange(event.target.value)}
          className="h-8 max-w-40 rounded-md border border-input bg-background px-2 text-xs shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="">System default</option>
          {devices.map((device) => (
            <option key={device.deviceId} value={device.deviceId}>
              {device.label || `Output ${device.deviceId.slice(0, 6)}`}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
