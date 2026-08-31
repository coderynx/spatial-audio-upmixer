import * as React from "react";
import { CloudFog, MoveVertical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { SegmentedControl } from "@/app/SegmentedControl";
import { getStemColor, getStemIcon } from "@/lib/stems";
import { speakerCoordinates, speakerDisplayLabel } from "@/lib/spatial";
import { FloatingWindow } from "./FloatingWindow";
import type { StemPlacement } from "./wasmEngine/panner";

type PannerMode = "planar" | "spherical";
type Point = { x: number; y: number };

const MODES = [
  { value: "planar" as const, label: "Planar" },
  { value: "spherical" as const, label: "Spherical" },
];
const MUTE_DB = -83;

function clamp(value: number, min = 0, max = 1) {
  return Math.min(max, Math.max(min, value));
}

function dbFromGain(gain: number) {
  return gain > 0 ? clamp(20 * Math.log10(gain), -82, 6) : MUTE_DB;
}

function gainFromDb(db: number) {
  return db <= MUTE_DB ? 0 : 10 ** (db / 20);
}

function formatDb(db: number) {
  return db <= MUTE_DB ? "Mute" : `${db > 0 ? "+" : ""}${db.toFixed(1)} dB`;
}

function puckPoint(placement: StemPlacement, mode: PannerMode, maxElevationDeg: number): Point {
  const radius = mode === "planar"
    ? 0.44 * (1 - (placement.diversity ?? 0))
    : 0.44 * (1 - (maxElevationDeg ? placement.elevation_deg / maxElevationDeg : 0));
  const angle = placement.azimuth_deg * Math.PI / 180;
  return { x: 0.5 - radius * Math.sin(angle), y: 0.5 - radius * Math.cos(angle) };
}

function channelPoint(angleDeg: number, radius: number): Point {
  const angle = angleDeg * Math.PI / 180;
  return { x: 0.5 - radius * Math.sin(angle), y: 0.5 - radius * Math.cos(angle) };
}

function dbControl(label: string, value: number, disabled: boolean, onChange: (value: number) => void) {
  return <label className="block min-w-0 text-[11px] text-muted-foreground">
    <span className="flex items-center gap-2"><span>{label}</span><span className="ml-auto tabular-nums text-foreground">{formatDb(value)}</span></span>
    <Slider aria-label={label} className="mt-2" min={MUTE_DB} max={6} step={0.5} disabled={disabled}
      value={[value]} onValueChange={([next]) => onChange(next)} />
  </label>;
}

export function BedPannerWindow({
  stemName,
  placement,
  route,
  channels,
  inputChannels,
  maxElevationDeg,
  ambientRear = 0,
  ambientHeight = 0,
  ambientHeightCrossoverHz = 2000,
  ariaLabel = "Bed panner",
  onPlacement,
  onRoute,
  onAmbient = () => undefined,
}: {
  stemName: string;
  placement: StemPlacement;
  route: Record<string, number>;
  channels: string[];
  inputChannels: number;
  maxElevationDeg: number;
  ambientRear?: number;
  ambientHeight?: number;
  ambientHeightCrossoverHz?: number;
  ariaLabel?: string;
  onPlacement: (next: StemPlacement) => void;
  onRoute: (patch: Record<string, number>) => void;
  onAmbient?: (patch: { rear?: number; height?: number; heightCrossoverHz?: number }) => void;
}) {
  const [mode, setMode] = React.useState<PannerMode>("planar");
  const ringInsets = [12.5, 25, 37.5];
  const stereo = inputChannels > 1;
  const hasCenter = channels.includes("C");
  const hasLfe = channels.includes("LFE");
  const hasSurround = channels.some((channel) => ["SL", "SR", "BL", "BR"].includes(channel));
  const hasHeight = channels.some((channel) => ["TFL", "TFR", "TBL", "TBR"].includes(channel));
  const diversity = placement.diversity ?? 0;
  const centerLevel = placement.center_level_db ?? 0;
  const lfeLevel = dbFromGain(route.LFE ?? 0);
  const heightCrossover = Math.min(4000, Math.max(500, ambientHeightCrossoverHz));
  const heightCrossoverPosition = Math.log(heightCrossover / 500) / Math.log(8);
  const point = puckPoint(placement, mode, maxElevationDeg);
  const left = channelPoint(placement.azimuth_deg + placement.width_deg / 2, Math.hypot(point.x - 0.5, point.y - 0.5));
  const right = channelPoint(placement.azimuth_deg - placement.width_deg / 2, Math.hypot(point.x - 0.5, point.y - 0.5));
  const StemIcon = getStemIcon(stemName);
  const stemColor = getStemColor(stemName);
  const edit = (patch: Partial<StemPlacement>) => onPlacement({ ...placement, ...patch });
  const move = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width - 0.5;
    const y = (event.clientY - rect.top) / rect.height - 0.5;
    const radius = clamp(Math.hypot(x, y) / 0.44);
    const azimuth_deg = Math.atan2(-x, -y) * 180 / Math.PI;
    edit(mode === "planar"
      ? { azimuth_deg, diversity: 1 - radius }
      : { azimuth_deg, elevation_deg: (1 - radius) * maxElevationDeg });
  };

  return <FloatingWindow
    title={`${stemName} Surround Panner`}
    ariaLabel={ariaLabel}
    borderColor={stemColor}
    icon={<StemIcon className="mr-1.5 h-3.5 w-3.5 shrink-0" style={{ color: stemColor }} aria-hidden="true" />}
    trigger={(open, expanded) => <Button className="relative h-10 w-10 overflow-hidden p-0" variant="outline" size="icon"
      aria-haspopup="dialog" aria-expanded={expanded} aria-label={ariaLabel} onClick={open}>
      <span className="relative h-7 w-7 rounded-full border border-border/70 bg-muted/50">
        <span aria-hidden="true" className="absolute inset-x-1/2 top-0 h-full w-px -translate-x-1/2 bg-border" />
        <span aria-hidden="true" className="absolute inset-y-1/2 left-0 h-px w-full -translate-y-1/2 bg-border" />
        <span data-bed-panner-preview-puck className="absolute z-10 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border border-primary bg-card ring-2 ring-primary/20"
          style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }} />
      </span>
    </Button>}
  >
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-2 text-center text-[11px] text-muted-foreground">
        <span>Angle <strong className="block font-medium tabular-nums text-foreground">{placement.azimuth_deg.toFixed(1)}°</strong></span>
        <span>Diversity <strong className="block font-medium tabular-nums text-foreground">{diversity.toFixed(2)}</strong></span>
        <span>Elevation <strong className="block font-medium tabular-nums text-foreground">{placement.elevation_deg.toFixed(1)}°</strong></span>
        <span className={stereo ? "" : "invisible"}>Spread <strong className="block font-medium tabular-nums text-foreground">+{placement.width_deg.toFixed(0)}°</strong></span>
      </div>

      <div
        role="group"
        tabIndex={0}
        aria-label="Surround position"
        aria-description="Drag the puck to adjust angle and diversity or elevation. Option-click resets to front."
        className="relative mx-auto aspect-square w-[min(100%,330px)] touch-none rounded-full bg-muted/50 ring-1 ring-border outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        onPointerDown={(event) => {
          if (event.altKey) { edit({ azimuth_deg: 0, diversity: 0, elevation_deg: 0 }); return; }
          event.currentTarget.setPointerCapture(event.pointerId);
          move(event);
        }}
        onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) move(event); }}
        onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)}
        onKeyDown={(event) => {
          const step = event.shiftKey ? 10 : 1;
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            edit({ azimuth_deg: placement.azimuth_deg + (event.key === "ArrowLeft" ? step : -step) });
          } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            event.preventDefault();
            const direction = event.key === "ArrowUp" ? 1 : -1;
            if (mode === "planar") edit({ diversity: clamp(diversity + direction * step / 100) });
            else edit({ elevation_deg: clamp(placement.elevation_deg + direction * step, 0, maxElevationDeg) });
          }
        }}
      >
        <div aria-hidden="true" className="pointer-events-none absolute inset-0 rounded-full">
          {ringInsets.map((inset) => <span key={inset} data-bed-radar-ring className="absolute rounded-full border border-border/70" style={{ inset: `${inset}%` }} />)}
          {[0, 45, 90, 135].map((angle) => <span key={angle} className="absolute inset-x-1/2 top-0 h-full w-px bg-border/70" style={{ transform: `rotate(${angle}deg)` }} />)}
        </div>
        {channels.filter((channel) => speakerCoordinates[channel]).map((channel) => {
          const speaker = speakerCoordinates[channel];
          return <span key={channel} aria-hidden="true" className={`absolute z-10 -translate-x-1/2 -translate-y-1/2 rounded px-1 text-[10px] font-semibold ${speaker.y ? "bg-primary/15 text-primary" : "bg-secondary text-muted-foreground"}`}
            style={{ left: `${50 + speaker.x * 48}%`, top: `${50 + speaker.z * 48}%` }}>{speakerDisplayLabel(channel, channels)}</span>;
        })}
        {stereo && <><span data-bed-channel="left" className="pointer-events-none absolute z-20 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold" style={{ left: `${left.x * 100}%`, top: `${left.y * 100}%` }}>L</span>
        <span data-bed-channel="right" className="pointer-events-none absolute z-20 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold" style={{ left: `${right.x * 100}%`, top: `${right.y * 100}%` }}>R</span></>}
        <span data-bed-drag-handle className="pointer-events-none absolute z-30 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-primary bg-card shadow-sm ring-4 ring-primary/20"
          style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }} />
      </div>

      {maxElevationDeg > 0 && <div className="flex justify-center"><SegmentedControl aria-label="Panning plane" segments={MODES} value={mode} onChange={setMode} /></div>}

      <div className="grid gap-3 border-t pt-3 sm:grid-cols-2">
        <label className="block text-[11px] text-muted-foreground">Angle <span className="float-right tabular-nums">{placement.azimuth_deg.toFixed(1)}°</span>
          <Slider aria-label="Angle" className="mt-2" min={-180} max={180} step={1} value={[placement.azimuth_deg]} onValueChange={([azimuth_deg]) => edit({ azimuth_deg })} />
        </label>
        <label className="block text-[11px] text-muted-foreground">Diversity <span className="float-right tabular-nums">{diversity.toFixed(2)}</span>
          <Slider aria-label="Diversity" className="mt-2" min={0} max={1} step={0.01} value={[diversity]} onValueChange={([next]) => edit({ diversity: next })} />
        </label>
        {maxElevationDeg > 0 && <label className="block text-[11px] text-muted-foreground">Elevation <span className="float-right tabular-nums">{placement.elevation_deg.toFixed(1)}°</span>
          <Slider aria-label="Elevation" className="mt-2" min={0} max={maxElevationDeg} step={1} value={[placement.elevation_deg]} onValueChange={([elevation_deg]) => edit({ elevation_deg })} />
        </label>}
        {stereo && <label className="block text-[11px] text-muted-foreground">Spread <span className="float-right tabular-nums">+{placement.width_deg.toFixed(0)}°</span>
          <Slider aria-label="Spread" className="mt-2" min={0} max={180} step={1} value={[placement.width_deg]} onValueChange={([width_deg]) => edit({ width_deg })} />
        </label>}
      </div>

      <div className="grid gap-3 border-t pt-3 sm:grid-cols-2">
        {dbControl("Center level", centerLevel, !hasCenter, (center_level_db) => edit({ center_level_db }))}
        {dbControl("LFE level", lfeLevel, !hasLfe, (db) => onRoute({ LFE: gainFromDb(db) }))}
      </div>

      {(hasSurround || hasHeight) && <div className="grid gap-3 border-t pt-3 sm:grid-cols-2">
        {hasSurround && <label className="block text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><CloudFog className="h-3 w-3" />Ambience to rear</span>
          <Slider aria-label="Ambience to rear" className="mt-2" min={0} max={1} step={0.01}
            value={[ambientRear]} onValueChange={([rear]) => onAmbient({ rear })} />
        </label>}
        {hasHeight && <label className="block text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><CloudFog className="h-3 w-3" />Ambience to height</span>
          <Slider aria-label="Ambience to height" className="mt-2" min={0} max={1} step={0.01}
            value={[ambientHeight]} onValueChange={([height]) => onAmbient({ height })} />
        </label>}
        {hasHeight && <label className="block text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><MoveVertical className="h-3 w-3" />Height crossover <span className="ml-auto">{Math.round(heightCrossover)} Hz</span></span>
          <Slider aria-label="Height crossover" className="mt-2" min={0} max={1} step={0.01}
            value={[heightCrossoverPosition]} onValueChange={([value]) => onAmbient({ heightCrossoverHz: 500 * 8 ** value })} />
        </label>}
      </div>}
    </div>
  </FloatingWindow>;
}
