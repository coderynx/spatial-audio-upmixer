import { Link2 } from "lucide-react";
import * as React from "react";
import { Fader } from "@/components/ui/fader";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { canvasTheme } from "@/lib/canvasTheme";
import { FADER_MIN_DB, dbToFaderPosition, faderPositionToDb, formatFaderDb } from "@/lib/fader";
import { cn } from "@/lib/utils";
import {
  FADER_TRAVEL,
  FADER_WIDTH,
  StemChannelStrip,
  StripReadouts,
  StripToggle,
  stripWidth,
} from "./ChannelStrip";
import { StripMeter } from "./StripMeter";
import { useStripMeterLoop } from "./useStripMeterLoop";
import type { MeterLevel } from "./useStemPreview";
import type { PanelImperativeHandle } from "react-resizable-panels";

// Rack layout, strip anatomy and resize behavior: docs/web_ui_controls.md.

const MONITOR_TICKS = [0, -6, -12, -18, -24, -36, -48, -60];
const ANCHOR_TICKS = [100, 75, 50, 25, 0];
const BED_TRIM_TICKS = [6, 3, 0];

const STRIP_WIDTHS_KEY = "upmixer.mixer.stripWidths";
const STRIP_EXTRA_MIN = 0;
const STRIP_EXTRA_MAX = 96;

function readStoredStripWidths(): Record<string, number> {
  try {
    const raw = window.localStorage.getItem(STRIP_WIDTHS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const widths: Record<string, number> = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === "number" && Number.isFinite(value)) {
        widths[key] = Math.min(STRIP_EXTRA_MAX, Math.max(STRIP_EXTRA_MIN, value));
      }
    }
    return widths;
  } catch {
    return {};
  }
}

/** The source-anchor strip blends the original, un-separated track back into
 * the render — a mix-wide correction, not one more channel of it. It sits
 * apart from the stem rack (own accent colour, own border, no meter or
 * mute/solo) precisely so it doesn't get mistaken for a stem: there is
 * nothing to solo or mute about "some of the original track," and unlike a
 * stem's post-gain send, this control changes how much of a *different*
 * signal — the pre-separation mix — is folded back in alongside the stems,
 * which is why it reads as "%blend" rather than "dB gain".
 *
 * Accented `success` (green), not `primary` (blue) — blue is already the
 * rack's selection colour (`bg-primary/10` on the selected stem strip), and
 * a second, unrelated use of the same hue right next to it read as the same
 * state. `success` reads as "engaged" for a blend control and isn't used as
 * a static background anywhere else in the rack. */
function AnchorStrip({
  strength,
  onChange,
  disabled,
}: {
  strength: number;
  onChange: (strength: number) => void;
  disabled: boolean;
}) {
  const percent = Math.round(strength * 100);
  return (
    <div
      className="relative flex shrink-0 flex-col items-center justify-end gap-1.5 border-x border-success/30 bg-success/5 px-1.5 py-1.5"
      style={{ width: "100%" }}
    >
      <span
        className="w-full rounded-[3px] py-px text-center text-[10px] font-semibold tabular-nums text-success"
        style={{ backgroundColor: canvasTheme.stripWell }}
      >
        {percent}%
      </span>
      <div className="flex items-stretch" style={{ height: FADER_TRAVEL }}>
        <Fader
          label="Source anchor blend"
          value={percent}
          min={0}
          max={100}
          step={1}
          ticks={ANCHOR_TICKS}
          valueText={`${percent}% blend`}
          onChange={(value) => onChange(value / 100)}
          onReset={() => onChange(0)}
          disabled={disabled}
          style={{ width: FADER_WIDTH }}
        />
      </div>
      {/* Matches the stem/master strips' M/S row so the fader and percent
          readout above it land at the same height across the rack, even
          though the anchor strip has no mute/solo of its own. */}
      <div className="h-6 w-full" aria-hidden="true" />
      <div
        className="flex w-full items-center justify-center gap-1 rounded-[5px] bg-success/15 px-1 py-1 text-[11px] font-medium text-success"
        title="Source anchor — blends the original mixed track back into the render, alongside the stems"
      >
        <Link2 className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-center">Anchor</span>
      </div>
    </div>
  );
}

