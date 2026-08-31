import * as React from "react";
import { ArrowLeftRight, ArrowUpDown, CloudFog, MoveVertical, Waves } from "lucide-react";
import { Slider } from "@/components/ui/slider";
import type { StemDynamicEqBand, StemDynamicEqSettings, StemDynamicsSettings, StemEqSettings } from "@/lib/manifest";
import { azimuthFromPosition, positionFromAzimuth, type PannerPosition } from "./ObjectPannerWindow";
import type { StemPlacement } from "./wasmEngine/panner";
import { StemEqWindow } from "./StemEqWindow";
import { StemDynamicsWindow } from "./StemDynamicsWindow";
import { StemDynamicEqWindow } from "./StemDynamicEqWindow";

/** Slider positions on the floor plane, both 0..1. `lateral` runs left to
 * right, `depth` front to back; together they name a direction, which is what
 * the panner takes. Dead centre (0.5, 0.5) has no direction of its own and
 * resolves to front. */
type Position = PannerPosition;

/** Half the slider spends the image out to the side pair; the rest turns it
 * around. A placement wider than the full arc reads as fully wrapped. */
const SIDE_ARC_DEG = 180;

/** `azimuth = atan2(-x, -z)`, matching `binaural/geometry.py`'s convention:
 * 0 = front, positive = left, listener facing -Z. */
export { azimuthFromPosition, positionFromAzimuth } from "./ObjectPannerWindow";

export type StemProcessingControlsProps = {
  stemName: string;
  eq: string | StemEqSettings;
  onEq: (eq: string | StemEqSettings | null) => void;
  onDynamicEq?: (value: StemDynamicEqSettings | null) => void;
  onDynamics: (value: StemDynamicsSettings | null) => void;
  stemEqProfiles?: string[];
  stemEqSettings?: Record<string, StemEqSettings>;
  dynamics?: StemDynamicsSettings;
  dynamicEq?: StemDynamicEqSettings;
  dynamicEqProfiles?: Record<string, StemDynamicEqBand[]>;
  dynamicsProfiles?: Record<string, StemDynamicsSettings>;
  stemProcessingPresets?: {
    eq: Record<string, string[]>;
    dynamic_eq: Record<string, string[]>;
    dynamics: Record<string, string[]>;
  };
  dynamicsMeterSource?: () => number;
  dynamicEqMeterSource?: () => number;
};

export function StemProcessingControls({
  stemName, eq, onEq, onDynamicEq = () => undefined, onDynamics, stemEqProfiles, stemEqSettings, dynamics,
  dynamicEq, dynamicEqProfiles, dynamicsProfiles, stemProcessingPresets, dynamicsMeterSource, dynamicEqMeterSource,
}: StemProcessingControlsProps) {
  const presetStemName = stemName.split("@", 1)[0];
  const selectProfiles = <T,>(profiles: Record<string, T> | undefined, block: keyof NonNullable<typeof stemProcessingPresets>) => Object.fromEntries((stemProcessingPresets?.[block][presetStemName] ?? Object.keys(profiles ?? {})).map((name) => [name, profiles?.[name]]).filter(([, profile]) => profile));

  return <div className="flex w-full flex-col gap-1.5">
    <StemEqWindow stemName={stemName} eq={eq} profiles={stemProcessingPresets?.eq[presetStemName] ?? stemEqProfiles} settings={stemEqSettings} onChange={onEq} />
    <StemDynamicEqWindow stemName={stemName} value={dynamicEq} profiles={selectProfiles(dynamicEqProfiles, "dynamic_eq")} onChange={onDynamicEq} meterSource={dynamicEqMeterSource} />
    <StemDynamicsWindow stemName={stemName} value={dynamics} profiles={selectProfiles(dynamicsProfiles, "dynamics")} onChange={onDynamics} meterSource={dynamicsMeterSource} />
  </div>;
}

