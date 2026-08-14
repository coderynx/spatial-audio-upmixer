import * as React from "react";
import { createMeterState, levelToDb } from "@/lib/meterScale";
import { drawStripMeterBars } from "./StripMeter";
import type { MeterLevel } from "./useStemPreview";

// Consecutive silent frames before the loop stops scheduling itself — long
// enough for the peak holds to visibly decay rather than freeze.
const SETTLE_FRAMES = 40;

/** Drives one channel strip's meter bars: its own rAF loop, its own peak-hold
 * state, and the canvas it paints into. Every strip (mixer stem, mixer
 * master, or the inspector's copy of the selected stem) uses this same hook,
 * so "the fader in the inspector" and "the fader in the mixer" are provably
 * the same behaviour, not a parallel re-implementation that can drift.
 *
 * Each strip owns an independent loop rather than sharing one across a rack
 * — simpler to reuse outside the rack, and the per-loop overhead is the same
 * canvas work either way; only the JS scheduling duplicates, which is cheap
 * next to 15 canvases' worth of pixel fills.
 */
export function useStripMeterLoop(
  source: () => MeterLevel[],
  muted: boolean,
  active: boolean,
): { register: (canvas: HTMLCanvasElement | null) => void; peakDb: number } {
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const meterState = React.useRef(createMeterState());
  const lastTime = React.useRef<number | null>(null);
  const lastFlush = React.useRef(0);
  const [peakDb, setPeakDb] = React.useState(-60);

  const sourceRef = React.useRef(source);
  sourceRef.current = source;
  const mutedRef = React.useRef(muted);
  mutedRef.current = muted;
  const activeRef = React.useRef(active);
  activeRef.current = active;

  React.useEffect(() => {
    let frame: number;
    let idle = 0;
    const draw = (time: number) => {
      const deltaSec = lastTime.current === null ? 0 : Math.min(0.25, (time - lastTime.current) / 1000);
      lastTime.current = time;
      let settled = true;
      let stripPeak = -60;

      const canvas = canvasRef.current;
      const levels = sourceRef.current();
      const isMuted = mutedRef.current;
      const bars = levels.map((level, channel) => {
        const key = `strip:${channel}`;
        // Peak-hold tracks the smoothed RMS bar, not the raw instantaneous
        // sample peak — see `createMeterState`. Feeding it `level.peak`
        // pinned every tick to the top of its bar.
        const eased = meterState.current.smoothLevel(key, isMuted ? 0 : level.rms, deltaSec);
        const currentDb = isMuted ? -60 : levelToDb(eased);
        const peakDbBar = meterState.current.updatePeak(key, currentDb, deltaSec);
        if (peakDbBar > currentDb + 0.05) settled = false;
        if (peakDbBar > stripPeak) stripPeak = peakDbBar;
        return { currentDb, peakDb: peakDbBar, clipped: !isMuted && level.clipped };
      });

      if (canvas) {
        const ratio = window.devicePixelRatio || 1;
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        if (width > 0 && height > 0) {
          if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
            canvas.width = Math.round(width * ratio);
            canvas.height = Math.round(height * ratio);
          }
          const ctx = canvas.getContext("2d");
          if (ctx) {
            ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
            ctx.clearRect(0, 0, width, height);
            drawStripMeterBars(ctx, height, bars, isMuted);
          }
        }
      }

      // The readout is text, so it updates at a readable ~10Hz rather than
      // every frame — a 60Hz number is unreadable, and it is React state, so
      // refreshing it per frame would re-render around the canvas the meter
      // itself exists to avoid re-rendering for.
      if (time - lastFlush.current > 100) {
        lastFlush.current = time;
        setPeakDb((current) => (Math.abs(current - stripPeak) > 0.05 ? stripPeak : current));
      }

      idle = activeRef.current || !settled ? 0 : idle + 1;
      if (idle > SETTLE_FRAMES) {
        frame = 0;
        return;
      }
      frame = window.requestAnimationFrame(draw);
    };
    frame = window.requestAnimationFrame(draw);
    return () => window.cancelAnimationFrame(frame);
  }, [active]);

  const register = React.useCallback((canvas: HTMLCanvasElement | null) => {
    canvasRef.current = canvas;
  }, []);

  return { register, peakDb };
}
