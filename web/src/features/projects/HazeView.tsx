import * as React from "react";
import type { StemRouting } from "@/api";
import { heightFraction, speakerCoordinates, speakerLabels, stemPosition, stemPositionStereo, vecAngle, type Vec3 } from "@/lib/spatial";

// NUGEN Halo Upmix-style "Haze View": a 2D radar where radius encodes
// spectral centroid (bass at the center, treble at the edge) and angle
// encodes speaker direction (compass-style, front = up). A separate dashed
// outer ring shows height-channel content per stem, since this projection
// is otherwise a flat floor-plan and would lose the y axis entirely.

type Voice = { key: string; stem: string; base: string; angle: number; heightAngle: number | null; sizeScale: number };

type SmoothedVoice = { angle: number; radius: number; heightRadius: number; level: number; heightLevel: number };

type HitTarget = { stem: string; x: number; y: number; radius: number };

const TAU = Math.PI * 2;

function lerpAngle(from: number, to: number, t: number) {
  let delta = (to - from) % TAU;
  if (delta > Math.PI) delta -= TAU;
  if (delta < -Math.PI) delta += TAU;
  return from + delta * t;
}

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  const value = clean.length === 3
    ? clean.split("").map((c) => c + c).join("")
    : clean;
  const num = parseInt(value, 16);
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

function polar(center: { x: number; y: number }, radius: number, angle: number) {
  return { x: center.x + Math.sin(angle) * radius, y: center.y - Math.cos(angle) * radius };
}

export type HazeViewProps = {
  channels: string[];
  routing: StemRouting;
  selectedStem: string | null;
  colors: Record<string, string>;
  channelCounts?: Record<string, number>;
  onSelectStem: (stem: string | null) => void;
  stemSpectrum: React.MutableRefObject<Map<string, { level: number; centroid: number }>>;
  className?: string;
};

