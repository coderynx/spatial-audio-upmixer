import * as React from "react";
import { AudioWaveform, Building2, Car, ChevronDown, ChevronRight, Grid3x3, Headphones, Laptop, Radio, Smartphone, Sofa, Speaker, SquareSplitHorizontal, Waves } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SpatialProfile, TransauralProfile } from "./masteringProfiles";
import type { OutputMode } from "./useStemPreview";

const MODE_OPTIONS: { value: OutputMode; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "binaural", label: "Binaural", icon: Headphones },
  { value: "transaural", label: "Transaural", icon: Speaker },
  { value: "apple_spatial", label: "Apple Spatial", icon: AudioWaveform },
  { value: "native", label: "Native", icon: Grid3x3 },
  { value: "stereo", label: "Stereo mixdown", icon: Waves },
];

// On a two-channel layout the bed *is* the speaker pair, so "native" is
// named for what the listener hears rather than for the render path.
const STEREO_MODE_OPTIONS: typeof MODE_OPTIONS = [
  { value: "native", label: "Stereo", icon: SquareSplitHorizontal },
];

// Rows that carry a submenu, keyed by their MODE_OPTIONS value.
const SUBMENU_MODES = new Set<OutputMode>(["binaural", "transaural"]);
const SPATIAL_MODES = new Set<OutputMode>(["binaural", "transaural", "apple_spatial"]);

// First MODE_OPTIONS index outside the "Spatial audio" group (binaural,
// transaural) — divider renders above it, no label needed for this second,
// self-explanatory group (native, stereo mixdown).
const GROUP_2_INDEX = MODE_OPTIONS.findIndex((option) => !SPATIAL_MODES.has(option.value));

// Studio/Listening/Flat picker for the Spatial Audio Engine binaural render
// (docs/standards/spatial_audio_engine.md), embedded as a submenu off the
// binaural row below rather than a separate button.
const PROFILE_OPTIONS: { value: SpatialProfile; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "studio", label: "Studio", icon: Building2 },
  { value: "listening", label: "Listening", icon: Sofa },
  { value: "flat", label: "Flat", icon: Headphones },
];

// Stereo/Smart speaker/Car/Laptop/Phone picker for the crosstalk-cancellation
// (transaural) render (docs/standards/transaural_speakers.md), same submenu
// pattern as PROFILE_OPTIONS above, off the transaural row.
const TRANSAURAL_PROFILE_OPTIONS: { value: TransauralProfile; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "stereo", label: "Stereo", icon: Speaker },
  { value: "smart_speaker", label: "Smart speaker", icon: Radio },
  { value: "car", label: "Car", icon: Car },
  { value: "laptop", label: "Laptop", icon: Laptop },
  { value: "phone", label: "Phone", icon: Smartphone },
];

const HEAD_TRACKING_OPTIONS = [
  { value: true, label: "Head tracking on", icon: Headphones },
  { value: false, label: "Head tracking off", icon: Headphones },
];

// Grace period before the profile submenu closes on mouse-out.
const SUBMENU_CLOSE_DELAY_MS = 200;

