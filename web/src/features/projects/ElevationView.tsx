import * as React from "react";
import type { StemRouting } from "@/api";
import { speakerCoordinates, speakerLabels, stemPosition, stemPositionStereo } from "@/lib/spatial";

// Secondary "elevation" view: a front-on cross-section showing the vertical
// (height) axis that the Haze view's top-down radar collapses away. X = the
// speaker's real left/right position, Y = its real floor/height position —
// unlike the radar, this uses actual routed coordinates for placement, not
// spectral centroid, matching NUGEN Halo Upmix's height panel.

type Voice = { key: string; stem: string; base: string; x: number; y: number; sizeScale: number };
type SmoothedVoice = { x: number; y: number; level: number };

const MAX_HEIGHT = 0.6;

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  const value = clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean;
  const num = parseInt(value, 16);
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

export type ElevationViewProps = {
  channels: string[];
  routing: StemRouting;
  selectedStem: string | null;
  colors: Record<string, string>;
  channelCounts?: Record<string, number>;
  stemSpectrum: React.MutableRefObject<Map<string, { level: number; centroid: number }>>;
  className?: string;
};

export default function ElevationView({
  channels,
  routing,
  selectedStem,
  colors,
  channelCounts,
  stemSpectrum,
  className,
}: ElevationViewProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const smoothed = React.useRef<Map<string, SmoothedVoice>>(new Map());
  const frame = React.useRef<number | null>(null);
  const initializedSize = React.useRef(false);
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
      const padX = 34;
      const padTop = 20;
      const padBottom = 22;
      const plotWidth = Math.max(1, width - padX * 2);
      const plotHeight = Math.max(1, height - padTop - padBottom);
      const floorY = height - padBottom;
      const toX = (x: number) => padX + ((x + 1) / 2) * plotWidth;
      const toY = (y: number) => floorY - Math.min(1, y / MAX_HEIGHT) * plotHeight;

      if (!initializedSize.current) {
        ctx.fillStyle = "#020617";
        ctx.fillRect(0, 0, width, height);
        initializedSize.current = true;
      } else {
        ctx.fillStyle = "rgba(2, 6, 23, 0.3)";
        ctx.fillRect(0, 0, width, height);
      }

      // Floor / mid / top guide lines and center pan gridline.
      ctx.strokeStyle = "#1e293b";
      ctx.lineWidth = 1;
      for (const fraction of [0, 0.5, 1]) {
        const y = floorY - fraction * plotHeight;
        ctx.beginPath();
        ctx.moveTo(padX, y);
        ctx.lineTo(width - padX, y);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.moveTo(toX(0), padTop);
      ctx.lineTo(toX(0), floorY);
      ctx.stroke();

      ctx.font = "600 9px system-ui, sans-serif";
      ctx.fillStyle = "#475569";
      ctx.textAlign = "left";
      ctx.fillText("TOP", 4, padTop + 8);
      ctx.fillText("FLOOR", 4, floorY + 3);

      // Speaker labels: floor channels along the bottom edge, height
      // channels along the top edge, both positioned by real left/right x.
      const floorChannels = currentChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel] && speakerCoordinates[channel].y === 0);
      const topChannels = currentChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel] && speakerCoordinates[channel].y > 0);
      ctx.font = "600 10px system-ui, sans-serif";
      ctx.textAlign = "center";
      for (const channel of floorChannels) {
        const x = toX(speakerCoordinates[channel].x);
        ctx.fillStyle = "#cbd5e1";
        ctx.fillText(speakerLabels[channel] || channel, x, floorY + 15);
        ctx.fillStyle = "#334155";
        ctx.beginPath();
        ctx.arc(x, floorY, 2.5, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.font = "600 9px system-ui, sans-serif";
      for (const channel of topChannels) {
        const x = toX(speakerCoordinates[channel].x);
        ctx.fillStyle = "#94a3b8";
        ctx.fillText(speakerLabels[channel] || channel, x, padTop - 8);
        ctx.fillStyle = "#475569";
        ctx.beginPath();
        ctx.arc(x, padTop, 2.5, 0, Math.PI * 2);
        ctx.fill();
      }

      const stems = Object.keys(currentRouting);
      const voices: Voice[] = [];
      for (const stem of stems) {
        const route = currentRouting[stem] || {};
        const base = stem.split("@", 1)[0];
        const stereo = (currentCounts?.[stem] ?? 2) >= 2;
        if (stereo) {
          const { left, right } = stemPositionStereo(route);
          voices.push({ key: `${stem}:L`, stem, base, x: left.x, y: left.y, sizeScale: 0.8 });
          voices.push({ key: `${stem}:R`, stem, base, x: right.x, y: right.y, sizeScale: 0.8 });
        } else {
          const pos = stemPosition(route);
          voices.push({ key: stem, stem, base, x: pos.x, y: pos.y, sizeScale: 1 });
        }
      }

      ctx.globalCompositeOperation = "screen";
      for (const voice of voices) {
        const spectrum = stemSpectrum.current.get(voice.base);
        const level = spectrum?.level ?? 0;

        const previous = smoothed.current.get(voice.key);
        const next: SmoothedVoice = previous
          ? {
            x: previous.x + (voice.x - previous.x) * Math.min(1, delta * 6),
            y: previous.y + (voice.y - previous.y) * Math.min(1, delta * 6),
            level: previous.level + (level - previous.level) * Math.min(1, delta * 8),
          }
          : { x: voice.x, y: voice.y, level };
        smoothed.current.set(voice.key, next);

        // Silent voices (muted, or another stem is soloed) fade all the way
        // out instead of leaving a baseline haze cloud behind.
        const audible = Math.min(1, next.level * 8);
        if (audible <= 0.005) continue;

        const color = currentColors[voice.stem] || "#60a5fa";
        const [r, g, b] = hexToRgb(color);
        const dimmed = Boolean(currentSelected) && currentSelected !== voice.stem;
        const emphasis = (currentSelected === voice.stem ? 1 : dimmed ? 0.35 : 0.8) * audible;
        const point = { x: toX(next.x), y: toY(next.y) };
        const blobRadius = (20 + next.level * 40) * voice.sizeScale * (currentSelected === voice.stem ? 1.15 : 1);

        const gradient = ctx.createRadialGradient(point.x, point.y, 0, point.x, point.y, blobRadius);
        gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(0.32 + next.level * 0.25) * emphasis})`);
        gradient.addColorStop(0.4, `rgba(${r}, ${g}, ${b}, ${0.12 * emphasis})`);
        gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(point.x, point.y, blobRadius, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalCompositeOperation = "source-over";

      frame.current = window.requestAnimationFrame(draw);
    };
    frame.current = window.requestAnimationFrame(draw);

    return () => {
      observer.disconnect();
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    };
  }, [stemSpectrum]);

  return <div className={`relative flex flex-col overflow-hidden rounded-lg border bg-slate-950 text-slate-100 ${className || ""}`}>
    <div className="pointer-events-none relative z-10 px-3 pt-2 text-xs text-slate-300">Elevation view</div>
    <div ref={containerRef} className="min-h-0 flex-1">
      <canvas ref={canvasRef} className="h-full w-full" />
    </div>
  </div>;
}
