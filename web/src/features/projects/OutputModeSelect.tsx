import * as React from "react";
import { Building2, Car, ChevronDown, ChevronRight, Grid3x3, Headphones, Radio, Sofa, Speaker, Waves } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SpatialProfile, TransauralProfile } from "./masteringProfiles";
import type { OutputMode } from "./useStemPreview";

const MODE_OPTIONS: { value: OutputMode; label: string; hint: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "binaural", label: "Binaural", hint: "Immersive spatial sound for headphone listening.", icon: Headphones },
  { value: "transaural", label: "Transaural", hint: "Immersive spatial sound for a stereo speaker pair.", icon: Speaker },
  { value: "native", label: "Native", hint: "Discrete channels of the selected layout, sent to a system output device.", icon: Grid3x3 },
  { value: "stereo", label: "Stereo mixdown", hint: "Downmix of the channel bed for two speakers.", icon: Waves },
];

// Rows that carry a profile submenu, keyed by their MODE_OPTIONS value.
const SUBMENU_MODES = new Set<OutputMode>(["binaural", "transaural"]);

// First MODE_OPTIONS index outside the "Spatial audio" group (binaural,
// transaural) — divider renders above it, no label needed for this second,
// self-explanatory group (native, stereo mixdown).
const GROUP_2_INDEX = MODE_OPTIONS.findIndex((option) => !SUBMENU_MODES.has(option.value));

// Studio/Listening/Flat picker for the Spatial Audio Engine binaural render
// (docs/standards/spatial_audio_engine.md), embedded as a submenu off the
// binaural row below rather than a separate button.
const PROFILE_OPTIONS: { value: SpatialProfile; label: string; hint: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "studio", label: "Studio", hint: "Clean, balanced, true to the mix", icon: Building2 },
  { value: "listening", label: "Listening", hint: "Warm, cinematic living-room feel", icon: Sofa },
  { value: "flat", label: "Flat", hint: "Pure, uncolored reference sound", icon: Headphones },
];

// Stereo/Smart speaker/Car picker for the crosstalk-cancellation (transaural)
// render (docs/standards/transaural_speakers.md), same submenu pattern as
// PROFILE_OPTIONS above, off the transaural row.
const TRANSAURAL_PROFILE_OPTIONS: { value: TransauralProfile; label: string; hint: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: "stereo", label: "Stereo", hint: "For a standard pair of speakers", icon: Speaker },
  { value: "smart_speaker", label: "Smart speaker", hint: "For compact, all-in-one speakers", icon: Radio },
  { value: "car", label: "Car", hint: "Tuned for in-car listening", icon: Car },
];

// Grace period before the profile submenu closes on mouse-out.
const SUBMENU_CLOSE_DELAY_MS = 200;

