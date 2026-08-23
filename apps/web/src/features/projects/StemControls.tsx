import * as React from "react";
import { ArrowLeftRight, ArrowUpDown, AudioWaveform, MoveVertical, Waves } from "lucide-react";
import { Slider } from "@/components/ui/slider";
import type { StemPlacement } from "./wasmEngine/panner";

/** Slider positions on the floor plane, both 0..1. `lateral` runs left to
 * right, `depth` front to back; together they name a direction, which is what
 * the panner takes. Dead centre (0.5, 0.5) has no direction of its own and
 * resolves to front. */
type Position = { lateral: number; depth: number };

/** `azimuth = atan2(-x, -z)`, matching `binaural/geometry.py`'s convention:
 * 0 = front, positive = left, listener facing -Z. */
export function azimuthFromPosition({ lateral, depth }: Position): number {
  const x = lateral * 2 - 1;
  const z = depth * 2 - 1;
  if (x === 0 && z === 0) return 0;
  return (Math.atan2(-x, -z) * 180) / Math.PI;
}

export function positionFromAzimuth(azimuthDeg: number): Position {
  const azimuth = (azimuthDeg * Math.PI) / 180;
  return {
    lateral: (-Math.sin(azimuth) + 1) / 2,
    depth: (-Math.cos(azimuth) + 1) / 2,
  };
}

export const StemControls = React.memo(function StemControls({
  placement, route, channels, eq, maxElevationDeg, onPlacement, onRoute, onEq, stemEqProfiles,
}: {
  placement: StemPlacement;
  route: Record<string, number>;
  channels: string[];
  eq: string;
  maxElevationDeg: number;
  onPlacement: (next: StemPlacement) => void;
  onRoute: (patch: Record<string, number>) => void;
  onEq: (eq: string) => void;
  stemEqProfiles?: string[];
}) {
  // The sliders are the Cartesian face of a direction, so a round trip through
  // azimuth normalizes them onto the unit circle. Holding the dragged pair
  // here keeps the thumb where it was put; it re-seeds only when the placement
  // arrives from somewhere else (a different stem, a preset, an undo).
  const [position, setPosition] = React.useState(() => positionFromAzimuth(placement.azimuth_deg));
  React.useEffect(() => {
    setPosition((current) =>
      Math.abs(azimuthFromPosition(current) - placement.azimuth_deg) < 1e-6
        ? current
        : positionFromAzimuth(placement.azimuth_deg));
  }, [placement.azimuth_deg]);

  const move = (patch: Partial<Position>) => {
    const next = { ...position, ...patch };
    setPosition(next);
    onPlacement({ ...placement, azimuth_deg: azimuthFromPosition(next) });
  };

  const stereo = channels.length === 2;
  const hasHeight = channels.includes("TFL") || channels.includes("TFR")
    || channels.includes("TBL") || channels.includes("TBR");
  const hasLfe = channels.includes("LFE");
  const height = maxElevationDeg > 0
    ? Math.min(1, Math.max(0, placement.elevation_deg / maxElevationDeg))
    : 0;

  const lateralSlider = (
    <label className="block text-[11px] text-muted-foreground">
      <span className="flex items-center gap-1"><ArrowLeftRight className="h-3 w-3" />Left <span className="ml-auto">Right</span></span>
      <Slider aria-label="Left to right" className="mt-1.5" min={0} max={1} step={0.01}
        value={[position.lateral]} onValueChange={([lateral]) => move({ lateral })} />
    </label>
  );
  const stemEqSelect = (
    <label className="block text-[11px] text-muted-foreground">
      <span className="flex items-center gap-1"><AudioWaveform className="h-3 w-3" />EQ</span>
      <select className="mt-1.5 flex h-7 w-full rounded-md border bg-secondary px-2 text-[13px] text-foreground"
        value={eq} onChange={(event) => onEq(event.target.value)}>
        <option value="">None</option>
        {(stemEqProfiles ?? []).filter((name) => name !== "flat").map((name) => <option key={name} value={name}>{name}</option>)}
      </select>
    </label>
  );

  if (stereo) return <div className="space-y-3">{lateralSlider}{stemEqSelect}</div>;

  return (
    <div className="space-y-3">
      {lateralSlider}
      <label className="block text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1"><ArrowUpDown className="h-3 w-3" />Front <span className="ml-auto">Back</span></span>
        <Slider aria-label="Front to back" className="mt-1.5" min={0} max={1} step={0.01}
          value={[position.depth]} onValueChange={([depth]) => move({ depth })} />
      </label>
      {hasHeight && (
        <label className="block text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><MoveVertical className="h-3 w-3" />Floor <span className="ml-auto">Height</span></span>
          <Slider aria-label="Floor to height" className="mt-1.5" min={0} max={1} step={0.01}
            value={[height]}
            onValueChange={([value]) => onPlacement({ ...placement, elevation_deg: value * maxElevationDeg })} />
        </label>
      )}
      {hasLfe && (
        <label className="block text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><Waves className="h-3 w-3" />LFE send</span>
          <Slider aria-label="LFE send" className="mt-1.5" min={0} max={1} step={0.01}
            value={[route.LFE ?? 0]} onValueChange={([lfe]) => onRoute({ LFE: lfe })} />
        </label>
      )}
      {stemEqSelect}
    </div>
  );
});
