import * as React from "react";
import type { StemRouting } from "@/api";
import { MIN_ALPHA_SCALE, SETTLE_FRAMES, canvasTheme, hexToRgb, lerp } from "@/lib/canvasTheme";
import { IntensitySlider } from "./IntensitySlider";
import { drawSpeakerPoint } from "./speakerMarker";
import { heightFraction, speakerCoordinates, speakerDisplayLabel, stemPosition, stemPositionStereo, vecAngle } from "@/lib/spatial";
import type { StemSpectrum } from "./audioEngine";

// NUGEN Halo Upmix-style "Haze View": a 2D radar where radius encodes
// spectral centroid (bass at the center, treble at the edge) and angle
// encodes speaker direction (compass-style, front = up). A separate dashed
// outer ring shows height-channel content per stem, since this projection
// is otherwise a flat floor-plan and would lose the y axis entirely.

type Voice = { key: string; stem: string; base: string; angle: number; heightAngle: number | null; sizeScale: number };

type SmoothedVoice = { angle: number; radius: number; heightRadius: number; level: number; heightLevel: number };

type HitTarget = { stem: string; x: number; y: number; radius: number };
type SpeakerHitTarget = { channel: string; x: number; y: number; radius: number };

const TAU = Math.PI * 2;

function lerpAngle(from: number, to: number, t: number) {
  let delta = (to - from) % TAU;
  if (delta > Math.PI) delta -= TAU;
  if (delta < -Math.PI) delta += TAU;
  return from + delta * t;
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
  stemSpectrum: React.MutableRefObject<Map<string, StemSpectrum>>;
  // Per-speaker mute — the preview renders the channel bed (see
  // useStemPreview.ts), so a speaker can be silenced independently of any
  // stem. Clicking a speaker's point on the graph toggles it directly.
  speakerEnabled: Record<string, boolean>;
  onToggleSpeaker: (channel: string) => void;
  // True while preview audio is live-updating `stemSpectrum` (i.e.
  // `preview.playing`). While inactive, the draw loop keeps running only
  // until every voice has faded out and the trailing background fade has
  // settled, then stops — see `SETTLE_FRAMES`.
  active: boolean;
  // Plain opacity control (0..1), persisted per project in
  // `viewState.hazeIntensity` — see `ProjectDetailPage`'s `useProjectViewState`.
  intensity: number;
  onIntensity: (next: number) => void;
  className?: string;
};

