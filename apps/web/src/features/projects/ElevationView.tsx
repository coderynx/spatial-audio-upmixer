import * as React from "react";
import type { StemRouting } from "@/api";
import { MIN_ALPHA_SCALE, SETTLE_FRAMES, canvasTheme, hexToRgb, lerp } from "@/lib/canvasTheme";
import { IntensitySlider } from "./IntensitySlider";
import { drawSpeakerPoint } from "./speakerMarker";
import { speakerCoordinates, speakerDisplayLabel, stemPosition, stemPositionStereo } from "@/lib/spatial";
import { cn } from "@/lib/utils";
import type { StemSpectrum } from "./audioEngine";

// Secondary "elevation" view: a front-on cross-section showing the vertical
// (height) axis that the Haze view's top-down radar collapses away. X = the
// speaker's real left/right position, Y = its real floor/height position —
// unlike the radar, this uses actual routed coordinates for placement, not
// spectral centroid, matching NUGEN Halo Upmix's height panel.

type Voice = { key: string; stem: string; base: string; x: number; y: number; sizeScale: number };
type SmoothedVoice = { x: number; y: number; level: number };
type SpeakerHitTarget = { channel: string; x: number; y: number; radius: number };

const MAX_HEIGHT = 0.6;

export type ElevationViewProps = {
  channels: string[];
  routing: StemRouting;
  selectedStem: string | null;
  colors: Record<string, string>;
  channelCounts?: Record<string, number>;
  stemSpectrum: React.MutableRefObject<Map<string, StemSpectrum>>;
  // Per-speaker mute — same channel-bed model as HazeView (see
  // useStemPreview.ts). Clicking a speaker's point on the graph toggles it.
  speakerEnabled: Record<string, boolean>;
  speakerSolo: ReadonlySet<string>;
  onToggleSpeaker: (channel: string) => void;
  onSoloSpeaker: (channel: string) => void;
  // True while preview audio is live-updating `stemSpectrum` (i.e.
  // `preview.playing`) — see HazeView's `active` prop for the idle-gating
  // rationale, identical here.
  active: boolean;
  // Plain opacity control (0..1), persisted per project in
  // `viewState.elevationIntensity` — kept independent of Haze's own
  // intensity (a user may want this view calmer than Haze, or vice versa).
  intensity: number;
  onIntensity: (next: number) => void;
  className?: string;
};

