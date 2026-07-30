// AudioWorkletProcessor mirroring upmixer/mastering/limiter.py's
// LookAheadLimiter for the web preview's native (discrete multichannel bed)
// monitoring path — see docs/contracts/preview_export_parity.md's "Look-
// ahead limiter" row. This is the Tier-2 preview counterpart to the
// backend's offline LookAheadLimiter: same target parameters (ceiling,
// look-ahead window, release time) and the same high-level algorithm
// (linked oversampled true-peak detection -> combined look-ahead +
// FIR-kernel-dilation window minimum -> release-smoothed gain -> apply),
// but realized as a genuinely causal, real-time processor via a monotonic-
// deque sliding-window minimum (standard O(1)-amortized technique) instead
// of `scipy.ndimage.minimum_filter1d` over a whole in-memory buffer.
//
// Unlike the offline backend (which has the whole file available upfront
// and therefore needs no output latency — see limiter.py's module
// docstring), a live AudioWorkletProcessor only ever sees samples as they
// arrive, so realizing "look-ahead" here requires an actual delay line:
// this processor introduces `latencySamples` (posted back via a `"ready"`
// message once computed) of constant output latency while active. That is
// normal and expected for any real-time look-ahead limiter — hardware and
// software mastering limiters alike pay this same cost.
//
// Detection reuses the same 32-tap Hann-windowed-sinc 4x oversample kernel
// as masteringProfiles.ts's `buildTruePeakKernel` (that function's own
// comment documents this as the one shared true-peak approximation for the
// whole preview, Tier-3 bounded by the parity contract's 1.0 dBTP
// tolerance) rather than inventing a third approximation — imported from
// truePeakKernel.js, the one worklet-side copy also used by
// loudness.worklet.js, rather than each worklet hand-duplicating it.
//
// Linked (cross-channel) detection: every input channel's oversampled
// envelope is combined with a running max, so one shared gain curve is
// applied to every channel — matching the backend's linked design.
// Verified against a whole-buffer reference implementation (chunked
// 128-sample processing vs single-shot) on synthetic impulse/near-Nyquist-
// tone/dense-noise test signals during development; see the PR this
// shipped in for that validation.

import { KERNEL, OVERSAMPLE, TAPS } from "./truePeakKernel.js";

const SAFETY_MARGIN_DB = 0.1;
const KERNEL_DELAY = Math.floor(TAPS / 2);

/** Monotonic-deque sliding-window minimum: O(1) amortized per push, holding
 * the minimum value pushed within the trailing `window` indices. Standard
 * technique for causal streaming look-ahead/hold windows — the streaming
 * equivalent of `limiter.py`'s single `scipy.ndimage.minimum_filter1d` call
 * over a whole in-memory buffer. */
class SlidingMin {
  constructor(window) {
    this.window = window;
    this.cap = window + 1;
    this.idxBuf = new Float64Array(this.cap);
    this.valBuf = new Float64Array(this.cap);
    this.head = 0;
    this.tail = 0;
  }
  push(index, value) {
    while (this.tail !== this.head) {
      const lastIdx = (this.tail - 1 + this.cap) % this.cap;
      if (this.valBuf[lastIdx] >= value) this.tail = lastIdx;
      else break;
    }
    this.valBuf[this.tail] = value;
    this.idxBuf[this.tail] = index;
    this.tail = (this.tail + 1) % this.cap;
    while (this.idxBuf[this.head] <= index - this.window) this.head = (this.head + 1) % this.cap;
  }
  min() {
    return this.head === this.tail ? 1.0 : this.valBuf[this.head];
  }
}

class LimiterProcessor extends AudioWorkletProcessor {
  static get parameterDescriptors() {
    return [];
  }

  constructor(options) {
    super(options);
    const opts = (options && options.processorOptions) || {};
    const ceilingDb = opts.ceilingDb ?? -1.0;
    const lookaheadMs = opts.lookaheadMs ?? 5.0;
    const releaseMs = opts.releaseMs ?? 50.0;
    this._numberOfChannels = Math.max(1, opts.numberOfChannels ?? 2);

    const overSr = sampleRate * OVERSAMPLE;
    const lookaheadSamples = Math.max(1, Math.round((lookaheadMs / 1000) * overSr));
    // Half-width, in oversampled samples, of the detector kernel's own
    // support -- mirrors upmixer/mastering/limiter.py's _FIR_MARGIN_SAMPLES.
    // Dilating the protected window by this much prevents the same
    // gain-modulation edge effect documented there: a heavily-reduced
    // sample sitting next to an unreduced neighbour can recombine, under
    // interpolation, into a fresh inter-sample peak the per-block analysis
    // didn't foresee.
    const dilateHalfBase = Math.ceil(KERNEL_DELAY / OVERSAMPLE);
    const dilateHalfOver = dilateHalfBase * OVERSAMPLE;
    this._windowTotal = lookaheadSamples + 2 * dilateHalfOver;
    // Oversampled-domain distance between "just finished detecting" and
    // "the output position this detection window fully covers" -- see the
    // derivation in this file's development notes: a value p is only
    // finalized once the sliding window has seen everything in
    // [p - dilateHalfOver, p + lookaheadSamples + dilateHalfOver - 1].
    this._offset = lookaheadSamples + dilateHalfOver - 1;

    this._ceilingLinear = Math.pow(10, (ceilingDb - SAFETY_MARGIN_DB) / 20);
    this._alphaRelease = 1 - Math.exp(-1 / ((releaseMs / 1000) * overSr));
    this._slowNeedDb = 0;

    this._slidingMin = new SlidingMin(this._windowTotal);

    // Per-channel zero-stuffed history ring (length TAPS) for the causal
    // direct-form FIR convolution driving detection.
    this._zeroStuffRing = [];
    for (let c = 0; c < this._numberOfChannels; c++) this._zeroStuffRing.push(new Float64Array(TAPS));
    this._ringWritePos = 0;

    // Delay line for the audio itself (base-rate) -- holds each incoming
    // sample until its finalized gain is available. See the module
    // docstring on why a real delay is unavoidable here (unlike the
    // offline backend, which has the whole file upfront).
    this._delayCapacity = Math.ceil((this._offset + KERNEL_DELAY) / OVERSAMPLE) + 8;
    this._delayBuf = [];
    for (let c = 0; c < this._numberOfChannels; c++) this._delayBuf.push(new Float64Array(this._delayCapacity));
    this._gainRing = new Float64Array(this._delayCapacity).fill(1.0);
    this._delayWritePos = 0;

    this._baseCounter = 0;
    this._oversampleCounter = 0;
    this._pendingMin = Infinity;
    this._pendingCount = 0;

    this.port.postMessage({ type: "ready", latencySamples: this._delayCapacity - 1 });
  }