function HazeViewImpl({
  channels,
  routing,
  selectedStem,
  colors,
  channelCounts,
  onSelectStem,
  stemSpectrum,
  speakerEnabled,
  onToggleSpeaker,
  active,
  intensity,
  onIntensity,
  className,
}: HazeViewProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const blobCanvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const smoothed = React.useRef<Map<string, SmoothedVoice>>(new Map());
  const hitTargets = React.useRef<HitTarget[]>([]);
  const speakerHitTargets = React.useRef<SpeakerHitTarget[]>([]);
  const frame = React.useRef<number | null>(null);
  const initializedSize = React.useRef(false);
  // Latest props, read fresh by the draw loop without restarting it.
  const propsRef = React.useRef({ channels, routing, selectedStem, colors, channelCounts, speakerEnabled, intensity });
  propsRef.current = { channels, routing, selectedStem, colors, channelCounts, speakerEnabled, intensity };
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

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const width = container.clientWidth;
      const height = container.clientHeight;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      blobCanvas.width = canvas.width;
      blobCanvas.height = canvas.height;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      blobCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      initializedSize.current = false;
    };
    resize();
    // Resizing a canvas clears its pixel buffer, and a ResizeObserver fires
    // after layout but before paint, so `wakeRef` repaints synchronously
    // rather than scheduling a frame. Scheduling would let the browser paint
    // the cleared buffer once per resize — which during an animated resize
    // (the 150ms sidebar collapse resizes on every frame) leaves the display
    // blank for the whole transition.
    const observer = new ResizeObserver(() => {
      resize();
      wakeRef.current();
    });
    observer.observe(container);

    let lastTime = performance.now();
    const draw = (time: number) => {
      const delta = Math.min(0.1, (time - lastTime) / 1000);
      lastTime = time;
      const { channels: currentChannels, routing: currentRouting, selectedStem: currentSelected, channelCounts: currentCounts, speakerEnabled: currentSpeakerEnabled, intensity: currentIntensity } = propsRef.current;
      const width = canvas.width / (window.devicePixelRatio || 1);
      const height = canvas.height / (window.devicePixelRatio || 1);
      const center = { x: width / 2, y: height / 2 };
      const radius = Math.min(width, height) / 2 * 0.62;
      const heightRingRadius = radius * 1.18;

      // Deep-navy plot field with a systemBlue wash pooled toward the
      // listener position, echoing the shaded region Logic paints under a
      // Channel EQ curve. Painting it through globalAlpha (rather than as a
      // flat translucent colour) keeps the motion-trail fade from flattening
      // the gradient out over successive frames.
      const field = ctx.createRadialGradient(center.x, center.y, 0, center.x, center.y, heightRingRadius * 1.35);
      field.addColorStop(0, canvasTheme.plotFieldCore);
      field.addColorStop(1, canvasTheme.plotField);
      ctx.save();
      ctx.globalAlpha = initializedSize.current ? 0.3 : 1;
      ctx.fillStyle = field;
      ctx.fillRect(0, 0, width, height);
      const shade = ctx.createRadialGradient(center.x, center.y, radius * 0.1, center.x, center.y, heightRingRadius);
      shade.addColorStop(0, canvasTheme.plotShade);
      shade.addColorStop(1, "rgba(10, 132, 255, 0)");
      ctx.fillStyle = shade;
      ctx.fillRect(0, 0, width, height);
      ctx.restore();
      initializedSize.current = true;

      // Radar guide rings (frequency axis). Inner guides sit back in
      // `gridSoft` and only the outer speaker ring is drawn at full strength,
      // so the substrate reads as depth rather than competing with the stem
      // haze painted over it.
      ctx.lineWidth = 1;
      ctx.strokeStyle = canvasTheme.gridSoft;
      for (const fraction of [0.33, 0.66]) {
        ctx.beginPath();
        ctx.arc(center.x, center.y, radius * fraction, 0, TAU);
        ctx.stroke();
      }
      ctx.strokeStyle = canvasTheme.grid;
      ctx.beginPath();
      ctx.arc(center.x, center.y, radius, 0, TAU);
      ctx.stroke();
      ctx.save();
      ctx.setLineDash([2, 4]);
      ctx.strokeStyle = canvasTheme.gridSoft;
      ctx.beginPath();
      ctx.arc(center.x, center.y, heightRingRadius, 0, TAU);
      ctx.stroke();
      ctx.restore();

      ctx.textAlign = "center";
      ctx.save();
      ctx.font = "600 9px system-ui, sans-serif";
      ctx.letterSpacing = "0.08em";
      ctx.fillStyle = canvasTheme.label;
      ctx.fillText("FRONT", center.x, center.y - heightRingRadius - 18);
      ctx.fillText("BACK", center.x, center.y + heightRingRadius + 22);
      ctx.restore();

      // Speaker labels: floor channels on the main ring, height channels on
      // the dashed outer ring so the two dimensions don't overlap visually.
      const floorChannels = currentChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel] && speakerCoordinates[channel].y === 0);
      const topChannels = currentChannels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel] && speakerCoordinates[channel].y > 0);

      const nextSpeakerHits: SpeakerHitTarget[] = [];
      ctx.font = "500 11px system-ui, sans-serif";
      for (const channel of floorChannels) {
        const angle = vecAngle(speakerCoordinates[channel]);
        const point = polar(center, radius, angle);
        const muted = currentSpeakerEnabled[channel] === false;
        drawSpeakerPoint(ctx, point.x, point.y, 4, muted);
        const labelPoint = polar(center, radius + 14, angle);
        ctx.fillStyle = muted ? canvasTheme.muteLabel : canvasTheme.labelStrong;
        ctx.textAlign = "center";
        ctx.fillText(speakerDisplayLabel(channel, currentChannels), labelPoint.x, labelPoint.y + 4);
        nextSpeakerHits.push({ channel, x: point.x, y: point.y, radius: 12 });
      }
      ctx.font = "500 9px system-ui, sans-serif";
      for (const channel of topChannels) {
        const angle = vecAngle(speakerCoordinates[channel]);
        const point = polar(center, heightRingRadius, angle);
        const muted = currentSpeakerEnabled[channel] === false;
        drawSpeakerPoint(ctx, point.x, point.y, 3.25, muted);
        const labelPoint = polar(center, heightRingRadius + 12, angle);
        ctx.fillStyle = muted ? canvasTheme.muteLabel : canvasTheme.label;
        ctx.fillText(speakerDisplayLabel(channel, currentChannels), labelPoint.x, labelPoint.y + 3);
        nextSpeakerHits.push({ channel, x: point.x, y: point.y, radius: 11 });
      }
      // LFE has no direction (it's a non-positional bass bus), so its mute
      // point sits at the radar's center (the listener position) instead of
      // on the ring.
      if (currentChannels.includes("LFE")) {
        const lfeMuted = currentSpeakerEnabled.LFE === false;
        drawSpeakerPoint(ctx, center.x, center.y, 4, lfeMuted);
        ctx.font = "500 9px system-ui, sans-serif";
        ctx.fillStyle = lfeMuted ? canvasTheme.muteLabel : canvasTheme.label;
        ctx.textAlign = "center";
        ctx.fillText("LFE", center.x, center.y + 16);
        nextSpeakerHits.push({ channel: "LFE", x: center.x, y: center.y, radius: 12 });
      }
      speakerHitTargets.current = nextSpeakerHits;

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

      // Two-pass render: resolve voice state and hit targets, then paint soft
      // blobs into an offscreen buffer that is blurred and screen-composited
      // back — additive blending is what melts overlapping halos into one field.
      const nextHits: HitTarget[] = [];
      type Resolved = { voice: Voice; point: { x: number; y: number }; blobRadius: number; emphasis: number; level: number; r: number; g: number; b: number };
      const resolved: Resolved[] = [];
      for (const voice of voices) {
        const spectrum = stemSpectrum.current.get(voice.base);
        const level = spectrum?.level ?? 0;
        const targetRadius = (spectrum ? spectrum.centroid : 0.42) * radius;
        const targetHeightLevel = voice.heightAngle !== null ? (spectrum?.level ?? 0) : 0;

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

        const color = propsRef.current.colors[voice.stem] || canvasTheme.stemFallback;
        const [r, g, b] = hexToRgb(color);
        const dimmed = Boolean(currentSelected) && currentSelected !== voice.stem;
        const emphasis = (currentSelected === voice.stem ? 1 : dimmed ? 0.35 : 0.8) * audible;
        const point = polar(center, next.radius, next.angle);
        const blobRadius = (radius * 0.32 + next.level * radius * 0.28) * voice.sizeScale * (currentSelected === voice.stem ? 1.1 : 1);

        if (emphasis > 0.005) resolved.push({ voice, point, blobRadius, emphasis, level: next.level, r, g, b });

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
      hitTargets.current = nextHits;

      blobCtx.clearRect(0, 0, width, height);
      blobCtx.globalCompositeOperation = "lighter";

      // No tendrils: soft halos overlap and additively brighten on their own.
      // currentIntensity is a plain opacity control (alphaScale dims every
      // stop uniformly); reach/blur stay fixed, floored at MIN_ALPHA_SCALE.
      const alphaScale = lerp(MIN_ALPHA_SCALE, 1, currentIntensity);
      for (const { point, blobRadius, emphasis, level, r, g, b } of resolved) {
        const meltRadius = blobRadius * 2.1;
        const gradient = blobCtx.createRadialGradient(point.x, point.y, 0, point.x, point.y, meltRadius);
        gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(0.45 + level * 0.35) * emphasis * alphaScale})`);
        gradient.addColorStop(0.3, `rgba(${r}, ${g}, ${b}, ${(0.22 + level * 0.15) * emphasis * alphaScale})`);
        gradient.addColorStop(0.65, `rgba(${r}, ${g}, ${b}, ${0.09 * emphasis * alphaScale})`);
        gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
        blobCtx.fillStyle = gradient;
        blobCtx.beginPath();
        blobCtx.arc(point.x, point.y, meltRadius, 0, TAU);
        blobCtx.fill();
      }
      blobCtx.globalCompositeOperation = "source-over";

      // Wider, softer blur than the halos alone would suggest — this is
      // what actually liquefies adjoining blobs into one continuous field
      // instead of a cluster of visibly separate soft discs. Fixed regardless
      // of intensity — only opacity responds to the slider.
      const blurPx = Math.max(10, Math.min(36, radius * 0.2));
      ctx.save();
      ctx.filter = `blur(${blurPx}px)`;
      ctx.globalCompositeOperation = "screen";
      ctx.drawImage(blobCanvas, 0, 0, width, height);
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
  }, [active, channels, routing, selectedStem, colors, channelCounts, speakerEnabled, intensity]);

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    // Speaker points take priority over stem selection — they're small,
    // fixed targets, and a stem's much larger blob often overlaps them.
    let closestSpeaker: { channel: string; distance: number } | null = null;
    for (const hit of speakerHitTargets.current) {
      const distance = Math.hypot(hit.x - x, hit.y - y);
      if (distance <= hit.radius && (!closestSpeaker || distance < closestSpeaker.distance)) {
        closestSpeaker = { channel: hit.channel, distance };
      }
    }
    if (closestSpeaker) {
      onToggleSpeaker(closestSpeaker.channel);
      return;
    }

    let closest: { stem: string; distance: number } | null = null;
    for (const hit of hitTargets.current) {
      const distance = Math.hypot(hit.x - x, hit.y - y);
      if (distance <= hit.radius && (!closest || distance < closest.distance)) closest = { stem: hit.stem, distance };
    }
    onSelectStem(closest ? (closest.stem === selectedStem ? null : closest.stem) : null);
  };

  // The wrapper takes the canvas's own background so the panel and the
  // painted surface are seamless — these displays read as one instrument
  // face, the way Logic's do, rather than a card with artwork inside it.
  return <div
    className={`relative flex flex-col overflow-hidden rounded-lg border ${className || ""}`}
    style={{ backgroundColor: canvasTheme.plotField }}
  >
    <div ref={containerRef} className="min-h-0 flex-1">
      <canvas ref={canvasRef} className="h-full w-full cursor-pointer" onPointerDown={handlePointerDown} />
    </div>
    <IntensitySlider
      value={intensity}
      onChange={onIntensity}
      label="Haze intensity"
      className="absolute left-2 top-2 z-10"
    />
    <button
      type="button"
      onClick={() => onSelectStem(null)}
      className="absolute right-2 top-2 z-10 rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-[11px] text-white/70 backdrop-blur-sm transition-colors hover:bg-white/10 hover:text-white"
    >
      {selectedStem || "Aggregate output"}
    </button>
  </div>;
}

export default React.memo(HazeViewImpl);
