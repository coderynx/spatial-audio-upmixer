import * as React from "react";
import type { StemRouting } from "@/api";
import {
  MIN_ALPHA_SCALE,
  SETTLE_FRAMES,
  canvasTheme,
  hexToRgb,
  lerp,
} from "@/lib/canvasTheme";
import { stemPan } from "@/lib/spatial";
import { IntensitySlider } from "./IntensitySlider";
import { drawSpeakerPoint } from "./speakerMarker";

// Stereo counterpart to the Haze and Elevation views, which both replace
// themselves with this one on a two-channel layout: with no depth, height or
// LFE axis left, the only placement a stem still has is its pan. X = that
// pan, Y = spectral centroid (the quantity Haze maps to radius), so a kick
// and a hi-hat sharing a pan position still read apart.

type Voice = {
  key: string;
  stem: string;
  base: string;
  pan: number;
  sizeScale: number;
};
type SmoothedVoice = { x: number; y: number; level: number };
type SpeakerHitTarget = {
  channel: string;
  x: number;
  y: number;
  radius: number;
};

const TAU = Math.PI * 2;

// Half-width of a stereo stem's two voices, widest at centre and closing to
// zero at either hard pan — a hard-panned stereo stem has no image left to
// spread.
function stereoSpread(pan: number): number {
  return 0.175 * (1 - Math.abs(2 * pan - 1));
}

export type StereoPanoramaViewProps = {
  channels: string[];
  routing: StemRouting;
  selectedStem: string | null;
  colors: Record<string, string>;
  channelCounts?: Record<string, number>;
  stemSpectrum: React.MutableRefObject<
    Map<string, { level: number; centroid: number }>
  >;
  // Per-speaker mute — same channel-bed model as HazeView (see
  // useStemPreview.ts). Clicking a speaker's point on the graph toggles it.
  speakerEnabled: Record<string, boolean>;
  speakerSolo: ReadonlySet<string>;
  onToggleSpeaker: (channel: string) => void;
  onSoloSpeaker: (channel: string) => void;
  // True while preview audio is live-updating `stemSpectrum` — see HazeView's
  // `active` prop for the idle-gating rationale, identical here.
  active: boolean;
  intensity: number;
  onIntensity: (next: number) => void;
  className?: string;
};