// Custom popover, not a native <select>: SelectField can't render per-option icons.
export function OutputModeSelect({
  value,
  onChange,
  nativeSupported,
  nativeOnly,
  devices,
  deviceId,
  onDeviceChange,
  spatialProfile,
  onSpatialProfileChange,
  transauralProfile,
  onTransauralProfileChange,
  appleHeadTracking = true,
  onAppleHeadTrackingChange,
  appleSpatialAvailable = false,
  systemOutput = false,
}: {
  value: OutputMode;
  onChange: (mode: OutputMode) => void;
  nativeSupported: boolean;
  /** Two-channel layouts have no bed to collapse, so every other mode is
   * hidden rather than disabled. */
  nativeOnly?: boolean;
  devices: MediaDeviceInfo[];
  deviceId: string;
  onDeviceChange: (deviceId: string) => void;
  spatialProfile: SpatialProfile;
  onSpatialProfileChange: (profile: SpatialProfile) => void;
  transauralProfile: TransauralProfile;
  onTransauralProfileChange: (profile: TransauralProfile) => void;
  appleHeadTracking?: boolean;
  onAppleHeadTrackingChange: (enabled: boolean) => void;
  appleSpatialAvailable?: boolean;
  systemOutput?: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const [menuFlip, setMenuFlip] = React.useState(false);
  // Which submenu-carrying row (if any) is open — at most one at a time.
  const [activeSubmenu, setActiveSubmenu] = React.useState<OutputMode | null>(null);
  const [submenuFlip, setSubmenuFlip] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const closeTimer = React.useRef<number | null>(null);
  // One row element per submenu-carrying mode, keyed by its OutputMode value
  // — a plain object ref (not a per-row `useRef` call, which would violate
  // the Rules of Hooks inside the `.map()` below) so `openSubmenu` can still
  // read each row's bounding rect for its overflow-flip check.
  const rowRefs = React.useRef<Partial<Record<OutputMode, HTMLDivElement | null>>>({});

  // Reaching the submenu means travelling off its row, and a diagonal path
  // crosses a sibling row on the way. Closing on the first mouse-out
  // therefore snatches the submenu away mid-gesture; every close is deferred
  // instead, and re-entering anywhere in the pair cancels it.
  const cancelClose = () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => {
      closeTimer.current = null;
      setActiveSubmenu(null);
    }, SUBMENU_CLOSE_DELAY_MS);
  };

  const openSubmenu = (mode: OutputMode, rowEl: HTMLElement | null) => {
    cancelClose();
    const rect = rowEl?.getBoundingClientRect();
    setSubmenuFlip(!!rect && rect.right + 256 + 4 > window.innerWidth);
    setActiveSubmenu(mode);
  };

  React.useEffect(() => cancelClose, []);

  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        cancelClose();
        setOpen(false);
        setActiveSubmenu(null);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  // Chrome lists a synthetic `default` output aliasing whichever real device
  // is current, so it is not a target of its own: with one real device left,
  // the picker would offer "System default" and its own duplicate.
  const selectableDevices = devices.filter((device) => device.deviceId !== "default");
  const modeOptions = nativeOnly
    ? STEREO_MODE_OPTIONS
    : MODE_OPTIONS.filter((option) => option.value !== "apple_spatial" || appleSpatialAvailable);
  const current = modeOptions.find((option) => option.value === value) ?? modeOptions[0];
  const CurrentIcon = current.icon;
  const currentProfile = PROFILE_OPTIONS.find((option) => option.value === spatialProfile) ?? PROFILE_OPTIONS[0];
  const currentTransauralProfile = TRANSAURAL_PROFILE_OPTIONS.find((option) => option.value === transauralProfile) ?? TRANSAURAL_PROFILE_OPTIONS[0];
  const currentHeadTracking = HEAD_TRACKING_OPTIONS.find((option) => option.value === appleHeadTracking) ?? HEAD_TRACKING_OPTIONS[0];
  // Binaural/transaural show their active profile inline; native/stereo fall
  // back to the mode's own label so the trigger never reads as broken.
  const currentModeProfile = value === "transaural" ? currentTransauralProfile : value === "binaural" ? currentProfile : null;
  const triggerLabel = currentModeProfile?.label ?? current.label;

  return (
    <div ref={containerRef} className="relative flex shrink-0 items-center gap-2">
      {/* h-8 to match the transport cluster and the mute/volume group beside
          it (Transport.tsx) — this trigger is touched as often as those
          while monitoring a preview, and it was previously smaller (`sm`,
          h-6) than its own device <select> a few pixels to its right. */}
      <Button
        type="button"
        variant="outline"
        size="default"
        title={`Preview output: ${current.label}${currentModeProfile ? ` (${currentModeProfile.label})` : ""}`}
        aria-label={`Preview output mode: ${current.label}${currentModeProfile ? `, ${currentModeProfile.label} profile` : ""}`}
        aria-expanded={open}
        onClick={() => {
          // Flips to right-aligned when left-aligned would overflow the window
          // (this control usually sits at the bar's trailing edge).
          const rect = containerRef.current?.getBoundingClientRect();
          setMenuFlip(!!rect && rect.left + 256 > window.innerWidth);
          setOpen((next) => !next);
        }}
        // Fixed width (fits the longest label, "Stereo mixdown") so this trigger,
        // packed against the mute button and volume fader, doesn't shift them
        // as the label changes between modes/profiles.
        className="h-8 w-[156px] shrink-0 justify-between px-2.5"
      >
        <span className="flex min-w-0 items-center gap-1">
          <CurrentIcon className="h-4 w-4 shrink-0" />
          <span className="truncate text-xs font-medium text-muted-foreground">{triggerLabel}</span>
        </span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
      </Button>
      {open && (
        <div
          className={cn(
            "absolute top-full z-10 mt-1 w-64 rounded-md border bg-popover p-1 shadow-md",
            menuFlip ? "right-0" : "left-0",
          )}
        >
          {modeOptions.map((option, index) => {
            const Icon = option.icon;
            const disabled = option.value === "native" && !nativeSupported;
            const hasSubmenu = SUBMENU_MODES.has(option.value) || option.value === "apple_spatial";
            const rowSubmenuOpen = activeSubmenu === option.value;
            const rowProfileOptions = option.value === "transaural" ? TRANSAURAL_PROFILE_OPTIONS : option.value === "apple_spatial" ? HEAD_TRACKING_OPTIONS : PROFILE_OPTIONS;
            const rowCurrentProfile = option.value === "transaural" ? currentTransauralProfile : option.value === "apple_spatial" ? currentHeadTracking : currentProfile;
            return (
              <React.Fragment key={option.value}>
                {!nativeOnly && index === 0 && (
                  <div className="px-2 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[.08em] text-muted-foreground">
                    Spatial audio
                  </div>
                )}
                {/* --border sits too close to --popover's own lightness to
                    read as a line on this surface (no other popover in the
                    app carries an internal divider) — muted-foreground at
                    low opacity gives it actual contrast. */}
                {!nativeOnly && index === GROUP_2_INDEX && (
                  <>
                    <div className="my-1 border-t border-muted-foreground/25" aria-hidden="true" />
                    <div className="px-2 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[.08em] text-muted-foreground">
                      Direct output
                    </div>
                  </>
                )}
              <div
                ref={hasSubmenu ? (el) => { rowRefs.current[option.value] = el; } : undefined}
                className="relative"
                onMouseEnter={() => (hasSubmenu ? openSubmenu(option.value, rowRefs.current[option.value] ?? null) : scheduleClose())}
                onMouseLeave={() => hasSubmenu && scheduleClose()}
              >
                <button
                  type="button"
                  disabled={disabled}
                  title={disabled ? "Current output device doesn't support this layout's discrete channel count." : undefined}
                  onClick={() => {
                    onChange(option.value);
                    if (hasSubmenu) openSubmenu(option.value, rowRefs.current[option.value] ?? null);
                    else setOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50",
                    option.value === value && "bg-accent/60",
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span className="flex-1 font-medium">{option.label}</span>
                  {hasSubmenu && (
                    <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                      {rowCurrentProfile.label}
                      <ChevronRight className="h-3.5 w-3.5" />
                    </span>
                  )}
                </button>
                {hasSubmenu && rowSubmenuOpen && (
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
                    {rowProfileOptions.map((profileOption) => {
                      const ProfileIcon = profileOption.icon;
                      const selected = option.value === "transaural"
                        ? profileOption.value === transauralProfile
                        : option.value === "apple_spatial"
                          ? profileOption.value === appleHeadTracking
                          : profileOption.value === spatialProfile;
                      return (
                        <button
                          key={String(profileOption.value)}
                          type="button"
                          onClick={() => {
                            if (option.value === "transaural") {
                              onTransauralProfileChange(profileOption.value as TransauralProfile);
                            } else if (option.value === "apple_spatial") {
                              onAppleHeadTrackingChange(profileOption.value as boolean);
                            } else {
                              onSpatialProfileChange(profileOption.value as SpatialProfile);
                            }
                            onChange(option.value);
                            setOpen(false);
                            setActiveSubmenu(null);
                          }}
                          className={cn(
                            "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground",
                            selected && "bg-accent/60",
                          )}
                        >
                          <ProfileIcon className="h-4 w-4 shrink-0" />
                          <span className="font-medium">{profileOption.label}</span>
                        </button>
                      );
                    })}
                  </div>
                  </div>
                )}
              </div>
              </React.Fragment>
            );
          })}
        </div>
      )}
      {systemOutput && (value === "native" || value === "apple_spatial") && (
        <span className="text-[11px] text-muted-foreground">System output</span>
      )}
      {!systemOutput && value === "native" && selectableDevices.length > 1 && (
        <select
          aria-label="Output device"
          value={deviceId}
          onChange={(event) => onDeviceChange(event.target.value)}
          className="h-8 max-w-40 rounded-md border border-input bg-background px-2 text-xs shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="">System default</option>
          {selectableDevices.map((device) => (
            <option key={device.deviceId} value={device.deviceId}>
              {device.label || `Output ${device.deviceId.slice(0, 6)}`}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
