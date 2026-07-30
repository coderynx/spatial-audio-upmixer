// Shared 4x-oversample windowed-sinc true-peak detection kernel, used by
// every AudioWorkletProcessor in this app that needs one (limiter.worklet.js,
// loudness.worklet.js). AudioWorklet modules are loaded as ES modules (the
// same fetch/parse algorithm as `<script type="module">`), so both files
// `import` this one instead of each hand-duplicating it — this is the single
// worklet-side copy; `src/features/projects/masteringProfiles.ts`'s
// `buildTruePeakKernel()` is the canonical TS copy the worklets can't import
// directly (they run in their own global scope with no access to the app's
// bundler-resolved module graph, only to sibling static files under
// public/). `limiterWorklet.test.ts` pins this file's values against that
// canonical copy bit-for-bit, so an edit to either without the other fails
// the suite instead of silently drifting.

export const TAPS = 32;
export const OVERSAMPLE = 4;

export function buildKernel() {
  const kernel = new Float64Array(TAPS);
  const center = (TAPS - 1) / 2;
  for (let i = 0; i < TAPS; i++) {
    const t = i - center;
    const sinc = t === 0 ? 1 : Math.sin((Math.PI * t) / 4) / ((Math.PI * t) / 4);
    const window = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (TAPS - 1)); // Hann
    kernel[i] = sinc * window;
  }
  return kernel;
}

export const KERNEL = buildKernel();
