import * as React from "react";
import { createPortal } from "react-dom";
import { Crosshair, UserRound, X } from "lucide-react";
import { Button } from "@/components/ui/button";
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
  onPlacement,
}: {
  stemName: string;
  placement: StemPlacement;
  maxElevationDeg: number;
  onPlacement: (next: StemPlacement) => void;
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
        aria-label="Object panner"
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
          <section className="w-full" aria-label="Horizontal object panner">
            <div className="mb-1 grid grid-cols-[44px_minmax(0,1fr)_44px] text-[12px] text-muted-foreground"><span className="col-start-2 text-center">Front</span></div>
            <div className="grid grid-cols-[44px_minmax(0,1fr)_44px] items-center text-[12px] text-muted-foreground">
              <span className="pr-3 text-right">Left</span>
              <div
                role="group"
                tabIndex={0}
                aria-label="Object horizontal position"
                aria-description="Drag the dot or use arrow keys to pan left, right, front, and back."
                className="relative aspect-square touch-none bg-muted/50 outline-none ring-1 ring-border focus-visible:ring-2 focus-visible:ring-ring/60"
                onPointerDown={(event) => { pannerDraggingRef.current = true; event.currentTarget.setPointerCapture(event.pointerId); movePosition(event); }}
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
                <span data-channel="left" className="pointer-events-none absolute z-10 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold text-foreground" style={{ left: `${channelPositions.left.lateral * 100}%`, top: `${channelPositions.left.depth * 100}%` }}>L</span>
                <span data-channel="right" className="pointer-events-none absolute z-10 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold text-foreground" style={{ left: `${channelPositions.right.lateral * 100}%`, top: `${channelPositions.right.depth * 100}%` }}>R</span>
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
          {maxElevationDeg > 0 && <section className="w-full" aria-label="Vertical object panner">
            <div className="grid grid-cols-[44px_minmax(0,1fr)_44px] items-stretch text-[12px] text-muted-foreground">
              <div
                role="group"
                tabIndex={0}
                aria-label="Object elevation position"
                aria-description="Drag the dot or use arrow keys to pan horizontally and vertically."
                className="relative col-start-2 h-40 touch-none bg-muted/50 outline-none ring-1 ring-border focus-visible:ring-2 focus-visible:ring-ring/60"
                onPointerDown={(event) => {
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
                <span className="pointer-events-none absolute z-10 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold text-foreground" style={{ left: `${channelPositions.left.lateral * 100}%`, top: `${(1 - elevation) * 100}%` }}>L</span>
                <span className="pointer-events-none absolute z-10 flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-secondary text-[11px] font-semibold text-foreground" style={{ left: `${channelPositions.right.lateral * 100}%`, top: `${(1 - elevation) * 100}%` }}>R</span>
                <span
                  data-drag-handle="elevation"
                  className="pointer-events-none absolute z-20 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-primary bg-card shadow-sm ring-4 ring-primary/20"
                  style={{ left: `${position.lateral * 100}%`, top: `${(1 - elevation) * 100}%` }}
                />
              </div>
              <div className="col-start-3 flex flex-col justify-between py-0.5 pl-3"><span>Top</span><span>Ear level</span></div>
            </div>
          </section>}
        </div>
      </div>
    </div>,
    document.body,
  );

  return (
    <>
      <Button
        className="mb-3 w-full"
        variant="outline"
        size="sm"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      ><Crosshair />Object panner</Button>
      {floatingWindow}
    </>
  );
}
