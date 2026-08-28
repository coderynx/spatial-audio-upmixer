import { canvasTheme } from "@/lib/canvasTheme";

/** Shared speaker marker for the Haze and Elevation displays.
 *
 * Drawn as a hollow ring over an opaque core rather than a flat disc: the
 * stem haze is painted underneath these points, and a solid grey blob both
 * muddied the colour behind it and read as part of the signal. The ring keeps
 * the speaker legible as fixed structure while the core stops the haze
 * bleeding through. Muted flips to a solid fill so the state is unmistakable
 * at a glance. */
export function drawSpeakerPoint(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  radius: number,
  muted: boolean,
  soloed = false,
  inactive = false,
) {
  if (muted) {
    ctx.fillStyle = canvasTheme.mute;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  ctx.fillStyle = soloed ? canvasTheme.meterWarn : inactive ? canvasTheme.plotField : canvasTheme.plotFieldCore;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = soloed ? canvasTheme.meterWarn : inactive ? canvasTheme.grid : canvasTheme.ring;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(x, y, radius - 0.75, 0, Math.PI * 2);
  ctx.stroke();
}