  process(inputs, outputs) {
    const input = inputs[0];
    const output = outputs[0];
    const nCh = this._numberOfChannels;
    const blockLen = (output[0] && output[0].length) || 128;

    for (let i = 0; i < blockLen; i++) {
      for (let c = 0; c < nCh; c++) {
        const sample = input[c] && i < input[c].length ? input[c][i] : 0;
        this._delayBuf[c][this._delayWritePos] = sample;
      }

      // 4 oversampled detection steps for this one base-rate sample.
      for (let phase = 0; phase < OVERSAMPLE; phase++) {
        let envelope = 0;
        for (let c = 0; c < nCh; c++) {
          const ring = this._zeroStuffRing[c];
          const sample = input[c] && i < input[c].length ? input[c][i] : 0;
          ring[this._ringWritePos] = phase === 0 ? sample : 0;
        }
        for (let c = 0; c < nCh; c++) {
          const ring = this._zeroStuffRing[c];
          let acc = 0;
          for (let t = 0; t < TAPS; t++) {
            const idx = ((this._ringWritePos - t) % TAPS + TAPS) % TAPS;
            acc += ring[idx] * KERNEL[t];
          }
          const mag = Math.abs(acc);
          if (mag > envelope) envelope = mag;
        }
        this._ringWritePos = (this._ringWritePos + 1) % TAPS;

        const gainInst = Math.min(1.0, this._ceilingLinear / Math.max(envelope, 1e-12));
        const detectionIndex = this._oversampleCounter - KERNEL_DELAY;
        if (detectionIndex >= 0) this._slidingMin.push(detectionIndex, gainInst);

        const targetIndex = detectionIndex - this._offset;
        if (targetIndex >= 0) {
          const gainLookahead = detectionIndex >= 0 ? this._slidingMin.min() : 1.0;
          const needDb = -20 * Math.log10(Math.max(gainLookahead, 1e-12));
          this._slowNeedDb += this._alphaRelease * (needDb - this._slowNeedDb);
          const needDbSmoothed = Math.max(needDb, this._slowNeedDb);
          const g = Math.pow(10, -needDbSmoothed / 20);
          if (g < this._pendingMin) this._pendingMin = g;
          this._pendingCount++;
          if (this._pendingCount === OVERSAMPLE) {
            // Decimate via block-minimum (not average), matching
            // limiter.py, so the oversampled-rate ceiling guarantee is
            // never loosened by decimation.
            const finalizedBaseIndex = Math.floor(targetIndex / OVERSAMPLE);
            const slot = ((finalizedBaseIndex % this._delayCapacity) + this._delayCapacity) % this._delayCapacity;
            this._gainRing[slot] = this._pendingMin;
            this._pendingMin = Infinity;
            this._pendingCount = 0;
          }
        }
        this._oversampleCounter++;
      }

      // Read back the delayed sample (written this same iteration, so the
      // oldest slot about to be overwritten is exactly one step ahead of
      // the current write position) and its finalized gain.
      const readPos = (this._delayWritePos + 1) % this._delayCapacity;
      const readBaseIndex = this._baseCounter - (this._delayCapacity - 1);
      let gain = 0;
      if (readBaseIndex >= 0) {
        const slot = ((readBaseIndex % this._delayCapacity) + this._delayCapacity) % this._delayCapacity;
        gain = this._gainRing[slot];
      }
      for (let c = 0; c < nCh; c++) {
        if (output[c]) output[c][i] = readBaseIndex >= 0 ? this._delayBuf[c][readPos] * gain : 0;
      }

      this._delayWritePos = (this._delayWritePos + 1) % this._delayCapacity;
      this._baseCounter++;
    }
    return true;
  }
}

registerProcessor("limiter-processor", LimiterProcessor);

// Re-exported (from the `import` above) for `limiterWorklet.test.ts`'s drift
// guard only — harmless in the real worklet, `registerProcessor` above
// doesn't care what else this module exports.
export { KERNEL, TAPS, OVERSAMPLE };