export const StemControls = React.memo(function StemControls({
  stemName = "Stem", placement, route, channels, eq, maxElevationDeg, ambientRear, ambientHeight, ambientHeightCrossoverHz,
  onPlacement, onRoute, onEq, onDynamicEq = () => undefined, onDynamics, onAmbient, stemEqProfiles, stemEqSettings, dynamicEq, dynamicEqProfiles, dynamicsProfiles, stemProcessingPresets, dynamics, dynamicsMeterSource, dynamicEqMeterSource, showPositionControls = true, showObjectSends = true, showProcessingControls = true,
}: {
  stemName?: string;
  placement: StemPlacement;
  route: Record<string, number>;
  channels: string[];
  eq: string | StemEqSettings;
  maxElevationDeg: number;
  /** Fraction of the stem's ambient half sent to the surrounds / heights. */
  ambientRear: number;
  ambientHeight: number;
  ambientHeightCrossoverHz: number;
  onPlacement: (next: StemPlacement) => void;
  onRoute: (patch: Record<string, number>) => void;
  onEq: (eq: string | StemEqSettings | null) => void;
  onDynamicEq?: (value: StemDynamicEqSettings | null) => void;
  onDynamics: (value: StemDynamicsSettings | null) => void;
  onAmbient: (patch: { rear?: number; height?: number; heightCrossoverHz?: number }) => void;
  stemEqProfiles?: string[];
  stemEqSettings?: Record<string, StemEqSettings>;
  dynamics?: StemDynamicsSettings;
  dynamicEq?: StemDynamicEqSettings;
  dynamicEqProfiles?: Record<string, StemDynamicEqBand[]>;
  dynamicsProfiles?: Record<string, StemDynamicsSettings>;
  stemProcessingPresets?: {
    eq: Record<string, string[]>;
    dynamic_eq: Record<string, string[]>;
    dynamics: Record<string, string[]>;
  };
  dynamicsMeterSource?: () => number;
  dynamicEqMeterSource?: () => number;
  showPositionControls?: boolean;
  showObjectSends?: boolean;
  showProcessingControls?: boolean;
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

  // Front/back as the panner expresses it: width is what carries a centred
  // image off the front wall out to the sides — half the slider — and past the
  // sides it is the azimuth that turns the image around. Keeping both on one
  // control is what the routing presets vary, and it leaves the left/right
  // slider the only thing that moves a stem laterally.
  const facesRear = Math.abs(placement.azimuth_deg) > 90;
  const wrap = Math.min(1, placement.width_deg / (SIDE_ARC_DEG * 2));
  const depth = facesRear ? 1 - wrap : wrap;
  const moveDepth = (value: number) => {
    const behind = value > 0.5;
    setPosition((current) => ({ ...current, depth: behind ? 1 : 0 }));
    onPlacement({
      ...placement,
      azimuth_deg: azimuthFromPosition({ lateral: position.lateral, depth: behind ? 1 : 0 }),
      width_deg: (behind ? 1 - value : value) * SIDE_ARC_DEG * 2,
    });
  };

  const stereo = channels.length === 2;
  const hasHeight = channels.includes("TFL") || channels.includes("TFR")
    || channels.includes("TBL") || channels.includes("TBR");
  const hasLfe = channels.includes("LFE");
  const hasSurround = channels.includes("SL") || channels.includes("SR")
    || channels.includes("BL") || channels.includes("BR");
  const height = maxElevationDeg > 0
    ? Math.min(1, Math.max(0, placement.elevation_deg / maxElevationDeg))
    : 0;
  const heightCrossover = Math.min(4000, Math.max(500, ambientHeightCrossoverHz));
  const heightCrossoverPosition = Math.log(heightCrossover / 500) / Math.log(8);

  const lateralSlider = (
    <label className="block text-[11px] text-muted-foreground">
      <span className="flex items-center gap-1"><ArrowLeftRight className="h-3 w-3" />Left <span className="ml-auto">Right</span></span>
      <Slider aria-label="Left to right" className="mt-1.5" min={0} max={1} step={0.01}
        value={[position.lateral]} onValueChange={([lateral]) => move({ lateral })} />
    </label>
  );
  const processingControls = showProcessingControls && <StemProcessingControls
    stemName={stemName} eq={eq} onEq={onEq} onDynamicEq={onDynamicEq} onDynamics={onDynamics}
    stemEqProfiles={stemEqProfiles} stemEqSettings={stemEqSettings} dynamics={dynamics} dynamicEq={dynamicEq}
    dynamicEqProfiles={dynamicEqProfiles} dynamicsProfiles={dynamicsProfiles} stemProcessingPresets={stemProcessingPresets}
    dynamicsMeterSource={dynamicsMeterSource} dynamicEqMeterSource={dynamicEqMeterSource}
  />;

  if (stereo) return <div className="space-y-3">{showPositionControls && lateralSlider}{processingControls}</div>;

  return (
    <div className="space-y-3">
      {showPositionControls && lateralSlider}
      {showPositionControls && <label className="block text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1"><ArrowUpDown className="h-3 w-3" />Front <span className="ml-auto">Back</span></span>
        <Slider aria-label="Front to back" className="mt-1.5" min={0} max={1} step={0.01}
          value={[depth]} onValueChange={([value]) => moveDepth(value)} />
      </label>}
      {showPositionControls && hasHeight && (
        <label className="block text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><MoveVertical className="h-3 w-3" />Floor <span className="ml-auto">Height</span></span>
          <Slider aria-label="Floor to height" className="mt-1.5" min={0} max={1} step={0.01}
            value={[height]}
            onValueChange={([value]) => onPlacement({ ...placement, elevation_deg: value * maxElevationDeg })} />
        </label>
      )}
      {showObjectSends && hasSurround && (
        <label className="block text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><CloudFog className="h-3 w-3" />Ambience to rear</span>
          <Slider aria-label="Ambience to rear" className="mt-1.5" min={0} max={1} step={0.01}
            value={[ambientRear]} onValueChange={([rear]) => onAmbient({ rear })} />
        </label>
      )}
      {showObjectSends && hasHeight && (
        <>
          <label className="block text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1"><CloudFog className="h-3 w-3" />Ambience to height</span>
            <Slider aria-label="Ambience to height" className="mt-1.5" min={0} max={1} step={0.01}
              value={[ambientHeight]} onValueChange={([height]) => onAmbient({ height })} />
          </label>
          <label className="block text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1"><MoveVertical className="h-3 w-3" />Height crossover <span className="ml-auto">{Math.round(heightCrossover)} Hz</span></span>
            <Slider aria-label="Height crossover" className="mt-1.5" min={0} max={1} step={0.01}
              value={[heightCrossoverPosition]} onValueChange={([value]) => onAmbient({ heightCrossoverHz: 500 * 8 ** value })} />
          </label>
        </>
      )}
      {showObjectSends && hasLfe && (
        <label className="block text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1"><Waves className="h-3 w-3" />LFE send</span>
          <Slider aria-label="LFE send" className="mt-1.5" min={0} max={1} step={0.01}
            value={[route.LFE ?? 0]} onValueChange={([lfe]) => onRoute({ LFE: lfe })} />
        </label>
      )}
      {processingControls}
    </div>
  );
});
