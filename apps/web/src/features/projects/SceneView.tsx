import * as React from "react";
import type { StemRouting } from "@/api";
import {
  MIN_ALPHA_SCALE,
  SETTLE_FRAMES,
  canvasTheme,
  hexToRgb,
  lerp,
} from "@/lib/canvasTheme";
import { isBedStem } from "@/lib/stems";
import {
  speakerCoordinates,
  speakerDisplayLabel,
  stemPosition,
  stemPositionStereo,
  type Vec3,
} from "@/lib/spatial";
import { cn } from "@/lib/utils";
import type { MeterLevel, StemSpectrum } from "./audioEngine";
import { IntensitySlider } from "./IntensitySlider";
import { drawSpeakerPoint } from "./speakerMarker";
import { startSpatialCanvas } from "./spatialCanvas";

const DEFAULT_CAMERA = {
  yaw: (35 * Math.PI) / 180,
  pitch: (22 * Math.PI) / 180,
  distance: 4,
};
const MIN_PITCH = (10 * Math.PI) / 180;
const MAX_PITCH = (65 * Math.PI) / 180;
const MIN_DISTANCE = 2;
const MAX_DISTANCE = 7;
const MAX_HEIGHT = 0.6;
const COLOR_RESPONSE = 3;
const RELEASE_RESPONSE = 8;
const SILENCE_LEVEL = 0.003;

type Camera = { yaw: number; pitch: number; distance?: number };
type ProjectedPoint = { x: number; y: number; depth: number; scale: number };
type Voice = {
  key: string;
  stem: string;
  base: string;
  kind: "object" | "bed";
  position: Vec3;
  lobes?: { channel: string; position: Vec3; weight: number }[];
};
type SpeakerHitTarget = {
  channel: string;
  x: number;
  y: number;
  radius: number;
};

export function bedLobeIntensity(
  level: number,
  weight: number,
  muted: boolean,
) {
  return muted ? 0 : Math.min(1, level * 8) * (0.45 + weight * 0.55);
}

export function smoothSceneLevel(
  previous: number,
  target: number,
  delta: number,
) {
  const response = target > previous ? COLOR_RESPONSE : RELEASE_RESPONSE;
  return previous + (target - previous) * Math.min(1, delta * response);
}

export function sceneActivityOpacity(activity: number) {
  return Math.min(1, Math.max(0, activity));
}

export function isSceneObjectAudible(level: number) {
  return level > SILENCE_LEVEL;
}

export function zoomSceneCamera(camera: Camera, delta: number): Camera {
  return {
    ...camera,
    distance: Math.min(
      MAX_DISTANCE,
      Math.max(
        MIN_DISTANCE,
        (camera.distance ?? DEFAULT_CAMERA.distance) * Math.exp(delta * 0.001),
      ),
    ),
  };
}

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

export function projectScenePoint(
  point: Vec3,
  width: number,
  height: number,
  camera: Camera,
): ProjectedPoint {
  const target = { x: 0, y: 0.5, z: 0 };
  const distance = camera.distance ?? DEFAULT_CAMERA.distance;
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
  return (
    !isBedStem(stem) &&
    (objectStems.has(stem) || objectStems.has(stem.split("@", 1)[0]))
  );
}

export function sceneSpeakerPosition(channel: string): Vec3 | undefined {
  return channel === "LFE" ? { x: 0, y: 0, z: 0 } : speakerCoordinates[channel];
}

export type SceneViewProps = {
  channels: string[];
  routing: StemRouting;
  objectStems: ReadonlySet<string>;
  selectedStem: string | null;
  colors: Record<string, string>;
  channelCounts?: Record<string, number>;
  stemSpectrum: React.MutableRefObject<Map<string, StemSpectrum>>;
  channelLevels: React.MutableRefObject<Map<string, MeterLevel>>;
  speakerEnabled: Record<string, boolean>;
  speakerSolo: ReadonlySet<string>;
  onToggleSpeaker: (channel: string) => void;
  onSoloSpeaker: (channel: string) => void;
  active: boolean;
  intensity: number;
  onIntensity: (next: number) => void;
  className?: string;
};

