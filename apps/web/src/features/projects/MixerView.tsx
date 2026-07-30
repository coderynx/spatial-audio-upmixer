import { Link2 } from "lucide-react";
import * as React from "react";
import { Fader } from "@/components/ui/fader";
import { canvasTheme } from "@/lib/canvasTheme";
import { FADER_MIN_DB, dbToFaderPosition, faderPositionToDb, formatFaderDb } from "@/lib/fader";
import { cn } from "@/lib/utils";
import {
  FADER_TRAVEL,
  FADER_WIDTH,
  STRIP_EXTRA_MAX,
  STRIP_EXTRA_MIN,
  StemChannelStrip,
  StripReadouts,
  StripResizeHandle,
  StripToggle,
  stripWidth,
} from "./ChannelStrip";
import { StripMeter } from "./StripMeter";
import { useStripMeterLoop } from "./useStripMeterLoop";
import type { MeterLevel } from "./useStemPreview";

// Logic's mixer: one channel strip per stem (`StemChannelStrip`, shared with
// the inspector's always-accessible copy — see ChannelStrip.tsx), a source
// anchor strip, then the master strip. Stem strips are deliberately shallow
// — nameplate, fader, meter, mute/solo — because position and stem EQ
// already have one home in the inspector, and the design spec's "one control
// per idea" rule makes duplicating them a defect rather than a convenience.
//
// The strip's instrument parts (fader cap, travel slot, meter, readouts) are
// fixed dark like Logic's own, which keeps them identical in both app
// appearances; the rack around them is ordinary themed chrome.
//
// Every strip resizes independently — a resize handle on its own trailing
// edge (`StripResizeHandle`, ChannelStrip.tsx) widens or narrows only that
// strip, the way dragging a column border in a spreadsheet only moves that
// column. Widths persist under one localStorage map keyed by strip id.

const MONITOR_TICKS = [0, -6, -12, -18, -24, -36, -48, -60];
const ANCHOR_TICKS = [100, 75, 50, 25, 0];

const STRIP_WIDTHS_KEY = "upmixer.mixer.stripWidths";

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
  extraWidth,
  onExtraWidthChange,
  onExtraWidthCommit,
}: {
  strength: number;
  onChange: (strength: number) => void;
  disabled: boolean;
  extraWidth: number;
  onExtraWidthChange: (px: number) => void;
  onExtraWidthCommit: (px: number) => void;
}) {
  const percent = Math.round(strength * 100);
  return (
    <div
      className="relative flex shrink-0 flex-col items-center justify-end gap-1.5 border-x border-success/30 bg-success/5 px-1.5 py-1.5"
      style={{ width: FADER_WIDTH + 28 + extraWidth }}
    >
      <StripResizeHandle
        label="Resize Anchor strip"
        value={extraWidth}
        onChange={onExtraWidthChange}
        onCommit={onExtraWidthCommit}
      />
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

function MasterStrip({
  volume,
  onVolume,
  muted,
  onToggleMasterMute,
  headphoneLevels,
  active,
  extraWidth,
  onExtraWidthChange,
  onExtraWidthCommit,
}: {
  volume: number;
  onVolume: (value: number) => void;
  muted: boolean;
  onToggleMasterMute: () => void;
  headphoneLevels: React.MutableRefObject<{ left: MeterLevel; right: MeterLevel }>;
  active: boolean;
  extraWidth: number;
  onExtraWidthChange: (px: number) => void;
  onExtraWidthCommit: (px: number) => void;
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
      style={{ width: stripWidth(2) + extraWidth }}
    >
      <StripResizeHandle
        label="Resize Master strip"
        value={extraWidth}
        onChange={onExtraWidthChange}
        onCommit={onExtraWidthCommit}
      />
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
        Master
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
  headphoneLevels,
  volume,
  onVolume,
  muted,
  onToggleMasterMute,
  active,
  disabled,
  className,
  style,
}: MixerViewProps) {
  const soloed = new Set(solo);
  const silent = (stem: string) => enabled[stem] === false || (soloed.size > 0 && !soloed.has(stem));

  // Per-strip width overrides, keyed by strip id ("stem:<name>" | "anchor" |
  // "master"). `onChange` (live, during drag) only touches React state;
  // `onCommit` (drag end) is the one write to localStorage, same split as
  // the bottom pane's own resize handle uses for its height.
  const [stripWidths, setStripWidths] = React.useState<Record<string, number>>(readStoredStripWidths);
  const setLiveWidth = React.useCallback((key: string, px: number) => {
    setStripWidths((current) => (current[key] === px ? current : { ...current, [key]: px }));
  }, []);
  const commitWidth = React.useCallback((key: string, px: number) => {
    setStripWidths((current) => {
      const next = { ...current, [key]: px };
      try {
        window.localStorage.setItem(STRIP_WIDTHS_KEY, JSON.stringify(next));
      } catch {
        // Storage being unavailable only costs the preference, not the resize.
      }
      return next;
    });
  }, []);

  return (
    <div
      className={cn("flex min-h-0 overflow-x-auto overflow-y-hidden bg-card", className)}
      style={style}
      role="group"
      aria-label="Mixer"
    >
      {stems.map((stem) => {
        const widthKey = `stem:${stem}`;
        return (
          <StemChannelStrip
            key={stem}
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
            extraWidth={stripWidths[widthKey] ?? 0}
            onExtraWidthChange={(px) => setLiveWidth(widthKey, px)}
            onExtraWidthCommit={(px) => commitWidth(widthKey, px)}
          />
        );
      })}

      <AnchorStrip
        strength={anchorStrength}
        onChange={onAnchorStrength}
        disabled={disabled}
        extraWidth={stripWidths.anchor ?? 0}
        onExtraWidthChange={(px) => setLiveWidth("anchor", px)}
        onExtraWidthCommit={(px) => commitWidth("anchor", px)}
      />

      <MasterStrip
        volume={volume}
        onVolume={onVolume}
        muted={muted}
        onToggleMasterMute={onToggleMasterMute}
        headphoneLevels={headphoneLevels}
        active={active}
        extraWidth={stripWidths.master ?? 0}
        onExtraWidthChange={(px) => setLiveWidth("master", px)}
        onExtraWidthCommit={(px) => commitWidth("master", px)}
      />
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
  headphoneLevels: React.MutableRefObject<{ left: MeterLevel; right: MeterLevel }>;
  /** Monitor fader position, 0..1 — the same value the Transport volume
   * control drives. MONITOR domain: never reaches the exported render. */
  volume: number;
  onVolume: (value: number) => void;
  muted: boolean;
  onToggleMasterMute: () => void;
  active: boolean;
  disabled: boolean;
  className?: string;
  style?: React.CSSProperties;
};

export const MixerView = React.memo(MixerViewImpl);
