import * as React from "react";
import { Building2, ChevronDown, ChevronRight, Grid3x3, Headphones, Sofa, Waves } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SpatialProfile } from "./masteringProfiles";
import type { OutputMode } from "./useStemPreview";

const MODE_OPTIONS: { value: OutputMode; label: string; hint: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "binaural", label: "Binaural", hint: "Headphone-virtualized render of the channel bed.", icon: Headphones },
  { value: "stereo", label: "Stereo mixdown", hint: "ITU-R BS.775 2/0 downmix of the channel bed.", icon: Waves },
  { value: "native", label: "Native", hint: "Discrete channels of the selected layout, sent to a system output device.", icon: Grid3x3 },
];

// Studio/Listening/Flat picker for the Spatial Audio Engine binaural render
// (docs/standards/spatial_audio_engine.md), embedded as a submenu off the
// binaural row below rather than a separate button.
const PROFILE_OPTIONS: { value: SpatialProfile; label: string; hint: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "studio", label: "Studio", hint: "Neutral spatial-audio mixing room", icon: Building2 },
  { value: "listening", label: "Listening", hint: "Hi-Fi Cinema room", icon: Sofa },
  { value: "flat", label: "Flat", hint: "Anechoic reference", icon: Headphones },
];

// Grace period before the profile submenu closes on mouse-out.
const SUBMENU_CLOSE_DELAY_MS = 200;

// Icon dropdown for the preview box's output-mode picker — plain <select>
// (SelectField, components/forms/fields.tsx) can't render per-option icons,
// so this is a small custom popover instead of a native <select>. Includes
// a secondary system-device picker, shown only once native mode is chosen,
// and a submenu on the binaural row for the Spatial Audio Engine profile.
export function OutputModeSelect({
  value,
  onChange,
  nativeSupported,
  devices,
  deviceId,
  onDeviceChange,
  spatialProfile,
  onSpatialProfileChange,
}: {
  value: OutputMode;
  onChange: (mode: OutputMode) => void;
  nativeSupported: boolean;
  devices: MediaDeviceInfo[];
  deviceId: string;
  onDeviceChange: (deviceId: string) => void;
  spatialProfile: SpatialProfile;
  onSpatialProfileChange: (profile: SpatialProfile) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [submenuOpen, setSubmenuOpen] = React.useState(false);
  const [submenuFlip, setSubmenuFlip] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const binauralRowRef = React.useRef<HTMLDivElement>(null);
  const closeTimer = React.useRef<number | null>(null);

  // Reaching the submenu means travelling off the binaural row, and a
  // diagonal path crosses a sibling row on the way. Closing on the first
  // mouse-out therefore snatches the submenu away mid-gesture; every close is
  // deferred instead, and re-entering anywhere in the pair cancels it.
  const cancelClose = () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => {
      closeTimer.current = null;
      setSubmenuOpen(false);
    }, SUBMENU_CLOSE_DELAY_MS);
  };

  const openSubmenu = () => {
    cancelClose();
    const rect = binauralRowRef.current?.getBoundingClientRect();
    setSubmenuFlip(!!rect && rect.right + 256 + 4 > window.innerWidth);
    setSubmenuOpen(true);
  };

  React.useEffect(() => cancelClose, []);

  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        cancelClose();
        setOpen(false);
        setSubmenuOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const current = MODE_OPTIONS.find((option) => option.value === value) ?? MODE_OPTIONS[0];
  const CurrentIcon = current.icon;
  const currentProfile = PROFILE_OPTIONS.find((option) => option.value === spatialProfile) ?? PROFILE_OPTIONS[0];

  return (
    <div ref={containerRef} className="relative flex shrink-0 items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        title={`Preview output: ${current.label}`}
        aria-label={`Preview output mode: ${current.label}`}
        aria-expanded={open}
        onClick={() => setOpen((next) => !next)}
        className="shrink-0 gap-1 px-2.5"
      >
        <CurrentIcon className="h-4 w-4 shrink-0" />
        <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
      </Button>
      {open && (
        <div className="absolute left-0 top-full z-10 mt-1 w-64 rounded-md border bg-popover p-1 shadow-md">
          {MODE_OPTIONS.map((option) => {
            const Icon = option.icon;
            const disabled = option.value === "native" && !nativeSupported;
            const isBinaural = option.value === "binaural";
            return (
              <div
                key={option.value}
                ref={isBinaural ? binauralRowRef : undefined}
                className="relative"
                onMouseEnter={() => (isBinaural ? openSubmenu() : scheduleClose())}
                onMouseLeave={() => isBinaural && scheduleClose()}
              >
                <button
                  type="button"
                  disabled={disabled}
                  title={disabled ? "Current output device doesn't support this layout's discrete channel count." : undefined}
                  onClick={() => {
                    onChange(option.value);
                    if (isBinaural) openSubmenu();
                    else setOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50",
                    option.value === value && "bg-accent/60",
                  )}
                >
                  <Icon className="mt-0.5 h-4 w-4 shrink-0" />
                  <span className="flex-1">
                    <span className="block font-medium">{option.label}</span>
                    <span className="block text-xs text-muted-foreground">{option.hint}</span>
                  </span>
                  {isBinaural && (
                    <span className="mt-0.5 flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                      {currentProfile.label}
                      <ChevronRight className="h-3.5 w-3.5" />
                    </span>
                  )}
                </button>
                {isBinaural && submenuOpen && (
                  // The offset is padding on this wrapper, not a margin: a
                  // margin would leave a dead 4px channel between row and
                  // submenu that the pointer has to cross, which reads to the
                  // browser as leaving the row entirely.
                  <div
                    className={cn(
                      "absolute top-0 z-20",
                      submenuFlip ? "right-full pr-1" : "left-full pl-1",
                    )}
                    onMouseEnter={cancelClose}
                    onMouseLeave={scheduleClose}
                  >
                  <div className="w-64 rounded-md border bg-popover p-1 shadow-md">
                    {PROFILE_OPTIONS.map((profileOption) => {
                      const ProfileIcon = profileOption.icon;
                      return (
                        <button
                          key={profileOption.value}
                          type="button"
                          onClick={() => {
                            onSpatialProfileChange(profileOption.value);
                            onChange("binaural");
                            setOpen(false);
                            setSubmenuOpen(false);
                          }}
                          className={cn(
                            "flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground",
                            profileOption.value === spatialProfile && "bg-accent/60",
                          )}
                        >
                          <ProfileIcon className="mt-0.5 h-4 w-4 shrink-0" />
                          <span>
                            <span className="block font-medium">{profileOption.label}</span>
                            <span className="block text-xs text-muted-foreground">{profileOption.hint}</span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  </div>
                )}
              </div>
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