function ElevationViewImpl({
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
}: ElevationViewProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const blobCanvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const blurCanvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const smoothed = React.useRef<Map<string, SmoothedVoice>>(new Map());
  const speakerHitTargets = React.useRef<SpeakerHitTarget[]>([]);
  const frame = React.useRef<number | null>(null);
  const initializedSize = React.useRef(false);
  const propsRef = React.useRef({ channels, routing, selectedStem, colors, channelCounts, speakerEnabled, speakerSolo, intensity });
  propsRef.current = { channels, routing, selectedStem, colors, channelCounts, speakerEnabled, speakerSolo, intensity };
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
    if (!blobCanvasRef.current) blobCanvasRef.current = document.createElement("canvas");
    const blobCanvas = blobCanvasRef.current;
    const blobCtx = blobCanvas.getContext("2d");
    if (!blobCtx) return;
    if (!blurCanvasRef.current) blurCanvasRef.current = document.createElement("canvas");
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
    // Repaints synchronously rather than scheduling a frame: a resize clears the
    // canvas, and scheduling would paint the cleared buffer during an animated resize.
    const observer = new ResizeObserver(() => {
      resize();
      wakeRef.current();
    });
    observer.observe(container);

    let lastTime = performance.now();
    const draw = (time: number) => {
      const delta = Math.min(0.1, (time - lastTime) / 1000);
      lastTime = time;
      const { channels: currentChannels, routing: currentRouting, selectedStem: currentSelected, colors: currentColors, channelCounts: currentCounts, speakerEnabled: currentSpeakerEnabled, speakerSolo: currentSolo, intensity: currentIntensity } = propsRef.current;
      const width = canvas.width / (window.devicePixelRatio || 1);
      const height = canvas.height / (window.devicePixelRatio || 1);
      // padTop solves (padTop - 8) - chipBottom = pad, so the gap above the
      // topmost label matches `pad` on the other three sides exactly, not an
      // eyeballed number. CHIP_TOP/CHIP_HEIGHT mirror IntensitySlider's actual
      // rendered geometry — update both together if that component resizes.
      const pad = 40;
      const CHIP_TOP = 8;
      const CHIP_HEIGHT = 22;
      const padX = pad;
      const padTop = CHIP_TOP + CHIP_HEIGHT + pad + 8;
      const padBottom = pad;
      const plotWidth = Math.max(1, width - padX * 2);
      const plotHeight = Math.max(1, height - padTop - padBottom);
      const floorY = height - padBottom;
      const toX = (x: number) => padX + ((x + 1) / 2) * plotWidth;
      const toY = (y: number) => floorY - Math.min(1, y / MAX_HEIGHT) * plotHeight;

      // Full-bleed gradients, not clamped to the padded plot rect: a clamped
      // gradient leaves flat bands and hard edges at the rect boundary.
      const field = ctx.createLinearGradient(0, 0, 0, height);
      field.addColorStop(0, canvasTheme.plotField);
      field.addColorStop(1, canvasTheme.plotFieldCore);
      ctx.save();
      ctx.globalAlpha = initializedSize.current ? 0.3 : 1;
      ctx.fillStyle = field;
      ctx.fillRect(0, 0, width, height);
      const shade = ctx.createLinearGradient(0, height, 0, 0);
      shade.addColorStop(0, canvasTheme.plotShadeStrong);
      shade.addColorStop(0.45, canvasTheme.plotShade);
      shade.addColorStop(1, "rgba(10, 132, 255, 0)");
      ctx.fillStyle = shade;
      ctx.fillRect(0, 0, width, height);
      ctx.restore();
      initializedSize.current = true;

      // Floor / mid / top guide lines and the center pan gridline. Half-pixel
      // offsets keep these hairlines crisp instead of smearing across two
      // rows; the mid guide and centre line sit back in `gridSoft` so only
      // the floor and top bounds read as hard edges.
      ctx.lineWidth = 1;
      const guide = (fraction: number, color: string) => {
        const y = Math.round(floorY - fraction * plotHeight) + 0.5;
        ctx.strokeStyle = color;
        ctx.beginPath();
        ctx.moveTo(padX, y);
        ctx.lineTo(width - padX, y);
        ctx.stroke();
      };
      guide(0, canvasTheme.grid);
      guide(0.5, canvasTheme.gridSoft);
      guide(1, canvasTheme.grid);
      ctx.strokeStyle = canvasTheme.gridSoft;
      ctx.beginPath();
      ctx.moveTo(Math.round(toX(0)) + 0.5, padTop);
      ctx.lineTo(Math.round(toX(0)) + 0.5, floorY);
      ctx.stroke();

      // Left-aligned at the same 8px inset the intensity chip's own left
      // edge sits at (`left-2`) — one shared gutter line for every label,
      // instead of a corner label sitting apart from everything else's
      // margin.
      ctx.save();
      ctx.font = "600 9px system-ui, sans-serif";
      ctx.letterSpacing = "0.08em";
      ctx.fillStyle = canvasTheme.label;
      ctx.textAlign = "left";
      ctx.fillText("TOP", 8, padTop + 8);
      ctx.fillText("FLOOR", 8, floorY + 3);
      ctx.restore();

      // Speaker labels: floor channels along the bottom edge, height
      // channels along the top edge, both positioned by real left/right x.
      const floorChannels = currentChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel] && speakerCoordinates[channel].y === 0);
      const topChannels = currentChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel] && speakerCoordinates[channel].y > 0);
      const nextSpeakerHits: SpeakerHitTarget[] = [];
      ctx.font = "500 10px system-ui, sans-serif";
      ctx.textAlign = "center";
      for (const channel of floorChannels) {
        const x = toX(speakerCoordinates[channel].x);
        const muted = currentSpeakerEnabled[channel] === false;
        const soloed = currentSolo.has(channel);
        const silent = !muted && currentSolo.size > 0 && !soloed;
        ctx.fillStyle = muted ? canvasTheme.muteLabel : soloed ? canvasTheme.meterWarn : silent ? canvasTheme.label : canvasTheme.labelStrong;
        ctx.fillText(speakerDisplayLabel(channel, currentChannels), x, floorY + 15);
        drawSpeakerPoint(ctx, x, floorY, 3.5, muted, soloed, silent);
        nextSpeakerHits.push({ channel, x, y: floorY, radius: 12 });
      }
      ctx.font = "500 9px system-ui, sans-serif";
      for (const channel of topChannels) {
        const x = toX(speakerCoordinates[channel].x);
        const muted = currentSpeakerEnabled[channel] === false;
        const soloed = currentSolo.has(channel);
        const silent = !muted && currentSolo.size > 0 && !soloed;
        ctx.fillStyle = muted ? canvasTheme.muteLabel : soloed ? canvasTheme.meterWarn : canvasTheme.label;
        ctx.fillText(speakerDisplayLabel(channel, currentChannels), x, padTop - 8);
        drawSpeakerPoint(ctx, x, padTop, 3.5, muted, soloed, silent);
        nextSpeakerHits.push({ channel, x, y: padTop, radius: 11 });
      }
      // LFE has no left/right position (non-positional bass bus), so its
      // mute point sits in the bottom-right corner instead of on the plot —
      // the bottom-left is already taken by the "FLOOR" axis label. Centred
      // in the right gutter (half the margin in from the edge), the mirror
      // of the left-side labels sitting a consistent inset from their edge.
      if (currentChannels.includes("LFE")) {
        const lfeMuted = currentSpeakerEnabled.LFE === false;
        const lfeSoloed = currentSolo.has("LFE");
        const lfeSilent = !lfeMuted && currentSolo.size > 0 && !lfeSoloed;
        const lfePoint = { x: width - padX / 2, y: floorY };
        drawSpeakerPoint(ctx, lfePoint.x, lfePoint.y, 3.5, lfeMuted, lfeSoloed, lfeSilent);
        ctx.font = "500 9px system-ui, sans-serif";
        ctx.fillStyle = lfeMuted ? canvasTheme.muteLabel : lfeSoloed ? canvasTheme.meterWarn : canvasTheme.label;
        ctx.textAlign = "center";
        ctx.fillText("LFE", lfePoint.x, floorY + 15);
        nextSpeakerHits.push({ channel: "LFE", x: lfePoint.x, y: lfePoint.y, radius: 12 });
      }
      speakerHitTargets.current = nextSpeakerHits;

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

      // Same melt treatment as the Haze view: resolve smoothed voices, paint
      // oversized additive blobs into an offscreen buffer, then blur +
      // screen-composite that buffer onto the main canvas so overlapping
      // stems merge into one continuous field instead of separate circular
      // halos — no tendrils here either, same reasoning as Haze.
      type Resolved = { voice: Voice; point: { x: number; y: number }; blobRadius: number; emphasis: number; level: number; r: number; g: number; b: number };
      const resolved: Resolved[] = [];
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

        const color = currentColors[voice.stem] || canvasTheme.stemFallback;
        const [r, g, b] = hexToRgb(color);
        const dimmed = Boolean(currentSelected) && currentSelected !== voice.stem;
        const emphasis = (currentSelected === voice.stem ? 1 : dimmed ? 0.35 : 0.8) * audible;
        const point = { x: toX(next.x), y: toY(next.y) };
        const blobRadius = (28 + next.level * 50) * voice.sizeScale * (currentSelected === voice.stem ? 1.15 : 1);

        resolved.push({ voice, point, blobRadius, emphasis, level: next.level, r, g, b });
      }

      blobCtx.clearRect(0, 0, width, height);
      blobCtx.globalCompositeOperation = "lighter";

      // No tendrils: proximity alone carries "melting together", same as Haze —
      // but this plot's wider pan-based spread needs a larger, fainter tail
      // (meltRadius multiplier) than Haze's radial layout. Alpha stops match
      // HazeView exactly; only reach/blur differ. currentIntensity just dims
      // every stop uniformly (alphaScale), floored at MIN_ALPHA_SCALE.
      const alphaScale = lerp(MIN_ALPHA_SCALE, 1, currentIntensity);
      for (const { point, blobRadius, emphasis, level, r, g, b } of resolved) {
        const meltRadius = blobRadius * 3.6;
        const gradient = blobCtx.createRadialGradient(point.x, point.y, 0, point.x, point.y, meltRadius);
        gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(0.45 + level * 0.35) * emphasis * alphaScale})`);
        gradient.addColorStop(0.3, `rgba(${r}, ${g}, ${b}, ${(0.22 + level * 0.15) * emphasis * alphaScale})`);
        gradient.addColorStop(0.65, `rgba(${r}, ${g}, ${b}, ${0.09 * emphasis * alphaScale})`);
        gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
        blobCtx.fillStyle = gradient;
        blobCtx.beginPath();
        blobCtx.arc(point.x, point.y, meltRadius, 0, Math.PI * 2);
        blobCtx.fill();
      }
      blobCtx.globalCompositeOperation = "source-over";

      // Wider, softer blur than the halos alone suggest — boosted further
      // than Haze's own factor for the same wide-spread reason above. Fixed
      // regardless of intensity — only opacity responds to the slider.
      const blurPx = Math.max(12, Math.min(42, Math.min(plotWidth, plotHeight) * 0.16));
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

      idleFrames.current = !activeRef.current && resolved.length === 0 ? idleFrames.current + 1 : 0;
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
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    };
  }, [stemSpectrum]);

  React.useEffect(() => {
    wakeRef.current();
  }, [active, channels, routing, selectedStem, colors, channelCounts, speakerEnabled, speakerSolo, intensity]);

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let closestSpeaker: { channel: string; distance: number } | null = null;
    for (const hit of speakerHitTargets.current) {
      const distance = Math.hypot(hit.x - x, hit.y - y);
      if (distance <= hit.radius && (!closestSpeaker || distance < closestSpeaker.distance)) {
        closestSpeaker = { channel: hit.channel, distance };
      }
    }
    if (closestSpeaker) {
      if (event.altKey) onSoloSpeaker(closestSpeaker.channel);
      else onToggleSpeaker(closestSpeaker.channel);
    }
  };

  return <div
    className={cn("relative flex flex-col overflow-hidden rounded-lg border", className)}
    style={{ backgroundColor: canvasTheme.plotField }}
  >
    <div ref={containerRef} className="min-h-0 flex-1">
      <canvas ref={canvasRef} className="h-full w-full cursor-pointer" onPointerDown={handlePointerDown} />
    </div>
    <IntensitySlider
      value={intensity}
      onChange={onIntensity}
      label="Elevation intensity"
      className="absolute left-2 top-2 z-10"
    />
  </div>;
}

export default React.memo(ElevationViewImpl);
