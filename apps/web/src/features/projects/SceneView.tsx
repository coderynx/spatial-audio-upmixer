import * as React from "react";
import type { StemRouting } from "@/api";
import { SETTLE_FRAMES, canvasTheme, hexToRgb } from "@/lib/canvasTheme";
import { isBedStem } from "@/lib/stems";
import { speakerCoordinates, stemPosition, stemPositionStereo, type Vec3 } from "@/lib/spatial";
import { cn } from "@/lib/utils";
import type { StemSpectrum } from "./audioEngine";

const DEFAULT_CAMERA = { yaw: 35 * Math.PI / 180, pitch: 22 * Math.PI / 180 };
const MIN_PITCH = 10 * Math.PI / 180;
const MAX_PITCH = 65 * Math.PI / 180;
const MAX_HEIGHT = 0.6;

type Camera = { yaw: number; pitch: number };
type ProjectedPoint = { x: number; y: number; depth: number; scale: number };
type Voice = {
  key: string;
  stem: string;
  base: string;
  kind: "object" | "bed";
  position: Vec3;
  lobes?: { position: Vec3; weight: number }[];
};
type HitTarget = { stem: string; x: number; y: number; radius: number };

function dot(a: Vec3, b: Vec3) {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

function cross(a: Vec3, b: Vec3): Vec3 {
  return {
    x: a.y * b.z - a.z * b.y,
    y: a.z * b.x - a.x * b.z,
    z: a.x * b.y - a.y * b.x,
  };
}

function normalize(vector: Vec3): Vec3 {
  const length = Math.hypot(vector.x, vector.y, vector.z) || 1;
  return { x: vector.x / length, y: vector.y / length, z: vector.z / length };
}

export function projectScenePoint(point: Vec3, width: number, height: number, camera: Camera): ProjectedPoint {
  const target = { x: 0, y: 0.5, z: 0 };
  const distance = 4;
  const cameraPosition = {
    x: Math.sin(camera.yaw) * Math.cos(camera.pitch) * distance,
    y: target.y + Math.sin(camera.pitch) * distance,
    z: Math.cos(camera.yaw) * Math.cos(camera.pitch) * distance,
  };
  const forward = normalize({
    x: target.x - cameraPosition.x,
    y: target.y - cameraPosition.y,
    z: target.z - cameraPosition.z,
  });
  const right = normalize(cross(forward, { x: 0, y: 1, z: 0 }));
  const up = cross(right, forward);
  const offset = {
    x: point.x - cameraPosition.x,
    y: point.y - cameraPosition.y,
    z: point.z - cameraPosition.z,
  };
  const depth = Math.max(0.1, dot(offset, forward));
  const focal = Math.min(width, height) * 1.15;
  const scale = focal / depth;
  return {
    x: width / 2 + dot(offset, right) * scale,
    y: height / 2 - dot(offset, up) * scale,
    depth,
    scale,
  };
}

function scenePosition(position: Vec3): Vec3 {
  return {
    x: position.x,
    y: 0.08 + Math.min(1, Math.max(0, position.y / MAX_HEIGHT)) * 0.84,
    z: position.z,
  };
}

export function isObjectStem(stem: string, objectStems: ReadonlySet<string>) {
  return !isBedStem(stem) && (objectStems.has(stem) || objectStems.has(stem.split("@", 1)[0]));
}

export type SceneViewProps = {
  routing: StemRouting;
  objectStems: ReadonlySet<string>;
  selectedStem: string | null;
  colors: Record<string, string>;
  channelCounts?: Record<string, number>;
  onSelectStem: (stem: string | null) => void;
  stemSpectrum: React.MutableRefObject<Map<string, StemSpectrum>>;
  active: boolean;
  className?: string;
};

function SceneViewImpl({
  routing,
  objectStems,
  selectedStem,
  colors,
  channelCounts,
  onSelectStem,
  stemSpectrum,
  active,
  className,
}: SceneViewProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const camera = React.useRef<Camera>({ ...DEFAULT_CAMERA });
  const levels = React.useRef(new Map<string, number>());
  const hits = React.useRef<HitTarget[]>([]);
  const frame = React.useRef<number | null>(null);
  const idleFrames = React.useRef(0);
  const wakeRef = React.useRef<() => void>(() => {});
  const drag = React.useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const propsRef = React.useRef({ routing, objectStems, selectedStem, colors, channelCounts });
  propsRef.current = { routing, objectStems, selectedStem, colors, channelCounts };
  const activeRef = React.useRef(active);
  activeRef.current = active;

  React.useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(container.clientWidth * dpr));
      canvas.height = Math.max(1, Math.round(container.clientHeight * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(() => {
      resize();
      wakeRef.current();
    });
    observer.observe(container);

    let lastTime = performance.now();
    const drawLine = (from: Vec3, to: Vec3, width: number, height: number) => {
      const a = projectScenePoint(from, width, height, camera.current);
      const b = projectScenePoint(to, width, height, camera.current);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    };
    const draw = (time: number) => {
      const delta = Math.min(0.1, (time - lastTime) / 1000);
      lastTime = time;
      const width = canvas.width / (window.devicePixelRatio || 1);
      const height = canvas.height / (window.devicePixelRatio || 1);
      const { routing: currentRouting, objectStems: currentObjectStems, selectedStem: currentSelected, colors: currentColors, channelCounts: currentCounts } = propsRef.current;

      const field = ctx.createLinearGradient(0, 0, 0, height);
      field.addColorStop(0, canvasTheme.plotFieldCore);
      field.addColorStop(1, canvasTheme.plotField);
      ctx.fillStyle = field;
      ctx.fillRect(0, 0, width, height);

      const corners: Vec3[] = [
        { x: -1, y: 0, z: -1 }, { x: 1, y: 0, z: -1 }, { x: 1, y: 0, z: 1 }, { x: -1, y: 0, z: 1 },
        { x: -1, y: 1, z: -1 }, { x: 1, y: 1, z: -1 }, { x: 1, y: 1, z: 1 }, { x: -1, y: 1, z: 1 },
      ];
      const floor = corners.slice(0, 4).map((point) => projectScenePoint(point, width, height, camera.current));
      ctx.save();
      ctx.globalAlpha = 0.8;
      ctx.fillStyle = canvasTheme.plotShadeStrong;
      ctx.beginPath();
      ctx.moveTo(floor[0].x, floor[0].y);
      for (const point of floor.slice(1)) ctx.lineTo(point.x, point.y);
      ctx.closePath();
      ctx.fill();
      ctx.restore();

      ctx.lineWidth = 1;
      ctx.strokeStyle = canvasTheme.gridSoft;
      for (let step = -0.75; step < 1; step += 0.5) {
        drawLine({ x: step, y: 0, z: -1 }, { x: step, y: 0, z: 1 }, width, height);
        drawLine({ x: -1, y: 0, z: step }, { x: 1, y: 0, z: step }, width, height);
      }
      ctx.strokeStyle = canvasTheme.grid;
      const edges = [
        [0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7],
      ];
      for (const [from, to] of edges) drawLine(corners[from], corners[to], width, height);

      const listener = projectScenePoint({ x: 0, y: 0.08, z: 0 }, width, height, camera.current);
      const listenerSize = Math.max(7, Math.min(14, listener.scale * 0.12));
      ctx.save();
      ctx.translate(listener.x, listener.y);
      ctx.fillStyle = canvasTheme.speaker;
      ctx.beginPath();
      ctx.arc(0, -listenerSize * 0.7, listenerSize * 0.42, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.ellipse(0, listenerSize * 0.32, listenerSize * 0.75, listenerSize * 0.46, 0, Math.PI, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = canvasTheme.ring;
      ctx.stroke();
      ctx.restore();

      const voices: Voice[] = [];
      for (const stem of Object.keys(currentRouting)) {
        const route = currentRouting[stem] || {};
        const base = stem.split("@", 1)[0];
        if (isObjectStem(stem, currentObjectStems) && (currentCounts?.[stem] ?? 2) >= 2) {
          const { left, right } = stemPositionStereo(route);
          voices.push({ key: `${stem}:L`, stem, base, kind: "object", position: scenePosition(left) });
          voices.push({ key: `${stem}:R`, stem, base, kind: "object", position: scenePosition(right) });
        } else if (isObjectStem(stem, currentObjectStems)) {
          voices.push({ key: stem, stem, base, kind: "object", position: scenePosition(stemPosition(route)) });
        } else {
          const lobes = Object.entries(route).flatMap(([channel, weight]) => {
            const position = speakerCoordinates[channel];
            return position && weight > 0 ? [{ position: scenePosition(position), weight }] : [];
          });
          voices.push({ key: stem, stem, base, kind: "bed", position: scenePosition(stemPosition(route)), lobes });
        }
      }

      const resolved = voices.map((voice) => {
        const target = stemSpectrum.current.get(voice.base)?.level ?? 0;
        const previous = levels.current.get(voice.key) ?? 0;
        const level = previous + (target - previous) * Math.min(1, delta * 8);
        levels.current.set(voice.key, level);
        return { voice, level, point: projectScenePoint(voice.position, width, height, camera.current) };
      }).sort((a, b) => b.point.depth - a.point.depth);

      const nextHits: HitTarget[] = [];
      ctx.save();
      ctx.globalCompositeOperation = "screen";
      for (const { voice, level, point } of resolved) {
        if (voice.kind !== "bed" || !voice.lobes?.length) continue;
        const activity = Math.min(1, level * 8);
        const emphasis = currentSelected && currentSelected !== voice.stem ? 0.45 : 1;
        const strongest = Math.max(...voice.lobes.map((lobe) => lobe.weight));
        const [r, g, b] = hexToRgb(currentColors[voice.stem] || canvasTheme.stemFallback);
        for (const lobe of voice.lobes) {
          const lobePoint = projectScenePoint(lobe.position, width, height, camera.current);
          const weight = lobe.weight / strongest;
          const radius = Math.max(18, Math.min(46, lobePoint.scale * 0.32)) * (0.5 + weight * 0.5);
          const haze = ctx.createRadialGradient(lobePoint.x, lobePoint.y, 0, lobePoint.x, lobePoint.y, radius);
          haze.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(0.14 + activity * 0.18) * weight * emphasis})`);
          haze.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
          ctx.fillStyle = haze;
          ctx.beginPath();
          ctx.arc(lobePoint.x, lobePoint.y, radius, 0, Math.PI * 2);
          ctx.fill();
        }
        nextHits.push({ stem: voice.stem, x: point.x, y: point.y, radius: Math.max(24, Math.min(80, point.scale * 0.45)) });
      }
      ctx.restore();

      for (const { voice, level, point } of resolved) {
        if (voice.kind !== "object") continue;
        const activity = Math.min(1, level * 8);
        const radius = Math.max(4, Math.min(11, point.scale * 0.08));
        const glowRadius = radius * (2 + activity * 2);
        const [r, g, b] = hexToRgb(currentColors[voice.stem] || canvasTheme.stemFallback);
        const glow = ctx.createRadialGradient(point.x, point.y, 0, point.x, point.y, glowRadius);
        glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${0.18 + activity * 0.52})`);
        glow.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(point.x, point.y, glowRadius, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${0.28 + activity * 0.72})`;
        ctx.beginPath();
        ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
        ctx.fill();
        if (voice.stem === currentSelected) {
          ctx.strokeStyle = canvasTheme.labelStrong;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(point.x, point.y, radius + 3, 0, Math.PI * 2);
          ctx.stroke();
        }
        nextHits.push({ stem: voice.stem, x: point.x, y: point.y, radius: Math.max(14, glowRadius) });
      }
      hits.current = nextHits;

      const quiet = resolved.every(({ level }) => level <= 0.003);
      idleFrames.current = !activeRef.current && quiet ? idleFrames.current + 1 : 0;
      if (activeRef.current || idleFrames.current < SETTLE_FRAMES) frame.current = window.requestAnimationFrame(draw);
      else frame.current = null;
    };
    frame.current = window.requestAnimationFrame(draw);
    wakeRef.current = () => {
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
      frame.current = null;
      idleFrames.current = 0;
      draw(performance.now());
    };
    return () => {
      observer.disconnect();
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    };
  }, [stemSpectrum]);

  React.useEffect(() => {
    wakeRef.current();
  }, [active, routing, objectStems, selectedStem, colors, channelCounts]);

  const selectAt = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let closest: { stem: string; distance: number } | null = null;
    for (const hit of hits.current) {
      const distance = Math.hypot(hit.x - x, hit.y - y);
      if (distance <= hit.radius && (!closest || distance < closest.distance)) closest = { stem: hit.stem, distance };
    }
    onSelectStem(closest ? (closest.stem === selectedStem ? null : closest.stem) : null);
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.focus();
    drag.current = { x: event.clientX, y: event.clientY, moved: false };
  };
  const handlePointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const current = drag.current;
    if (!current) return;
    const dx = event.clientX - current.x;
    const dy = event.clientY - current.y;
    if (Math.hypot(dx, dy) > 4) current.moved = true;
    if (!current.moved) return;
    camera.current.yaw += dx * 0.01;
    camera.current.pitch = Math.min(MAX_PITCH, Math.max(MIN_PITCH, camera.current.pitch + dy * 0.01));
    current.x = event.clientX;
    current.y = event.clientY;
    wakeRef.current();
  };
  const handlePointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const current = drag.current;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (current && !current.moved) selectAt(event);
  };
  const handleKeyDown = (event: React.KeyboardEvent<HTMLCanvasElement>) => {
    const turn = 5 * Math.PI / 180;
    if (event.key === "Home") camera.current = { ...DEFAULT_CAMERA };
    else if (event.key === "ArrowLeft") camera.current.yaw -= turn;
    else if (event.key === "ArrowRight") camera.current.yaw += turn;
    else if (event.key === "ArrowUp") camera.current.pitch = Math.max(MIN_PITCH, camera.current.pitch - turn);
    else if (event.key === "ArrowDown") camera.current.pitch = Math.min(MAX_PITCH, camera.current.pitch + turn);
    else return;
    event.preventDefault();
    wakeRef.current();
  };

  return <div className={cn("relative flex flex-col overflow-hidden rounded-lg border", className)} style={{ backgroundColor: canvasTheme.plotField }}>
    <div ref={containerRef} className="min-h-0 flex-1">
      <canvas
        ref={canvasRef}
        tabIndex={0}
        aria-label="3D object scene. Drag to orbit, use arrow keys to rotate, and Home to reset the view."
        className="h-full w-full touch-none cursor-grab outline-none active:cursor-grabbing focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => { drag.current = null; }}
        onKeyDown={handleKeyDown}
      />
    </div>
    <button
      type="button"
      onClick={() => onSelectStem(null)}
      className="absolute right-2 top-2 z-10 rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-[11px] text-white/70 backdrop-blur-sm transition-colors hover:bg-white/10 hover:text-white"
    >
      {selectedStem || "Aggregate output"}
    </button>
  </div>;
}

export default React.memo(SceneViewImpl);