// Icon dropdown for the preview box's output-mode picker — plain <select>
// (SelectField, components/forms/fields.tsx) can't render per-option icons,
// so this is a small custom popover instead of a native <select>. Includes
// a secondary system-device picker, shown only once native mode is chosen,
// and a submenu on the binaural/transaural rows for their respective Spatial
// Audio Engine profiles.
export function OutputModeSelect({
  value,
  onChange,
  nativeSupported,
  devices,
  deviceId,
  onDeviceChange,
  spatialProfile,
  onSpatialProfileChange,
  transauralProfile,
  onTransauralProfileChange,
}: {
  value: OutputMode;
  onChange: (mode: OutputMode) => void;
  nativeSupported: boolean;
  devices: MediaDeviceInfo[];
  deviceId: string;
  onDeviceChange: (deviceId: string) => void;
  spatialProfile: SpatialProfile;
  onSpatialProfileChange: (profile: SpatialProfile) => void;
  transauralProfile: TransauralProfile;
  onTransauralProfileChange: (profile: TransauralProfile) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [menuFlip, setMenuFlip] = React.useState(false);
  // Which submenu-carrying row (if any) is open — at most one at a time,
  // same as the single `submenuOpen` boolean this replaces, generalized to
  // pick between the binaural and transaural rows' distinct option lists.
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

  const current = MODE_OPTIONS.find((option) => option.value === value) ?? MODE_OPTIONS[0];
  const CurrentIcon = current.icon;
  const currentProfile = PROFILE_OPTIONS.find((option) => option.value === spatialProfile) ?? PROFILE_OPTIONS[0];
  const currentTransauralProfile = TRANSAURAL_PROFILE_OPTIONS.find((option) => option.value === transauralProfile) ?? TRANSAURAL_PROFILE_OPTIONS[0];
  // The trigger sits directly beside Transport's dB readout (Transport.tsx)
  // with no menu open — for binaural/transaural, the active Spatial Audio
  // Engine profile is as glanceable a fact as that readout, so it's shown
  // inline rather than left for the submenu alone to report. Native and
  // stereo have no profile to show, so the mode's own label fills the same
  // slot — an icon-only trigger next to two labelled ones read as broken
  // rather than simply profile-less.
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
          // Left-aligned under the trigger overflows off the right edge of
          // the window when this control sits at the bar's trailing
          // edge (its usual spot) — flip to right-aligned whenever the
          // menu's own width wouldn't fit, the same check the profile
          // submenu already makes for its own overflow.
          const rect = containerRef.current?.getBoundingClientRect();
          setMenuFlip(!!rect && rect.left + 256 > window.innerWidth);
          setOpen((next) => !next);
        }}
        // Fixed width, sized to fit the icon, the longest label this trigger
        // ever shows ("Stereo mixdown"), and the chevron together — this
        // trigger sits packed against the mute button and volume fader (see
        // Transport.tsx), so a width that changed with the mode/profile
        // would drag those along with it every time.
        className="h-8 w-[156px] shrink-0 gap-1 px-2.5"
      >
        <CurrentIcon className="h-4 w-4 shrink-0" />
        <span className="truncate text-xs font-medium text-muted-foreground">{triggerLabel}</span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
      </Button>
      {open && (
        <div
          className={cn(
            "absolute top-full z-10 mt-1 w-64 rounded-md border bg-popover p-1 shadow-md",
            menuFlip ? "right-0" : "left-0",
          )}
        >
          {MODE_OPTIONS.map((option, index) => {
            const Icon = option.icon;
            const disabled = option.value === "native" && !nativeSupported;
            const hasSubmenu = SUBMENU_MODES.has(option.value);
            const rowSubmenuOpen = activeSubmenu === option.value;
            const rowProfileOptions = option.value === "transaural" ? TRANSAURAL_PROFILE_OPTIONS : PROFILE_OPTIONS;
            const rowCurrentProfile = option.value === "transaural" ? currentTransauralProfile : currentProfile;
            return (
              <React.Fragment key={option.value}>
                {index === 0 && (
                  <div className="px-2 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[.08em] text-muted-foreground">
                    Spatial audio
                  </div>
                )}
                {/* --border sits too close to --popover's own lightness to
                    read as a line on this surface (no other popover in the
                    app carries an internal divider) — muted-foreground at
                    low opacity gives it actual contrast. */}
                {index === GROUP_2_INDEX && <div className="my-1 border-t border-muted-foreground/25" aria-hidden="true" />}
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
                    "flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50",
                    option.value === value && "bg-accent/60",
                  )}
                >
                  <Icon className="mt-0.5 h-4 w-4 shrink-0" />
                  <span className="flex-1">
                    <span className="block font-medium">{option.label}</span>
                    <span className="block text-xs text-muted-foreground">{option.hint}</span>
                  </span>
                  {hasSubmenu && (
                    <span className="mt-0.5 flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
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
                        : profileOption.value === spatialProfile;
                      return (
                        <button
                          key={profileOption.value}
                          type="button"
                          onClick={() => {
                            if (option.value === "transaural") {
                              onTransauralProfileChange(profileOption.value as TransauralProfile);
                            } else {
                              onSpatialProfileChange(profileOption.value as SpatialProfile);
                            }
                            onChange(option.value);
                            setOpen(false);
                            setActiveSubmenu(null);
                          }}
                          className={cn(
                            "flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground",
                            selected && "bg-accent/60",
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
              </React.Fragment>
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
