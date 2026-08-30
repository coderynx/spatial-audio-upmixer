import * as React from "react";
import { createPortal } from "react-dom";
import { CloudFog, MoveVertical, UserRound, Waves, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { getStemColor, getStemIcon } from "@/lib/stems";
import type { StemPlacement } from "./wasmEngine/panner";

export type PannerPosition = { lateral: number; depth: number };

const KEY_STEP = 0.02;

function clamp(value: number) {
  return Math.min(1, Math.max(0, value));
}

/** `azimuth = atan2(-x, -z)`: front is 0°, left is positive. */
export function azimuthFromPosition({ lateral, depth }: PannerPosition): number {
  const x = lateral * 2 - 1;
  const z = depth * 2 - 1;
  if (x === 0 && z === 0) return 0;
  return (Math.atan2(-x, -z) * 180) / Math.PI;
}

export function positionFromAzimuth(azimuthDeg: number): PannerPosition {
  const azimuth = (azimuthDeg * Math.PI) / 180;
  return {
    lateral: (-Math.sin(azimuth) + 1) / 2,
    depth: (-Math.cos(azimuth) + 1) / 2,
  };
}

export function pannerPositionFromPlacement(placement: StemPlacement): PannerPosition {
  return positionFromAzimuth(placement.azimuth_deg);
}

export function placementFromPannerPosition(placement: StemPlacement, position: PannerPosition): StemPlacement {
  return { ...placement, azimuth_deg: azimuthFromPosition(position) };
}

/** Linked-stereo feeds keep their angular width at the centre handle's radius. */
export function objectChannelPositions(placement: StemPlacement, center = pannerPositionFromPlacement(placement)) {
  const halfWidth = placement.width_deg / 2;
  const radius = Math.hypot(center.lateral - 0.5, center.depth - 0.5);
  const position = (azimuthDeg: number) => {
    const azimuth = (azimuthDeg * Math.PI) / 180;
    return {
      lateral: clamp(0.5 - radius * Math.sin(azimuth)),
      depth: clamp(0.5 - radius * Math.cos(azimuth)),
    };
  };
  return {
    left: position(placement.azimuth_deg + halfWidth),
    right: position(placement.azimuth_deg - halfWidth),
  };
}

function horizontalGrid() {
  return Array.from({ length: 16 }, (_, index) => (
    <span key={index} className="border-r border-b border-border/70 [&:nth-child(4n)]:border-r-0 [&:nth-last-child(-n+4)]:border-b-0" />
  ));
}

function verticalGrid() {
  return Array.from({ length: 8 }, (_, index) => (
    <span key={index} className="border-r border-b border-border/70 [&:nth-child(4n)]:border-r-0 [&:nth-last-child(-n+4)]:border-b-0" />
  ));
}

function positionFromEvent(event: React.PointerEvent<HTMLDivElement>): PannerPosition {
  const rect = event.currentTarget.getBoundingClientRect();
  return {
    lateral: clamp((event.clientX - rect.left) / rect.width),
    depth: clamp((event.clientY - rect.top) / rect.height),
  };
}

export function ObjectPannerWindow({
  stemName,
  placement,
  maxElevationDeg,
  objectMode = "linked-stereo",
  route = {},
  channels = [],
  ambientRear = 0,
  ambientHeight = 0,
  ambientHeightCrossoverHz = 2000,
  ariaLabel = "Object panner",
  onPlacement,
  onObjectMode = () => {},
  onRoute = () => {},
  onAmbient = () => {},
}: {
  stemName: string;
  placement: StemPlacement;
  maxElevationDeg: number;
  objectMode?: "linked-stereo" | "mono";
  route?: Record<string, number>;
  channels?: string[];
  ambientRear?: number;
  ambientHeight?: number;
  ambientHeightCrossoverHz?: number;
  ariaLabel?: string;
  onPlacement: (next: StemPlacement) => void;
  onObjectMode?: (mode: "linked-stereo" | "mono") => void;
  onRoute?: (patch: Record<string, number>) => void;
  onAmbient?: (patch: { rear?: number; height?: number; heightCrossoverHz?: number }) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [windowPosition, setWindowPosition] = React.useState<{ left: number; top: number } | null>(null);
  const windowRef = React.useRef<HTMLDivElement>(null);
  const dragRef = React.useRef<{ pointerId: number; offsetX: number; offsetY: number } | null>(null);
  const pannerDraggingRef = React.useRef(false);
  const lastLocalPlacementRef = React.useRef<{ azimuth: number; elevation: number } | null>(null);
  const [position, setCartesianPosition] = React.useState(() => pannerPositionFromPlacement(placement));
  const placementAzimuth = placement.azimuth_deg;
  const placementElevation = placement.elevation_deg;
  const [elevation, setElevation] = React.useState(() => maxElevationDeg ? clamp(placementElevation / maxElevationDeg) : 0);
  const stereo = objectMode === "linked-stereo";
  const hasSurround = channels.includes("SL") || channels.includes("SR") || channels.includes("BL") || channels.includes("BR");
  const hasHeight = channels.includes("TFL") || channels.includes("TFR") || channels.includes("TBL") || channels.includes("TBR");
  const hasLfe = channels.includes("LFE");
  const heightCrossover = Math.min(4000, Math.max(500, ambientHeightCrossoverHz));
  const heightCrossoverPosition = Math.log(heightCrossover / 500) / Math.log(8);
  const StemIcon = getStemIcon(stemName);
  const stemColor = getStemColor(stemName);
  const channelPositions = objectChannelPositions({ ...placement, azimuth_deg: azimuthFromPosition(position) }, position);
  const setPosition = (next: PannerPosition) => {
    const nextPlacement = placementFromPannerPosition(placement, next);
    setCartesianPosition(next);
    lastLocalPlacementRef.current = { azimuth: nextPlacement.azimuth_deg, elevation: nextPlacement.elevation_deg };
    onPlacement(nextPlacement);
  };
  const movePosition = (event: React.PointerEvent<HTMLDivElement>) => setPosition(positionFromEvent(event));
  const setElevationPosition = (lateral: number, nextElevation: number) => {
    const nextPosition = { lateral, depth: position.depth };
    const nextPlacement = {
      ...placement,
      azimuth_deg: azimuthFromPosition(nextPosition),
      elevation_deg: clamp(nextElevation) * maxElevationDeg,
    };
    setCartesianPosition(nextPosition);
    setElevation(clamp(nextElevation));
    lastLocalPlacementRef.current = { azimuth: nextPlacement.azimuth_deg, elevation: nextPlacement.elevation_deg };
    onPlacement(nextPlacement);
  };
  const moveElevationPosition = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setElevationPosition(
      clamp((event.clientX - rect.left) / rect.width),
      clamp(1 - (event.clientY - rect.top) / rect.height),
    );
  };

  React.useEffect(() => {
    if (open) windowRef.current?.focus();
  }, [open]);

  React.useEffect(() => {
    if (pannerDraggingRef.current || Math.abs((lastLocalPlacementRef.current?.azimuth ?? Infinity) - placementAzimuth) < 1e-6) return;
    setCartesianPosition(positionFromAzimuth(placementAzimuth));
  }, [placementAzimuth]);

  React.useEffect(() => {
    if (pannerDraggingRef.current || Math.abs((lastLocalPlacementRef.current?.elevation ?? Infinity) - placementElevation) < 1e-6) return;
    setElevation(maxElevationDeg ? clamp(placementElevation / maxElevationDeg) : 0);
  }, [placementElevation, maxElevationDeg]);

  const placeWindow = (left: number, top: number) => {
    const rect = windowRef.current?.getBoundingClientRect();
    if (!rect) return;
    setWindowPosition({
      left: Math.min(window.innerWidth - 64, Math.max(64 - rect.width, left)),
      top: Math.min(window.innerHeight - 32, Math.max(0, top)),
    });
  };

  const floatingWindow = open && createPortal(
    <div className="pointer-events-none fixed inset-0 z-50">
      <div
        ref={windowRef}
        role="dialog"
        aria-modal="false"
        aria-label={ariaLabel}
        tabIndex={-1}
        className="pointer-events-auto fixed max-h-[calc(100vh-24px)] w-[420px] max-w-[calc(100vw-24px)] overflow-auto rounded-lg border bg-card shadow-2xl outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        style={{
          borderColor: `${stemColor}40`,
          ...(windowPosition
            ? { left: windowPosition.left, top: windowPosition.top }
            : { left: "50%", top: "50%", transform: "translate(-50%, -50%)" }),
        }}
        onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}
      >
        <div
          tabIndex={0}
          aria-label="Move object panner window"
          className="flex h-8 touch-none cursor-default items-center border-b px-3 text-[12px] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
          onPointerDown={(event) => {
            if (event.button !== 0 || !windowRef.current) return;
            const rect = windowRef.current.getBoundingClientRect();
            dragRef.current = { pointerId: event.pointerId, offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top };
            event.currentTarget.setPointerCapture(event.pointerId);
            setWindowPosition({ left: rect.left, top: rect.top });
          }}
          onPointerMove={(event) => {
            const drag = dragRef.current;
            if (drag?.pointerId === event.pointerId) placeWindow(event.clientX - drag.offsetX, event.clientY - drag.offsetY);
          }}
          onPointerUp={(event) => {
            if (dragRef.current?.pointerId !== event.pointerId) return;
            dragRef.current = null;
            event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onKeyDown={(event) => {
            if (!event.key.startsWith("Arrow") || !windowRef.current) return;
            const rect = windowRef.current.getBoundingClientRect();
            const step = event.shiftKey ? 40 : 10;
            event.preventDefault();
            placeWindow(
              rect.left + (event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0),
              rect.top + (event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0),
            );
          }}
        >
          <StemIcon className="mr-1.5 h-3.5 w-3.5 shrink-0" style={{ color: stemColor }} aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate">{stemName} Panner</span>
          <button
            type="button"
            aria-label="Close object panner"
            className="-mr-1 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => setOpen(false)}
          ><X className="h-3.5 w-3.5" /></button>
        </div>
        <div className="flex flex-col items-center gap-6 px-6 py-5">
          <section className="w-full" aria-label="Left/right and back/front position">
            <div className="mb-1 grid grid-cols-[44px_minmax(0,1fr)_44px] text-[12px] text-muted-foreground"><span className="col-start-2 text-center">Front</span></div>
            <div className="grid grid-cols-[44px_minmax(0,1fr)_44px] items-center text-[12px] text-muted-foreground">
              <span className="pr-3 text-right">Left</span>
              <div
                role="group"
                tabIndex={0}
                aria-label="Left/right and back/front"
                aria-description="Drag the centre puck or use arrow keys to pan left, right, front, and back. Option-click resets to centre front."
                className="relative aspect-square touch-none bg-muted/50 outline-none ring-1 ring-border focus-visible:ring-2 focus-visible:ring-ring/60"
                onPointerDown={(event) => {
                  if (event.altKey) { setPosition(positionFromAzimuth(0)); return; }
                  pannerDraggingRef.current = true;
                  event.currentTarget.setPointerCapture(event.pointerId);
                  movePosition(event);
                }}
                onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) movePosition(event); }}
                onPointerUp={(event) => { pannerDraggingRef.current = false; event.currentTarget.releasePointerCapture(event.pointerId); }}
                onPointerCancel={() => { pannerDraggingRef.current = false; }}
                onKeyDown={(event) => {
                  const next = { ...position };
                  if (event.key === "ArrowLeft") next.lateral -= KEY_STEP;
                  else if (event.key === "ArrowRight") next.lateral += KEY_STEP;
                  else if (event.key === "ArrowUp") next.depth -= KEY_STEP;
                  else if (event.key === "ArrowDown") next.depth += KEY_STEP;
                  else return;
                  event.preventDefault();
                  setPosition({ lateral: clamp(next.lateral), depth: clamp(next.depth) });
                }}
              >
                <div aria-hidden="true" className="absolute inset-0 grid grid-cols-4 grid-rows-4">{horizontalGrid()}</div>
                {stereo && <><span data-channel="left" className="pointer-events-none absolute z-10 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold text-foreground" style={{ left: `${channelPositions.left.lateral * 100}%`, top: `${channelPositions.left.depth * 100}%` }}>L</span>
                <span data-channel="right" className="pointer-events-none absolute z-10 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold text-foreground" style={{ left: `${channelPositions.right.lateral * 100}%`, top: `${channelPositions.right.depth * 100}%` }}>R</span></>}
                <UserRound aria-hidden="true" className="pointer-events-none absolute left-1/2 top-1/2 h-9 w-9 -translate-x-1/2 -translate-y-1/2 text-muted-foreground/60" />
                <span
                  data-drag-handle="horizontal"
                  className="pointer-events-none absolute z-20 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-primary bg-card shadow-sm ring-4 ring-primary/20"
                  style={{ left: `${position.lateral * 100}%`, top: `${position.depth * 100}%` }}
                />
              </div>
              <span className="pl-3">Right</span>
            </div>
            <div className="mt-1 grid grid-cols-[44px_minmax(0,1fr)_44px] text-[12px] text-muted-foreground"><span className="col-start-2 text-center">Back</span></div>
          </section>
          {maxElevationDeg > 0 && <section className="w-full" aria-label="Elevation">
            <div className="grid grid-cols-[44px_minmax(0,1fr)_44px] items-stretch text-[12px] text-muted-foreground">
              <div
                role="group"
                tabIndex={0}
                aria-label="Elevation"
                aria-description="Drag the centre puck or use arrow keys to set elevation. Option-click resets to ear level."
                className="relative col-start-2 h-40 touch-none bg-muted/50 outline-none ring-1 ring-border focus-visible:ring-2 focus-visible:ring-ring/60"
                onPointerDown={(event) => {
                  if (event.altKey) { setElevationPosition(position.lateral, 0); return; }
                  pannerDraggingRef.current = true;
                  event.currentTarget.setPointerCapture(event.pointerId);
                  moveElevationPosition(event);
                }}
                onPointerMove={(event) => {
                  if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
                  moveElevationPosition(event);
                }}
                onPointerUp={(event) => { pannerDraggingRef.current = false; event.currentTarget.releasePointerCapture(event.pointerId); }}
                onPointerCancel={() => { pannerDraggingRef.current = false; }}
                onKeyDown={(event) => {
                  let lateral = position.lateral;
                  let nextElevation = elevation;
                  if (event.key === "ArrowLeft") lateral -= KEY_STEP;
                  else if (event.key === "ArrowRight") lateral += KEY_STEP;
                  else if (event.key === "ArrowUp") nextElevation += 1 / maxElevationDeg;
                  else if (event.key === "ArrowDown") nextElevation -= 1 / maxElevationDeg;
                  else return;
                  event.preventDefault();
                  setElevationPosition(clamp(lateral), clamp(nextElevation));
                }}
              >
                <div aria-hidden="true" className="absolute inset-0 grid grid-cols-4 grid-rows-2">{verticalGrid()}</div>
                <UserRound aria-hidden="true" className="pointer-events-none absolute bottom-0 left-1/2 h-9 w-9 -translate-x-1/2 text-muted-foreground/60" />
                {stereo && <><span className="pointer-events-none absolute z-10 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold text-foreground" style={{ left: `${channelPositions.left.lateral * 100}%`, top: `${(1 - elevation) * 100}%` }}>L</span>
                <span className="pointer-events-none absolute z-10 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold text-foreground" style={{ left: `${channelPositions.right.lateral * 100}%`, top: `${(1 - elevation) * 100}%` }}>R</span></>}
                <span
                  data-drag-handle="elevation"
                  className="pointer-events-none absolute z-20 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-primary bg-card shadow-sm ring-4 ring-primary/20"
                  style={{ left: `${position.lateral * 100}%`, top: `${(1 - elevation) * 100}%` }}
                />
              </div>
              <div className="col-start-3 flex flex-col justify-between py-0.5 pl-3"><span>Elevation</span><span>Ear level</span></div>
            </div>
          </section>}
          <div className="w-full space-y-3 border-t pt-4">
            <label className="block text-[11px] text-muted-foreground">
              <span>Direct image</span>
              <select aria-label="Direct image" className="mt-1.5 flex h-7 w-full rounded-md border bg-secondary px-2 text-[13px] text-foreground"
                value={objectMode} onChange={(event) => onObjectMode(event.target.value as "linked-stereo" | "mono")}>
                <option value="linked-stereo">Linked stereo</option><option value="mono">Mono</option>
              </select>
            </label>
            <div className={`grid gap-3${stereo ? " sm:grid-cols-2" : ""}`}>
              {stereo && <label className="block text-[11px] text-muted-foreground">
                <span className="flex items-center"><span>Spread</span><span className="ml-auto tabular-nums">{Math.round(placement.width_deg)}°</span></span>
                <Slider aria-label="Stereo spread" className="mt-1.5" min={0} max={360} step={1}
                  value={[placement.width_deg]} onValueChange={([width_deg]) => onPlacement({ ...placement, width_deg })} />
              </label>}
              <label className="block text-[11px] text-muted-foreground">
                <span className="flex items-center"><span>Size</span><span className="ml-auto tabular-nums">{Math.round(placement.object_size * 100)}%</span></span>
                <Slider aria-label="Object size" className="mt-1.5" min={0} max={1} step={0.01}
                  value={[placement.object_size]} onValueChange={([object_size]) => onPlacement({ ...placement, object_size })} />
              </label>
            </div>
            {(hasSurround || hasHeight || hasLfe) && <div className="grid gap-3 sm:grid-cols-2">
              {hasSurround && <label className="block text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1"><CloudFog className="h-3 w-3" />Ambience to rear</span>
                <Slider aria-label="Ambience to rear" className="mt-1.5" min={0} max={1} step={0.01}
                  value={[ambientRear]} onValueChange={([rear]) => onAmbient({ rear })} />
              </label>}
              {hasHeight && <label className="block text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1"><CloudFog className="h-3 w-3" />Ambience to height</span>
                <Slider aria-label="Ambience to height" className="mt-1.5" min={0} max={1} step={0.01}
                  value={[ambientHeight]} onValueChange={([height]) => onAmbient({ height })} />
              </label>}
              {hasHeight && <label className="block text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1"><MoveVertical className="h-3 w-3" />Height crossover <span className="ml-auto">{Math.round(heightCrossover)} Hz</span></span>
                <Slider aria-label="Height crossover" className="mt-1.5" min={0} max={1} step={0.01}
                  value={[heightCrossoverPosition]} onValueChange={([value]) => onAmbient({ heightCrossoverHz: 500 * 8 ** value })} />
              </label>}
              {hasLfe && <label className="block text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1"><Waves className="h-3 w-3" />LFE send</span>
                <Slider aria-label="LFE send" className="mt-1.5" min={0} max={1} step={0.01}
                  value={[route.LFE ?? 0]} onValueChange={([lfe]) => onRoute({ LFE: lfe })} />
              </label>}
            </div>}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );

  return (
    <>
      <Button
        className="relative h-10 w-10 overflow-hidden p-0"
        variant="outline"
        size="icon"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen(true)}
      >
        <span data-panner-preview className="relative h-7 w-7 border border-border/70 bg-muted/50">
          <span aria-hidden="true" className="absolute inset-x-1/2 top-0 h-px -translate-x-1/2 bg-border" />
          <span aria-hidden="true" className="absolute inset-y-1/2 left-0 w-px -translate-y-1/2 bg-border" />
          <span aria-hidden="true" className="absolute inset-y-1/2 right-0 w-px -translate-y-1/2 bg-border" />
          <span data-panner-preview-puck className="absolute z-20 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border border-primary bg-card ring-2 ring-primary/20" style={{ left: `${position.lateral * 100}%`, top: `${position.depth * 100}%` }} />
        </span>
      </Button>
      {floatingWindow}
    </>
  );
}