function SceneViewImpl({
  channels,
  routing,
  objectStems,
  selectedStem,
  colors,
  channelCounts,
  stemSpectrum,
  channelLevels,
  speakerEnabled,
  speakerSolo,
  onToggleSpeaker,
  onSoloSpeaker,
  active,
  intensity,
  onIntensity,
  className,
}: SceneViewProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const blobCanvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const blurCanvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const camera = React.useRef<Camera>({ ...DEFAULT_CAMERA });
  const levels = React.useRef(new Map<string, number>());
  const speakerHits = React.useRef<SpeakerHitTarget[]>([]);
  const wakeRef = React.useRef<() => void>(() => {});
  const drag = React.useRef<{ x: number; y: number; moved: boolean } | null>(
    null,
  );
  const propsRef = React.useRef({
    channels,
    routing,
    objectStems,
    selectedStem,
    colors,
    channelCounts,
    speakerEnabled,
    speakerSolo,
    intensity,
  });
  propsRef.current = {
    channels,
    routing,
    objectStems,
    selectedStem,
    colors,
    channelCounts,
    speakerEnabled,
    speakerSolo,
    intensity,
  };
  const activeRef = React.useRef(active);
  activeRef.current = active;

  React.useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (!blobCanvasRef.current)
      blobCanvasRef.current = document.createElement("canvas");
    const blobCanvas = blobCanvasRef.current;
    const blobCtx = blobCanvas.getContext("2d");
    if (!blobCtx) return;
    if (!blurCanvasRef.current)
      blurCanvasRef.current = document.createElement("canvas");
    const blurCanvas = blurCanvasRef.current;
    const blurCtx = blurCanvas.getContext("2d");
    if (!blurCtx) return;
    let lastBlurTime = -Infinity;

    let lastTime = performance.now();
    const drawLine = (from: Vec3, to: Vec3, width: number, height: number) => {
      const a = projectScenePoint(from, width, height, camera.current);
      const b = projectScenePoint(to, width, height, camera.current);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    };
    const draw = (time: number, { width, height }: { width: number; height: number }) => {
      const delta = Math.min(0.1, (time - lastTime) / 1000);
      lastTime = time;
      const {
        channels: currentChannels,
        routing: currentRouting,
        objectStems: currentObjectStems,
        selectedStem: currentSelected,
        colors: currentColors,
        channelCounts: currentCounts,
        speakerEnabled: currentSpeakerEnabled,
        speakerSolo: currentSolo,
        intensity: currentIntensity,
      } = propsRef.current;

      const field = ctx.createLinearGradient(0, 0, 0, height);
      field.addColorStop(0, canvasTheme.plotFieldCore);
      field.addColorStop(1, canvasTheme.plotField);
      ctx.fillStyle = field;
      ctx.fillRect(0, 0, width, height);

      const corners: Vec3[] = [
        { x: -1, y: 0, z: -1 },
        { x: 1, y: 0, z: -1 },
        { x: 1, y: 0, z: 1 },
        { x: -1, y: 0, z: 1 },
        { x: -1, y: 1, z: -1 },
        { x: 1, y: 1, z: -1 },
        { x: 1, y: 1, z: 1 },
        { x: -1, y: 1, z: 1 },
      ];
      const floor = corners
        .slice(0, 4)
        .map((point) =>
          projectScenePoint(point, width, height, camera.current),
        );
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
        drawLine(
          { x: step, y: 0, z: -1 },
          { x: step, y: 0, z: 1 },
          width,
          height,
        );
        drawLine(
          { x: -1, y: 0, z: step },
          { x: 1, y: 0, z: step },
          width,
          height,
        );
      }
      ctx.strokeStyle = canvasTheme.grid;
      const edges = [
        [0, 1],
        [1, 2],
        [2, 3],
        [3, 0],
        [4, 5],
        [5, 6],
        [6, 7],
        [7, 4],
        [0, 4],
        [1, 5],
        [2, 6],
        [3, 7],
      ];
      for (const [from, to] of edges)
        drawLine(corners[from], corners[to], width, height);

      const voices: Voice[] = [];
      for (const stem of Object.keys(currentRouting)) {
        const route = currentRouting[stem] || {};
        const base = stem.split("@", 1)[0];
        if (
          isObjectStem(stem, currentObjectStems) &&
          (currentCounts?.[stem] ?? 2) >= 2
        ) {
          const { left, right } = stemPositionStereo(route);
          voices.push({
            key: `${stem}:L`,
            stem,
            base,
            kind: "object",
            position: scenePosition(left),
          });
          voices.push({
            key: `${stem}:R`,
            stem,
            base,
            kind: "object",
            position: scenePosition(right),
          });
        } else if (isObjectStem(stem, currentObjectStems)) {
          voices.push({
            key: stem,
            stem,
            base,
            kind: "object",
            position: scenePosition(stemPosition(route)),
          });
        } else {
          const lobes = Object.entries(route).flatMap(([channel, weight]) => {
            const position = speakerCoordinates[channel];
            return position && weight > 0
              ? [{ channel, position: scenePosition(position), weight }]
              : [];
          });
          voices.push({
            key: stem,
            stem,
            base,
            kind: "bed",
            position: scenePosition(stemPosition(route)),
            lobes,
          });
        }
      }

      const resolved = voices
        .map((voice) => {
          const target = stemSpectrum.current.get(voice.base)?.level ?? 0;
          const previous = levels.current.get(voice.key) ?? 0;
          const level = smoothSceneLevel(previous, target, delta);
          levels.current.set(voice.key, level);
          return {
            voice,
            level,
            point: projectScenePoint(
              voice.position,
              width,
              height,
              camera.current,
            ),
          };
        })
        .sort((a, b) => b.point.depth - a.point.depth);

      const alphaScale = lerp(MIN_ALPHA_SCALE, 1, currentIntensity);
      blobCtx.clearRect(0, 0, width, height);
      blobCtx.globalCompositeOperation = "lighter";
      for (const { voice, level } of resolved) {
        if (voice.kind !== "bed" || !voice.lobes?.length) continue;
        const emphasis =
          currentSelected && currentSelected !== voice.stem ? 0.45 : 1;
        const strongest = Math.max(...voice.lobes.map((lobe) => lobe.weight));
        const [r, g, b] = hexToRgb(
          currentColors[voice.stem] || canvasTheme.stemFallback,
        );
        for (const lobe of voice.lobes) {
          const lobePoint = projectScenePoint(
            lobe.position,
            width,
            height,
            camera.current,
          );
          const weight = lobe.weight / strongest;
          const activity = bedLobeIntensity(
            level,
            weight,
            currentSpeakerEnabled[lobe.channel] === false,
          );
          if (activity <= 0.005) continue;
          const radius =
            Math.max(30, Math.min(90, lobePoint.scale * 0.62)) *
            (0.65 + weight * 0.35);
          const haze = blobCtx.createRadialGradient(
            lobePoint.x,
            lobePoint.y,
            0,
            lobePoint.x,
            lobePoint.y,
            radius,
          );
          const opacity = sceneActivityOpacity(activity);
          haze.addColorStop(
            0,
            `rgba(${r}, ${g}, ${b}, ${opacity * 0.7 * emphasis * alphaScale})`,
          );
          haze.addColorStop(
            0.42,
            `rgba(${r}, ${g}, ${b}, ${opacity * 0.3 * emphasis * alphaScale})`,
          );
          haze.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
          blobCtx.fillStyle = haze;
          blobCtx.beginPath();
          blobCtx.arc(lobePoint.x, lobePoint.y, radius, 0, Math.PI * 2);
          blobCtx.fill();
        }
      }
      blobCtx.globalCompositeOperation = "source-over";
      if (time - lastBlurTime >= 1000 / 30) {
        blurCtx.clearRect(0, 0, width, height);
        blurCtx.save();
        blurCtx.filter = `blur(${Math.max(14, Math.min(42, Math.min(width, height) * 0.1))}px)`;
        blurCtx.drawImage(blobCanvas, 0, 0, width, height);
        blurCtx.restore();
        lastBlurTime = time;
      }
      ctx.save();
      ctx.globalCompositeOperation = "screen";
      ctx.drawImage(blurCanvas, 0, 0, width, height);
      ctx.restore();

      const nextSpeakerHits: SpeakerHitTarget[] = [];
      ctx.font = "500 10px system-ui, sans-serif";
      ctx.textAlign = "center";
      for (const channel of currentChannels) {
        const position = sceneSpeakerPosition(channel);
        if (!position) continue;
        const speakerPoint = projectScenePoint(
          scenePosition(position),
          width,
          height,
          camera.current,
        );
        const muted = currentSpeakerEnabled[channel] === false;
        const soloed = currentSolo.has(channel);
        const silent = !muted && currentSolo.size > 0 && !soloed;
        const target = silent
          ? 0
          : Math.min(1, (channelLevels.current.get(channel)?.rms ?? 0) * 8);
        const activity = smoothSceneLevel(
          levels.current.get(`speaker:${channel}`) ?? 0,
          target,
          delta,
        );
        levels.current.set(`speaker:${channel}`, activity);
        if (activity > 0.005) {
          const glow = ctx.createRadialGradient(
            speakerPoint.x,
            speakerPoint.y,
            0,
            speakerPoint.x,
            speakerPoint.y,
            16 + activity * 22,
          );
          const [r, g, b] = hexToRgb(canvasTheme.headphone);
          glow.addColorStop(
            0,
            `rgba(${r}, ${g}, ${b}, ${(0.18 + activity * 0.3) * alphaScale})`,
          );
          glow.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
          ctx.fillStyle = glow;
          ctx.beginPath();
          ctx.arc(
            speakerPoint.x,
            speakerPoint.y,
            16 + activity * 22,
            0,
            Math.PI * 2,
          );
          ctx.fill();
        }
        drawSpeakerPoint(
          ctx,
          speakerPoint.x,
          speakerPoint.y,
          4,
          muted,
          soloed,
          silent,
        );
        ctx.fillStyle = muted
          ? canvasTheme.muteLabel
          : soloed
            ? canvasTheme.meterWarn
            : silent
              ? canvasTheme.label
              : canvasTheme.labelStrong;
        ctx.fillText(
          speakerDisplayLabel(channel, currentChannels),
          speakerPoint.x,
          speakerPoint.y - 10,
        );
        nextSpeakerHits.push({
          channel,
          x: speakerPoint.x,
          y: speakerPoint.y,
          radius: 12,
        });
      }
      speakerHits.current = nextSpeakerHits;

      for (const { voice, level, point } of resolved) {
        if (voice.kind !== "object" || !isSceneObjectAudible(level)) continue;
        const activity = Math.min(1, level * 8);
        const opacity = sceneActivityOpacity(activity);
        const emphasis =
          currentSelected && currentSelected !== voice.stem ? 0.35 : 1;
        const radius = Math.max(4, Math.min(11, point.scale * 0.08));
        const glowRadius = radius * (2 + activity * 2);
        const [r, g, b] = hexToRgb(
          currentColors[voice.stem] || canvasTheme.stemFallback,
        );
        const glow = ctx.createRadialGradient(
          point.x,
          point.y,
          0,
          point.x,
          point.y,
          glowRadius,
        );
        glow.addColorStop(
          0,
          `rgba(${r}, ${g}, ${b}, ${opacity * 0.7 * emphasis * alphaScale})`,
        );
        glow.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(point.x, point.y, glowRadius, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${opacity * emphasis * alphaScale})`;
        ctx.beginPath();
        ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
        ctx.fill();
      }

      return resolved.every(({ level }) => !isSceneObjectAudible(level));
    };
    const lifecycle = startSpatialCanvas({
      canvas,
      container,
      active: activeRef,
      draw,
      settleFrames: SETTLE_FRAMES,
      resize: ({ dpr }) => {
        blobCanvas.width = canvas.width;
        blobCanvas.height = canvas.height;
        blurCanvas.width = canvas.width;
        blurCanvas.height = canvas.height;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        blobCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
        blurCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
        lastBlurTime = -Infinity;
      },
    });
    wakeRef.current = lifecycle.wake;
    return lifecycle.dispose;
  }, [stemSpectrum, channelLevels]);

  React.useEffect(() => {
    wakeRef.current();
  }, [
    active,
    channels,
    routing,
    objectStems,
    selectedStem,
    colors,
    channelCounts,
    speakerEnabled,
    speakerSolo,
    intensity,
  ]);

  const toggleSpeakerAt = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let closestSpeaker: { channel: string; distance: number } | null = null;
    for (const hit of speakerHits.current) {
      const distance = Math.hypot(hit.x - x, hit.y - y);
      if (
        distance <= hit.radius &&
        (!closestSpeaker || distance < closestSpeaker.distance)
      )
        closestSpeaker = { channel: hit.channel, distance };
    }
    if (closestSpeaker) {
      if (event.altKey) onSoloSpeaker(closestSpeaker.channel);
      else onToggleSpeaker(closestSpeaker.channel);
    }
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
    camera.current.pitch = Math.min(
      MAX_PITCH,
      Math.max(MIN_PITCH, camera.current.pitch + dy * 0.01),
    );
    current.x = event.clientX;
    current.y = event.clientY;
    wakeRef.current();
  };
  const handlePointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const current = drag.current;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    if (current && !current.moved) toggleSpeakerAt(event);
  };
  const handleWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    camera.current = zoomSceneCamera(camera.current, event.deltaY);
    wakeRef.current();
  };
  const handleKeyDown = (event: React.KeyboardEvent<HTMLCanvasElement>) => {
    const turn = (5 * Math.PI) / 180;
    if (event.key === "Home") camera.current = { ...DEFAULT_CAMERA };
    else if (event.key === "ArrowLeft") camera.current.yaw -= turn;
    else if (event.key === "ArrowRight") camera.current.yaw += turn;
    else if (event.key === "ArrowUp")
      camera.current.pitch = Math.max(MIN_PITCH, camera.current.pitch - turn);
    else if (event.key === "ArrowDown")
      camera.current.pitch = Math.min(MAX_PITCH, camera.current.pitch + turn);
    else if (event.key === "+" || event.key === "=")
      camera.current = zoomSceneCamera(camera.current, -180);
    else if (event.key === "-" || event.key === "_")
      camera.current = zoomSceneCamera(camera.current, 180);
    else return;
    event.preventDefault();
    wakeRef.current();
  };

  return (
    <div
      className={cn(
        "relative flex flex-col overflow-hidden rounded-lg border",
        className,
      )}
      style={{ backgroundColor: canvasTheme.plotField }}
    >
      <div ref={containerRef} className="min-h-0 flex-1">
        <canvas
          ref={canvasRef}
          tabIndex={0}
          aria-label="3D object scene. Bed clouds show routed speaker channels; click a speaker to mute it, or Option/Alt-click to solo it. Drag to orbit, scroll or use plus and minus to zoom, use arrow keys to rotate, and Home to reset the view."
          className="h-full w-full touch-none cursor-grab outline-none active:cursor-grabbing focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={() => {
            drag.current = null;
          }}
          onWheel={handleWheel}
          onKeyDown={handleKeyDown}
        />
      </div>
      <IntensitySlider
        value={intensity}
        onChange={onIntensity}
        label="Scene intensity"
        className="absolute left-2 top-2 z-10"
      />
    </div>
  );
}

export default React.memo(SceneViewImpl);