export default function HazeView({
  channels,
  routing,
  selectedStem,
  colors,
  channelCounts,
  onSelectStem,
  stemSpectrum,
  className,
}: HazeViewProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const smoothed = React.useRef<Map<string, SmoothedVoice>>(new Map());
  const hitTargets = React.useRef<HitTarget[]>([]);
  const frame = React.useRef<number | null>(null);
  const initializedSize = React.useRef(false);
  // Latest props, read fresh by the draw loop without restarting it.
  const propsRef = React.useRef({ channels, routing, selectedStem, colors, channelCounts });
  propsRef.current = { channels, routing, selectedStem, colors, channelCounts };

  React.useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const width = container.clientWidth;
      const height = container.clientHeight;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      initializedSize.current = false;
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);

    let lastTime = performance.now();
    const draw = (time: number) => {
      const delta = Math.min(0.1, (time - lastTime) / 1000);
      lastTime = time;
      const { channels: currentChannels, routing: currentRouting, selectedStem: currentSelected, colors: currentColors, channelCounts: currentCounts } = propsRef.current;
      const width = canvas.width / (window.devicePixelRatio || 1);
      const height = canvas.height / (window.devicePixelRatio || 1);
      const center = { x: width / 2, y: height / 2 };
      const radius = Math.min(width, height) / 2 * 0.62;
      const heightRingRadius = radius * 1.18;

      if (!initializedSize.current) {
        ctx.fillStyle = "#020617";
        ctx.fillRect(0, 0, width, height);
        initializedSize.current = true;
      } else {
        ctx.fillStyle = "rgba(2, 6, 23, 0.3)";
        ctx.fillRect(0, 0, width, height);
      }

      // Radar guide rings (frequency axis) + crosshair.
      ctx.strokeStyle = "#1e293b";
      ctx.lineWidth = 1;
      for (const fraction of [0.33, 0.66, 1]) {
        ctx.beginPath();
        ctx.arc(center.x, center.y, radius * fraction, 0, TAU);
        ctx.stroke();
      }
      ctx.save();
      ctx.setLineDash([2, 4]);
      ctx.strokeStyle = "#334155";
      ctx.beginPath();
      ctx.arc(center.x, center.y, heightRingRadius, 0, TAU);
      ctx.stroke();
      ctx.restore();

      ctx.textAlign = "center";
      ctx.font = "600 10px system-ui, sans-serif";
      ctx.fillStyle = "#94a3b8";
      ctx.fillText("FRONT", center.x, center.y - heightRingRadius - 18);
      ctx.fillText("BACK", center.x, center.y + heightRingRadius + 22);

      // Speaker labels: floor channels on the main ring, height channels on
      // the dashed outer ring so the two dimensions don't overlap visually.
      const floorChannels = currentChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel] && speakerCoordinates[channel].y === 0);
      const topChannels = currentChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel] && speakerCoordinates[channel].y > 0);
      ctx.font = "600 11px system-ui, sans-serif";
      for (const channel of floorChannels) {
        const angle = vecAngle(speakerCoordinates[channel]);
        const point = polar(center, radius, angle);
        ctx.fillStyle = "#334155";
        ctx.beginPath();
        ctx.arc(point.x, point.y, 3, 0, TAU);
        ctx.fill();
        const labelPoint = polar(center, radius + 14, angle);
        ctx.fillStyle = "#cbd5e1";
        ctx.textAlign = "center";
        ctx.fillText(speakerLabels[channel] || channel, labelPoint.x, labelPoint.y + 4);
      }
      ctx.font = "600 9px system-ui, sans-serif";
      for (const channel of topChannels) {
        const angle = vecAngle(speakerCoordinates[channel]);
        const point = polar(center, heightRingRadius, angle);
        ctx.fillStyle = "#475569";
        ctx.beginPath();
        ctx.arc(point.x, point.y, 2.5, 0, TAU);
        ctx.fill();
        const labelPoint = polar(center, heightRingRadius + 12, angle);
        ctx.fillStyle = "#94a3b8";
        ctx.fillText(speakerLabels[channel] || channel, labelPoint.x, labelPoint.y + 3);
      }

      // Listener marker.
      ctx.fillStyle = "#e2e8f0";
      ctx.beginPath();
      ctx.arc(center.x, center.y, 3, 0, TAU);
      ctx.fill();

      // Build this frame's voices (mono, or L/R pair for stereo stems).
      const stems = Object.keys(currentRouting);
      const voices: Voice[] = [];
      for (const stem of stems) {
        const route = currentRouting[stem] || {};
        const base = stem.split("@", 1)[0];
        const stereo = (currentCounts?.[stem] ?? 2) >= 2;
        const heightAngleValue = (() => {
          if (heightFraction(route) <= 0) return null;
          return vecAngle(stemPosition(route));
        })();
        if (stereo) {
          const { left, right } = stemPositionStereo(route);
          // One height blob per stem, not per L/R voice — both would sit at
          // the same angle and just double-draw on top of each other.
          voices.push({ key: `${stem}:L`, stem, base, angle: vecAngle(left), heightAngle: heightAngleValue, sizeScale: 0.8 });
          voices.push({ key: `${stem}:R`, stem, base, angle: vecAngle(right), heightAngle: null, sizeScale: 0.8 });
        } else {
          voices.push({ key: stem, stem, base, angle: vecAngle(stemPosition(route)), heightAngle: heightAngleValue, sizeScale: 1 });
        }
      }

      // Draw haze blobs with "screen" blending so overlapping stems glow
      // together without blowing straight to white the way additive
      // ("lighter") blending does once a few blobs stack near the center.
      ctx.globalCompositeOperation = "screen";
      const nextHits: HitTarget[] = [];
      for (const voice of voices) {
        const spectrum = stemSpectrum.current.get(voice.base);
        const level = spectrum?.level ?? 0;
        const targetRadius = (spectrum ? spectrum.centroid : 0.42) * radius;
        const targetHeightLevel = voice.heightAngle !== null ? level : 0;

        const previous = smoothed.current.get(voice.key);
        const next: SmoothedVoice = previous
          ? {
            angle: lerpAngle(previous.angle, voice.angle, Math.min(1, delta * 6)),
            radius: previous.radius + (targetRadius - previous.radius) * Math.min(1, delta * 6),
            heightRadius: voice.heightAngle !== null
              ? lerpAngle(previous.heightRadius, voice.heightAngle, Math.min(1, delta * 6))
              : previous.heightRadius,
            level: previous.level + (level - previous.level) * Math.min(1, delta * 8),
            heightLevel: previous.heightLevel + (targetHeightLevel - previous.heightLevel) * Math.min(1, delta * 8),
          }
          : { angle: voice.angle, radius: targetRadius, heightRadius: voice.heightAngle ?? 0, level, heightLevel: targetHeightLevel };
        smoothed.current.set(voice.key, next);

        // Silent voices (muted, or another stem is soloed) fade all the way
        // out instead of leaving a baseline haze cloud behind — the level
        // already reflects mute/solo (see useStemPreview.ts's appliedGain).
        const audible = Math.min(1, next.level * 8);

        const color = propsRef.current.colors[voice.stem] || "#60a5fa";
        const [r, g, b] = hexToRgb(color);
        const dimmed = Boolean(currentSelected) && currentSelected !== voice.stem;
        const emphasis = (currentSelected === voice.stem ? 1 : dimmed ? 0.35 : 0.8) * audible;
        const point = polar(center, next.radius, next.angle);
        const blobRadius = (radius * 0.32 + next.level * radius * 0.28) * voice.sizeScale * (currentSelected === voice.stem ? 1.1 : 1);

        if (emphasis > 0.005) {
          const gradient = ctx.createRadialGradient(point.x, point.y, 0, point.x, point.y, blobRadius);
          gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(0.32 + next.level * 0.25) * emphasis})`);
          gradient.addColorStop(0.4, `rgba(${r}, ${g}, ${b}, ${0.12 * emphasis})`);
          gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
          ctx.fillStyle = gradient;
          ctx.beginPath();
          ctx.arc(point.x, point.y, blobRadius, 0, TAU);
          ctx.fill();
        }

        // Height indicator on the dashed outer ring, brightness = level.
        if (voice.heightAngle !== null && emphasis > 0.005) {
          const heightPoint = polar(center, heightRingRadius, next.heightRadius);
          const heightBlob = 6 + next.heightLevel * 16;
          const heightGradient = ctx.createRadialGradient(heightPoint.x, heightPoint.y, 0, heightPoint.x, heightPoint.y, heightBlob);
          heightGradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(0.5 + next.heightLevel * 0.5) * emphasis})`);
          heightGradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
          ctx.fillStyle = heightGradient;
          ctx.beginPath();
          ctx.arc(heightPoint.x, heightPoint.y, heightBlob, 0, TAU);
          ctx.fill();
        }

        nextHits.push({ stem: voice.stem, x: point.x, y: point.y, radius: Math.max(blobRadius, 16) });
      }
      ctx.globalCompositeOperation = "source-over";
      hitTargets.current = nextHits;

      frame.current = window.requestAnimationFrame(draw);
    };
    frame.current = window.requestAnimationFrame(draw);

    return () => {
      observer.disconnect();
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    };
  }, [stemSpectrum]);

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let closest: { stem: string; distance: number } | null = null;
    for (const hit of hitTargets.current) {
      const distance = Math.hypot(hit.x - x, hit.y - y);
      if (distance <= hit.radius && (!closest || distance < closest.distance)) closest = { stem: hit.stem, distance };
    }
    onSelectStem(closest ? (closest.stem === selectedStem ? null : closest.stem) : null);
  };

  const lfeRoute = selectedStem ? routing[selectedStem]?.LFE || 0 : 0;

  return <div className={`relative flex flex-col overflow-hidden rounded-lg border bg-slate-950 text-slate-100 ${className || ""}`}>
    <div className="pointer-events-none relative z-10 flex items-center justify-between px-3 pt-3 text-xs text-slate-300">
      <span>Haze view</span>
      <button className="pointer-events-auto hover:text-white" onClick={() => onSelectStem(null)}>{selectedStem || "Aggregate output"}</button>
    </div>
    <div ref={containerRef} className="min-h-0 flex-1">
      <canvas ref={canvasRef} className="h-full w-full cursor-pointer" onPointerDown={handlePointerDown} />
    </div>
    {channels.includes("LFE") && <div className="pointer-events-none absolute bottom-2 left-2 z-10 rounded bg-slate-800/90 px-2 py-1 text-xs">LFE {selectedStem ? `${lfeRoute.toFixed(2)} send` : "bus"}</div>}
  </div>;
}