function BedTrimStrip({
  trim,
  onChange,
  disabled,
}: {
  trim: number;
  onChange: (trim: number) => void;
  disabled: boolean;
}) {
  const value = `${trim > 0 ? "+" : ""}${trim.toFixed(1)}`;
  return (
    <div
      className="relative flex shrink-0 flex-col items-center justify-end gap-1.5 border-x bg-muted/40 px-1.5 py-1.5"
      style={{ width: "100%" }}
    >
      <StripReadouts value={value} peakDb={0} showPeak={false} />
      <div className="flex items-stretch" style={{ height: FADER_TRAVEL }}>
        <Fader
          label="Bed trim"
          value={trim}
          min={0}
          max={6}
          step={0.1}
          detent={0}
          ticks={BED_TRIM_TICKS}
          valueText={`${value} dB`}
          onChange={onChange}
          onReset={() => onChange(0)}
          disabled={disabled}
          style={{ width: FADER_WIDTH }}
        />
      </div>
      <div className="h-6 w-full" aria-hidden="true" />
      <span
        className="w-full truncate rounded-[5px] px-1 py-1 text-center text-[11px] font-semibold"
        style={{ backgroundColor: canvasTheme.stripWell }}
      >
        Bed trim
      </span>
    </div>
  );
}

function MasterStrip({
  volume,
  onVolume,
  muted,
  onToggleMasterMute,
  headphoneLevels,
  active,
}: {
  volume: number;
  onVolume: (value: number) => void;
  muted: boolean;
  onToggleMasterMute: () => void;
  headphoneLevels: React.MutableRefObject<{ left: MeterLevel; right: MeterLevel }>;
  active: boolean;
}) {
  const source = React.useCallback(
    () => [headphoneLevels.current.left, headphoneLevels.current.right],
    [headphoneLevels],
  );
  const { register, peakDb } = useStripMeterLoop(source, muted, active);
  const monitorDb = Number.isFinite(faderPositionToDb(volume)) ? faderPositionToDb(volume) : FADER_MIN_DB;

  return (
    <div
      className="relative flex shrink-0 flex-col items-center justify-end gap-1.5 border-l-2 bg-muted/40 px-1.5 py-1.5"
      style={{ width: "100%" }}
    >
      <StripReadouts value={formatFaderDb(volume).replace(" dB", "")} peakDb={peakDb} />
      <div className="flex items-stretch gap-1" style={{ height: FADER_TRAVEL }}>
        <Fader
          label="Monitor level"
          value={monitorDb}
          min={FADER_MIN_DB}
          max={0}
          step={0.5}
          detent={0}
          ticks={MONITOR_TICKS}
          valueText={formatFaderDb(volume)}
          onChange={(db) => onVolume(dbToFaderPosition(db))}
          onReset={() => onVolume(1)}
          style={{ width: FADER_WIDTH }}
        />
        <StripMeter channels={2} ref={register} />
      </div>
      <div className="flex w-full gap-1">
        <StripToggle letter="M" active={muted} label={muted ? "Unmute monitor" : "Mute monitor"} onClick={onToggleMasterMute} />
      </div>
      {/* The master fader is monitor gain, not program gain — it never
          reaches the exported render (see lib/fader.ts and
          useStemPreview.ts's PROGRAM/MONITOR split). */}
      <span
        className="w-full truncate rounded-[5px] px-1 py-1 text-center text-[11px] font-semibold"
        style={{ backgroundColor: canvasTheme.stripWell }}
      >
        Monitor
      </span>
    </div>
  );
}

