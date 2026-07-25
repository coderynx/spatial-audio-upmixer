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
type SpeakerHitTarget = { channel: string; x: number; y: number; radius: number };

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
  // Per-speaker mute — the preview renders the channel bed (see
  // useStemPreview.ts), so a speaker can be silenced independently of any
  // stem. Clicking a speaker's point on the graph toggles it directly.
  speakerEnabled: Record<string, boolean>;
  onToggleSpeaker: (channel: string) => void;
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
  speakerEnabled,
  onToggleSpeaker,
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
  const propsRef = React.useRef({ channels, routing, selectedStem, colors, channelCounts, speakerEnabled });
  propsRef.current = { channels, routing, selectedStem, colors, channelCounts, speakerEnabled };

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
    const observer = new ResizeObserver(resize);
    observer.observe(container);

    let lastTime = performance.now();
    const draw = (time: number) => {
      const delta = Math.min(0.1, (time - lastTime) / 1000);
      lastTime = time;
      const { channels: currentChannels, routing: currentRouting, selectedStem: currentSelected, colors: currentColors, channelCounts: currentCounts, speakerEnabled: currentSpeakerEnabled } = propsRef.current;
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
      const nextSpeakerHits: SpeakerHitTarget[] = [];
      ctx.font = "600 11px system-ui, sans-serif";
      for (const channel of floorChannels) {
        const angle = vecAngle(speakerCoordinates[channel]);
        const point = polar(center, radius, angle);
        const muted = currentSpeakerEnabled[channel] === false;
        ctx.fillStyle = muted ? "#ef4444" : "#334155";
        ctx.beginPath();
        ctx.arc(point.x, point.y, muted ? 4 : 5, 0, TAU);
        ctx.fill();
        const labelPoint = polar(center, radius + 14, angle);
        ctx.fillStyle = muted ? "#f87171" : "#cbd5e1";
        ctx.textAlign = "center";
        ctx.fillText(speakerLabels[channel] || channel, labelPoint.x, labelPoint.y + 4);
        nextSpeakerHits.push({ channel, x: point.x, y: point.y, radius: 12 });
      }
      ctx.font = "600 9px system-ui, sans-serif";
      for (const channel of topChannels) {
        const angle = vecAngle(speakerCoordinates[channel]);
        const point = polar(center, heightRingRadius, angle);
        const muted = currentSpeakerEnabled[channel] === false;
        ctx.fillStyle = muted ? "#ef4444" : "#475569";
        ctx.beginPath();
        ctx.arc(point.x, point.y, muted ? 3.5 : 4, 0, TAU);
        ctx.fill();
        const labelPoint = polar(center, heightRingRadius + 12, angle);
        ctx.fillStyle = muted ? "#f87171" : "#94a3b8";
        ctx.fillText(speakerLabels[channel] || channel, labelPoint.x, labelPoint.y + 3);
        nextSpeakerHits.push({ channel, x: point.x, y: point.y, radius: 11 });
      }
      // LFE has no direction (it's a non-positional bass bus), so its mute
      // point sits just below the listener marker instead of on the ring.
      if (currentChannels.includes("LFE")) {
        const lfePoint = { x: center.x, y: center.y + 16 };
        const lfeMuted = currentSpeakerEnabled.LFE === false;
        ctx.fillStyle = lfeMuted ? "#ef4444" : "#334155";
        ctx.beginPath();
        ctx.arc(lfePoint.x, lfePoint.y, lfeMuted ? 4 : 5, 0, TAU);
        ctx.fill();
        ctx.font = "600 9px system-ui, sans-serif";
        ctx.fillStyle = lfeMuted ? "#f87171" : "#94a3b8";
        ctx.textAlign = "center";
        ctx.fillText("LFE", lfePoint.x, lfePoint.y + 15);
        nextSpeakerHits.push({ channel: "LFE", x: lfePoint.x, y: lfePoint.y, radius: 12 });
      }
      speakerHitTargets.current = nextSpeakerHits;

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

      // Two-pass render: resolve smoothed voice state + hit targets first,
      // then paint tendrils and oversized soft blobs into an offscreen
      // buffer that gets blurred and screen-composited back onto the main
      // canvas. That blur is what turns separate circular halos into one
      // continuous, melted field — additive blending in the buffer makes
      // overlapping stems brighten into shared "hot" cores instead of just
      // stacking flat discs.
      const nextHits: HitTarget[] = [];
      type Resolved = { voice: Voice; point: { x: number; y: number }; blobRadius: number; emphasis: number; level: number; r: number; g: number; b: number };
      const resolved: Resolved[] = [];
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

      // Faint tendrils between nearby, simultaneously active stems — a
      // visual cue that they're melting into each other, not just glowing
      // in place.
      const tendrilReach = radius * 0.85;
      for (let i = 0; i < resolved.length; i++) {
        for (let j = i + 1; j < resolved.length; j++) {
          const a = resolved[i];
          const c = resolved[j];
          if (a.voice.stem === c.voice.stem) continue;
          const dist = Math.hypot(a.point.x - c.point.x, a.point.y - c.point.y);
          if (dist >= tendrilReach) continue;
          const strength = (1 - dist / tendrilReach) * Math.min(a.emphasis, c.emphasis) * Math.min(a.level, c.level) * 6;
          if (strength <= 0.01) continue;
          const tendril = blobCtx.createLinearGradient(a.point.x, a.point.y, c.point.x, c.point.y);
          tendril.addColorStop(0, `rgba(${a.r}, ${a.g}, ${a.b}, ${Math.min(0.5, strength)})`);
          tendril.addColorStop(1, `rgba(${c.r}, ${c.g}, ${c.b}, ${Math.min(0.5, strength)})`);
          blobCtx.strokeStyle = tendril;
          blobCtx.lineWidth = Math.max(2, Math.min(a.blobRadius, c.blobRadius) * 0.35);
          blobCtx.beginPath();
          blobCtx.moveTo(a.point.x, a.point.y);
          blobCtx.lineTo(c.point.x, c.point.y);
          blobCtx.stroke();
        }
      }

      for (const { point, blobRadius, emphasis, level, r, g, b } of resolved) {
        const meltRadius = blobRadius * 1.55;
        const gradient = blobCtx.createRadialGradient(point.x, point.y, 0, point.x, point.y, meltRadius);
        gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(0.4 + level * 0.3) * emphasis})`);
        gradient.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${0.16 * emphasis})`);
        gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
        blobCtx.fillStyle = gradient;
        blobCtx.beginPath();
        blobCtx.arc(point.x, point.y, meltRadius, 0, TAU);
        blobCtx.fill();
      }
      blobCtx.globalCompositeOperation = "source-over";

      const blurPx = Math.max(6, Math.min(26, radius * 0.14));
      ctx.save();
      ctx.filter = `blur(${blurPx}px)`;
      ctx.globalCompositeOperation = "screen";
      ctx.drawImage(blobCanvas, 0, 0, width, height);
      ctx.restore();

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

  return <div className={`relative flex flex-col overflow-hidden rounded-lg border bg-slate-950 text-slate-100 ${className || ""}`}>
    <div className="pointer-events-none relative z-10 flex items-center justify-between px-3 pt-3 text-xs text-slate-300">
      <span>Haze view</span>
      <button className="pointer-events-auto hover:text-white" onClick={() => onSelectStem(null)}>{selectedStem || "Aggregate output"}</button>
    </div>
    <div ref={containerRef} className="min-h-0 flex-1">
      <canvas ref={canvasRef} className="h-full w-full cursor-pointer" onPointerDown={handlePointerDown} />
    </div>
  </div>;
}