function StereoPanoramaViewImpl({
  channels,
  routing,
  selectedStem,
  colors,
  channelCounts,
  stemSpectrum,
  speakerEnabled,
  speakerSolo,
  onToggleSpeaker,
  onSoloSpeaker,
  active,
  intensity,
  onIntensity,
  className,
}: StereoPanoramaViewProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const blobCanvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const blurCanvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const smoothed = React.useRef<Map<string, SmoothedVoice>>(new Map());
  const speakerHitTargets = React.useRef<SpeakerHitTarget[]>([]);
  const frame = React.useRef<number | null>(null);
  const initializedSize = React.useRef(false);
  const propsRef = React.useRef({
    channels,
    routing,
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
    selectedStem,
    colors,
    channelCounts,
    speakerEnabled,
    speakerSolo,
    intensity,
  };
  const activeRef = React.useRef(active);
  activeRef.current = active;
  const idleFrames = React.useRef(0);
  const wakeRef = React.useRef<() => void>(() => {});

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

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const width = container.clientWidth;
      const height = container.clientHeight;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      blobCanvas.width = canvas.width;
      blobCanvas.height = canvas.height;
      blurCanvas.width = canvas.width;
      blurCanvas.height = canvas.height;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      blobCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      blurCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      lastBlurTime = -Infinity;
      initializedSize.current = false;
    };
    resize();
    let resizeFrame: number | null = null;
    const observer = new ResizeObserver(() => {
      if (resizeFrame !== null) return;
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = null;
        resize();
        wakeRef.current();
      });
    });
    observer.observe(container);

    let lastTime = performance.now();
    const draw = (time: number) => {
      const delta = Math.min(0.1, (time - lastTime) / 1000);
      lastTime = time;
      const {
        channels: currentChannels,
        routing: currentRouting,
        selectedStem: currentSelected,
        colors: currentColors,
        channelCounts: currentCounts,
        speakerEnabled: currentSpeakerEnabled,
        speakerSolo: currentSolo,
        intensity: currentIntensity,
      } = propsRef.current;
      const width = canvas.width / (window.devicePixelRatio || 1);
      const height = canvas.height / (window.devicePixelRatio || 1);
      // Same gutter arithmetic as ElevationView: padTop clears the intensity
      // chip by exactly `pad`, so every edge reads with one margin.
      const pad = 40;
      const CHIP_TOP = 8;
      const CHIP_HEIGHT = 22;
      const padX = pad;
      const padTop = CHIP_TOP + CHIP_HEIGHT + pad + 8;
      const padBottom = pad;
      const plotWidth = Math.max(1, width - padX * 2);
      const plotHeight = Math.max(1, height - padTop - padBottom);
      const floorY = height - padBottom;
      const toX = (pan: number) =>
        padX + Math.min(1, Math.max(0, pan)) * plotWidth;
      const toY = (centroid: number) =>
        floorY - Math.min(1, Math.max(0, centroid)) * plotHeight;

      const field = ctx.createLinearGradient(0, 0, 0, height);
      field.addColorStop(0, canvasTheme.plotField);
      field.addColorStop(1, canvasTheme.plotFieldCore);
      ctx.save();
      ctx.globalAlpha = initializedSize.current ? 0.3 : 1;
      ctx.fillStyle = field;
      ctx.fillRect(0, 0, width, height);
      const shade = ctx.createRadialGradient(
        (padX + width - padX) / 2,
        floorY,
        plotWidth * 0.05,
        (padX + width - padX) / 2,
        floorY,
        plotWidth * 0.75,
      );
      shade.addColorStop(0, canvasTheme.plotShadeStrong);
      shade.addColorStop(1, "rgba(10, 132, 255, 0)");
      ctx.fillStyle = shade;
      ctx.fillRect(0, 0, width, height);
      ctx.restore();
      initializedSize.current = true;

      ctx.lineWidth = 1;
      const vertical = (pan: number, color: string) => {
        const x = Math.round(toX(pan)) + 0.5;
        ctx.strokeStyle = color;
        ctx.beginPath();
        ctx.moveTo(x, padTop);
        ctx.lineTo(x, floorY);
        ctx.stroke();
      };
      vertical(0.25, canvasTheme.gridSoft);
      vertical(0.75, canvasTheme.gridSoft);
      vertical(0.5, canvasTheme.grid);
      const horizontal = (fraction: number, color: string) => {
        const y = Math.round(floorY - fraction * plotHeight) + 0.5;
        ctx.strokeStyle = color;
        ctx.beginPath();
        ctx.moveTo(padX, y);
        ctx.lineTo(width - padX, y);
        ctx.stroke();
      };
      horizontal(0, canvasTheme.grid);
      horizontal(0.33, canvasTheme.gridSoft);
      horizontal(0.66, canvasTheme.gridSoft);
      horizontal(1, canvasTheme.grid);

      ctx.save();
      ctx.font = "600 9px system-ui, sans-serif";
      ctx.letterSpacing = "0.08em";
      ctx.fillStyle = canvasTheme.label;
      ctx.textAlign = "left";
      ctx.fillText("HIGH", 8, padTop + 8);
      ctx.fillText("LOW", 8, floorY + 3);
      ctx.textAlign = "center";
      ctx.fillText("C", toX(0.5), floorY + 26);
      ctx.restore();

      // The two speakers anchor the ends of the pan axis, drawn on the plot's
      // top corners so they never sit under the blob field's densest region.
      const nextSpeakerHits: SpeakerHitTarget[] = [];
      ctx.font = "500 11px system-ui, sans-serif";
      ctx.textAlign = "center";
      const speakerPan: Record<string, number> = { FL: 0, FR: 1 };
      for (const channel of currentChannels) {
        const pan = speakerPan[channel];
        if (pan === undefined) continue;
        const x = toX(pan);
        const muted = currentSpeakerEnabled[channel] === false;
        const soloed = currentSolo.has(channel);
        const silent = !muted && currentSolo.size > 0 && !soloed;
        drawSpeakerPoint(ctx, x, padTop, 4, muted, soloed, silent);
        ctx.fillStyle = muted
          ? canvasTheme.muteLabel
          : soloed
            ? canvasTheme.meterWarn
            : silent
              ? canvasTheme.label
              : canvasTheme.labelStrong;
        ctx.fillText(channel === "FL" ? "L" : "R", x, padTop - 10);
        nextSpeakerHits.push({ channel, x, y: padTop, radius: 12 });
      }
      speakerHitTargets.current = nextSpeakerHits;

      const voices: Voice[] = [];
      for (const stem of Object.keys(currentRouting)) {
        const pan = stemPan(currentRouting[stem] || {});
        const base = stem.split("@", 1)[0];
        if ((currentCounts?.[stem] ?? 2) >= 2) {
          const spread = stereoSpread(pan);
          voices.push({
            key: `${stem}:L`,
            stem,
            base,
            pan: pan - spread,
            sizeScale: 0.8,
          });
          voices.push({
            key: `${stem}:R`,
            stem,
            base,
            pan: pan + spread,
            sizeScale: 0.8,
          });
        } else {
          voices.push({ key: stem, stem, base, pan, sizeScale: 1 });
        }
      }

      // Same melt treatment as the Haze and Elevation views: resolve smoothed
      // voices, paint oversized additive blobs into an offscreen buffer, then
      // blur + screen-composite it back onto the main canvas.
      type Resolved = {
        point: { x: number; y: number };
        blobRadius: number;
        emphasis: number;
        level: number;
        r: number;
        g: number;
        b: number;
      };
      const resolved: Resolved[] = [];
      for (const voice of voices) {
        const spectrum = stemSpectrum.current.get(voice.base);
        const level = spectrum?.level ?? 0;
        const targetY = spectrum ? spectrum.centroid : 0.42;

        const previous = smoothed.current.get(voice.key);
        const next: SmoothedVoice = previous
          ? {
              x: previous.x + (voice.pan - previous.x) * Math.min(1, delta * 6),
              y: previous.y + (targetY - previous.y) * Math.min(1, delta * 6),
              level:
                previous.level +
                (level - previous.level) * Math.min(1, delta * 8),
            }
          : { x: voice.pan, y: targetY, level };
        smoothed.current.set(voice.key, next);

        const audible = Math.min(1, next.level * 8);
        const color = currentColors[voice.stem] || canvasTheme.stemFallback;
        const [r, g, b] = hexToRgb(color);
        const dimmed =
          Boolean(currentSelected) && currentSelected !== voice.stem;
        const emphasis =
          (currentSelected === voice.stem ? 1 : dimmed ? 0.35 : 0.8) * audible;
        const point = { x: toX(next.x), y: toY(next.y) };
        const blobRadius =
          (28 + next.level * 50) *
          voice.sizeScale *
          (currentSelected === voice.stem ? 1.15 : 1);

        if (emphasis > 0.005)
          resolved.push({
            point,
            blobRadius,
            emphasis,
            level: next.level,
            r,
            g,
            b,
          });
      }

      blobCtx.clearRect(0, 0, width, height);
      blobCtx.globalCompositeOperation = "lighter";

      const alphaScale = lerp(MIN_ALPHA_SCALE, 1, currentIntensity);
      for (const { point, blobRadius, emphasis, level, r, g, b } of resolved) {
        const meltRadius = blobRadius * 3.6;
        const gradient = blobCtx.createRadialGradient(
          point.x,
          point.y,
          0,
          point.x,
          point.y,
          meltRadius,
        );
        gradient.addColorStop(
          0,
          `rgba(${r}, ${g}, ${b}, ${(0.45 + level * 0.35) * emphasis * alphaScale})`,
        );
        gradient.addColorStop(
          0.3,
          `rgba(${r}, ${g}, ${b}, ${(0.22 + level * 0.15) * emphasis * alphaScale})`,
        );
        gradient.addColorStop(
          0.65,
          `rgba(${r}, ${g}, ${b}, ${0.09 * emphasis * alphaScale})`,
        );
        gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
        blobCtx.fillStyle = gradient;
        blobCtx.beginPath();
        blobCtx.arc(point.x, point.y, meltRadius, 0, TAU);
        blobCtx.fill();
      }
      blobCtx.globalCompositeOperation = "source-over";

      const blurPx = Math.max(
        12,
        Math.min(42, Math.min(plotWidth, plotHeight) * 0.16),
      );
      if (time - lastBlurTime >= 1000 / 30) {
        blurCtx.clearRect(0, 0, width, height);
        blurCtx.save();
        blurCtx.filter = `blur(${blurPx}px)`;
        blurCtx.drawImage(blobCanvas, 0, 0, width, height);
        blurCtx.restore();
        lastBlurTime = time;
      }
      ctx.save();
      ctx.globalCompositeOperation = "screen";
      ctx.drawImage(blurCanvas, 0, 0, width, height);
      ctx.restore();

      idleFrames.current =
        !activeRef.current && resolved.length === 0
          ? idleFrames.current + 1
          : 0;
      if (activeRef.current || idleFrames.current < SETTLE_FRAMES) {
        frame.current = window.requestAnimationFrame(draw);
      } else {
        frame.current = null;
      }
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
      if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame);
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    };
  }, [stemSpectrum]);

  React.useEffect(() => {
    wakeRef.current();
  }, [
    active,
    channels,
    routing,
    selectedStem,
    colors,
    channelCounts,
    speakerEnabled,
    speakerSolo,
    intensity,
  ]);

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    let closestSpeaker: { channel: string; distance: number } | null = null;
    for (const hit of speakerHitTargets.current) {
      const distance = Math.hypot(hit.x - x, hit.y - y);
      if (
        distance <= hit.radius &&
        (!closestSpeaker || distance < closestSpeaker.distance)
      ) {
        closestSpeaker = { channel: hit.channel, distance };
      }
    }
    if (closestSpeaker) {
      if (event.altKey) onSoloSpeaker(closestSpeaker.channel);
      else onToggleSpeaker(closestSpeaker.channel);
    }
  };

  return (
    <div
      className={`relative flex flex-col overflow-hidden rounded-lg border ${className || ""}`}
      style={{ backgroundColor: canvasTheme.plotField }}
    >
      <div ref={containerRef} className="min-h-0 flex-1">
        <canvas
          ref={canvasRef}
          className="h-full w-full cursor-pointer"
          onPointerDown={handlePointerDown}
        />
      </div>
      <IntensitySlider
        value={intensity}
        onChange={onIntensity}
        label="Panorama intensity"
        className="absolute left-2 top-2 z-10"
      />
    </div>
  );
}

export default React.memo(StereoPanoramaViewImpl);