function MixerViewImpl({
  stems,
  stemChannels,
  selectedStem,
  onSelectStem,
  gains,
  onGain,
  enabled,
  solo,
  onToggleMute,
  onToggleSolo,
  stemLevels,
  anchorStrength,
  onAnchorStrength,
  bedTrim,
  onBedTrim,
  headphoneLevels,
  volume,
  onVolume,
  muted,
  onToggleMasterMute,
  active,
  disabled,
  topControlForStem,
  className,
  style,
}: MixerViewProps) {
  const soloed = new Set(solo);
  const silent = (stem: string) => enabled[stem] === false || (soloed.size > 0 && !soloed.has(stem));

  const stripWidths = React.useRef(readStoredStripWidths()).current;
  const panelRefs = React.useRef<Record<string, PanelImperativeHandle | null>>({});
  const stripBases = Object.fromEntries([
    ...stems.map((stem) => [`stem:${stem}`, stripWidth(Math.min(2, Math.max(1, stemChannels[stem] ?? 1)))]),
    ["anchor", FADER_WIDTH + 36],
    ["bed-trim", FADER_WIDTH + 36],
    ["master", stripWidth(2)],
  ]);
  const persistWidths = (_: Record<string, number>, meta: { isUserInteraction: boolean }) => {
    if (!meta.isUserInteraction) return;
    const widths = Object.fromEntries(Object.entries(panelRefs.current).flatMap(([key, panel]) => {
      const base = stripBases[key];
      return panel && base ? [[key, Math.round(Math.min(STRIP_EXTRA_MAX, Math.max(STRIP_EXTRA_MIN, panel.getSize().inPixels - base)))]] : [];
    }));
    try { window.localStorage.setItem(STRIP_WIDTHS_KEY, JSON.stringify(widths)); } catch { /* preference only */ }
  };
  const panel = (key: string, label: string, children: React.ReactNode, last = false) => {
    const base = stripBases[key];
    return [
      <ResizablePanel key={`${key}:panel`} id={key} defaultSize={base + (stripWidths[key] ?? 0)} minSize={base} maxSize={base + STRIP_EXTRA_MAX} panelRef={(node) => { panelRefs.current[key] = node }} groupResizeBehavior="preserve-pixel-size" className="flex h-full w-full min-h-0 overflow-hidden">
        {children}
      </ResizablePanel>,
      !last && <ResizableHandle key={`${key}:handle`} aria-label={label} disableDoubleClick onDoubleClick={() => panelRefs.current[key]?.resize(base)} className="-mx-1 z-10 w-2 bg-transparent after:w-2 after:bg-transparent" />,
    ];
  };

  return (
    <div className={cn("flex min-h-0 overflow-x-auto overflow-y-hidden bg-card", className)} style={style}>
      <ResizablePanelGroup orientation="horizontal" className="min-w-full w-max" role="group" aria-label="Mixer" onLayoutChanged={persistWidths}>
        {stems.flatMap((stem) => {
          const widthKey = `stem:${stem}`;
          return panel(widthKey, `Resize ${stem} strip`, (
            <StemChannelStrip
              className={cn("border-r px-1.5 py-1.5", selectedStem === stem && "bg-primary/10")}
              stem={stem}
              channels={stemChannels[stem] ?? 1}
              gain={gains[stem] ?? 0}
              onGain={(value) => onGain(stem, value)}
              muted={enabled[stem] === false}
              soloed={soloed.has(stem)}
              silent={silent(stem)}
              onToggleMute={() => onToggleMute(stem)}
              onToggleSolo={() => onToggleSolo(stem)}
              meterSource={() => stemLevels.current.get(stem) ?? []}
              active={active}
              selected={selectedStem === stem}
              onSelect={() => onSelectStem(stem)}
              disabled={disabled}
              topControl={topControlForStem?.(stem)}
              style={{ width: "100%" }}
            />
          ), false);
        })}

        {panel("anchor", "Resize Anchor strip", <AnchorStrip
          strength={anchorStrength}
          onChange={onAnchorStrength}
          disabled={disabled}
        />)}

        {panel("bed-trim", "Resize Bed trim strip", <BedTrimStrip
          trim={bedTrim}
          onChange={onBedTrim}
          disabled={disabled}
        />)}

        {panel("master", "Resize Master strip", <MasterStrip
          volume={volume}
          onVolume={onVolume}
          muted={muted}
          onToggleMasterMute={onToggleMasterMute}
          headphoneLevels={headphoneLevels}
          active={active}
        />)}
        <ResizablePanel id="tail" minSize={0} className="min-h-0" />
      </ResizablePanelGroup>
    </div>
  );
}

export type MixerViewProps = {
  stems: string[];
  /** Source channel count per stem — a stereo stem gets two meter bars. */
  stemChannels: Record<string, number>;
  selectedStem: string | null;
  onSelectStem: (stem: string) => void;
  /** dB per stem, read from and written to `mixing.stem_rebalance`. */
  gains: Record<string, number>;
  onGain: (stem: string, gainDb: number) => void;
  enabled: Record<string, boolean>;
  solo: string[];
  onToggleMute: (stem: string) => void;
  onToggleSolo: (stem: string) => void;
  stemLevels: React.MutableRefObject<Map<string, MeterLevel[]>>;
  /** 0..1, written to `mixing.stem_source_anchor_strength`. */
  anchorStrength: number;
  onAnchorStrength: (strength: number) => void;
  /** 0..6 dB, written to `mixing.bed_trim_db`. */
  bedTrim: number;
  onBedTrim: (trim: number) => void;
  headphoneLevels: React.MutableRefObject<{ left: MeterLevel; right: MeterLevel }>;
  /** Monitor fader position, 0..1 — the same value the Transport volume
   * control drives. MONITOR domain: never reaches the exported render. */
  volume: number;
  onVolume: (value: number) => void;
  muted: boolean;
  onToggleMasterMute: () => void;
  active: boolean;
  disabled: boolean;
  topControlForStem?: (stem: string) => React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
};

export const MixerView = React.memo(MixerViewImpl);
