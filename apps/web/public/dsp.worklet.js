// Preview DSP processor: hosts the shared Rust core (packages/dsp) compiled
// to WebAssembly and renders the whole mastered speaker bed itself.
//
// This node is the *source*, not an insert — the decoded stems live in the
// wasm heap, so the engine can look ahead and run the same offline algorithms
// the export does rather than causal approximations of them. See
// docs/contracts/preview_export_parity.md.
//
// Deliberately dependency-free: an AudioWorkletGlobalScope has no module
// loader, so the WebAssembly.Module is compiled on the main thread and handed
// over through processorOptions, where instantiation is synchronous.

const RENDER_QUANTUM = 128;

// Measurement runs in two stages: a fast excerpt pass clears the "calibrating
// loudness" UI in a few seconds, then an exact whole-programme pass refines
// the gain in the background. See docs/contracts/preview_export_parity.md P3.
const FAST_EXCERPT_COUNT = 5;
const FAST_EXCERPT_SECONDS = 3;
const FAST_EXCERPT_PREROLL_SECONDS = 0.5;

// Frames of the fast excerpt pass to advance per quantum while paused. Sized
// well past one quantum's budget on purpose: the node is the *source* and its
// output is already zero-filled while paused, so an overrun here costs a
// dropped silent callback, not a glitch, and finishing the (short) excerpt
// plan in a handful of calls is what makes the banner clear in seconds.
const MEASURE_FRAMES_FAST_IDLE = 2048;

// Frames of the fast excerpt pass to advance per quantum while playing — kept
// far smaller than the idle slice, because unlike the idle case this shares
// the quantum with a real render: an overrun here drops real audio, not
// silence. Bench-measured worst-case headroom on the heaviest configuration
// (order-3 binaural decode, 9 stems) is what sets this value.
const MEASURE_FRAMES_FAST_PLAYING = 32;

// Frames of the exact whole-programme pass to advance per quantum while the
// transport is idle. Measuring the whole programme costs ~0.12x realtime, so
// it cannot happen in one call without starving the callback; it is spread
// across quanta instead, using the budget the render is not using.
//
// It advances only while paused. Playing already spends most of the quantum,
// and both the render and the measurement have periodic look-ahead strides
// that overrun it when they land together. A pass is kept, not dropped, when
// playback starts, so it resumes where it left off on the next pause.
const MEASURE_FRAMES_IDLE = 384;

// Frames of the route-scale pass to advance per quantum, idle and playing.
// It runs the routing chain of one stem at a time — no mastering, no decode —
// so it is cheaper per frame than a measurement pass, but it shares the same
// discipline: a generous slice while the output is silent anyway, a small one
// while a real render needs the quantum.
const SCALE_FRAMES_IDLE = 4096;
const SCALE_FRAMES_PLAYING = 384;

// The route-scale pass runs the two stages the loudness pass does: a handful
// of excerpts for an answer within a second or two, then the whole programme,
// which is the number the export normalizes by and the one that finally
// stands. The cost is per stem, not per programme: measured at ~90x realtime
// for one stem's routing, the excerpt plan below is a couple of seconds for a
// thirteen-stem bed while paused, and most of a minute while playing, when
// the slice has to share the quantum with a render.
const SCALE_EXCERPT_COUNT = 5;
const SCALE_EXCERPT_SECONDS = 3;
const SCALE_EXCERPT_PREROLL_SECONDS = 0.5;

class UpmixerDspProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const { module, channelCount } = options.processorOptions;
    this.channelCount = channelCount;
    this.engine = 0;
    this.outPtr = 0;
    this.outBytes = 0;
    this.meterPtr = 0;
    this.meterBytes = 0;
    this.spectrumPtr = 0;
    this.spectrumBytes = 0;
    this.ended = false;
    this.playing = false;
    this.loop = false;
    this.reportCountdown = 0;
    this.measurePass = 0;
    this.measureOut = 0;
    this.measureReport = 0;
    this.measureStage = null;
    this.measureWeights = [1];
    this.measureReported = false;
    this.scalePass = 0;
    this.scaleStage = null;

    try {
      this.instance = new WebAssembly.Instance(module);
      this.wasm = this.instance.exports;
    } catch (error) {
      this.port.postMessage({ type: "error", message: String(error) });
      return;
    }

    this.port.onmessage = (event) => this.handle(event.data);
    this.port.postMessage({ type: "ready", version: this.readVersion() });
  }

  readVersion() {
    const len = this.wasm.dsp_core_version_len();
    const ptr = this.wasm.dsp_core_version_ptr();
    const bytes = new Uint8Array(this.wasm.memory.buffer, ptr, len);
    return String.fromCharCode(...bytes);
  }

  // A wasm allocation may grow the heap, which detaches every existing view;
  // always take a fresh one at the point of use.
  heapF32(ptr, length) {
    return new Float32Array(this.wasm.memory.buffer, ptr, length);
  }

  copyIn(values) {
    const bytes = values.length * 4;
    const ptr = this.wasm.dsp_alloc(bytes);
    this.heapF32(ptr, values.length).set(values);
    return { ptr, bytes };
  }

  copyBytes(values) {
    const bytes = values.length;
    const ptr = this.wasm.dsp_alloc(bytes);
    new Uint8Array(this.wasm.memory.buffer, ptr, bytes).set(values);
    return { ptr, bytes };
  }

  heapF64(ptr, length) {
    return new Float64Array(this.wasm.memory.buffer, ptr, length);
  }

  copyF64(values) {
    const bytes = values.length * 8;
    const ptr = this.wasm.dsp_alloc(bytes);
    this.heapF64(ptr, values.length).set(values);
    return { ptr, bytes };
  }

  handle(message) {
    if (!this.wasm) return;
    switch (message.type) {
      case "params":
        this.setParams(message.bytes);
        break;
      case "stem":
        this.addStem(message.left, message.right);
        break;
      case "decodeTaps":
        this.setDecodeTaps(message.taps);
        break;
      case "xtcTaps":
        this.setXtcTaps(message.taps);
        break;
      case "rewind":
        if (this.engine) this.wasm.dsp_engine_rewind(this.engine);
        this.ended = false;
        break;
      case "update":
        this.updateParams(message.bytes);
        break;
      case "transport":
        if (message.playing !== undefined) this.playing = Boolean(message.playing);
        if (message.loop !== undefined) this.loop = Boolean(message.loop);
        break;
      case "seek":
        if (this.engine) {
          this.wasm.dsp_engine_seek(this.engine, message.frame >>> 0);
          this.ended = false;
          // The seek warms filter state with a real, audible preroll render
          // so playback resumes cleanly (see `PreviewEngine::seek`), which
          // leaves the engine's meters non-zero even while paused. Report
          // only while playing — otherwise the meters/haze would flash with
          // levels from audio that isn't actually being heard.
          if (this.playing) this.report();
        }
        break;
      case "measure":
        this.measure(message.weights || []);
        break;
      case "dispose":
        this.dispose();
        break;
      default:
        break;
    }
  }

  setParams(encoded) {
    // A pass in flight measures a forked copy of the engine being replaced.
    this.endMeasure();
    this.endScale();
    const { ptr, bytes } = this.copyBytes(encoded);
    const engine = this.wasm.dsp_engine_new(sampleRate, ptr, bytes);
    this.wasm.dsp_free(ptr, bytes);

    if (!engine) {
      this.port.postMessage({ type: "error", message: "engine parameters rejected" });
      return;
    }
    if (this.engine) this.wasm.dsp_engine_free(this.engine);
    this.engine = engine;
    this.ended = false;
  }

  // Replacing the parameter block keeps the loaded stems and the playhead,
  // so mute, solo, rebalance, routing, mastering and output-mode changes all
  // take effect without a reload.
  updateParams(encoded) {
    if (!this.engine) return;
    // Deliberately does not `endMeasure()`: a pass in flight forked its own
    // engine (see `PreviewEngine::fork`) and keeps measuring it against the
    // parameters at the moment `measure()` started. `setParams`/`dispose`
    // can call `endMeasure()` freely because they also tell the main thread
    // the pass ended (a fresh engine or a torn-down one); this path, fired
    // on every mix edit, has no such signal — freeing the pass here would
    // leave `DspEngineClient.measure()`'s promise on the main thread waiting
    // for a "measured" message that would never arrive.
    const { ptr, bytes } = this.copyBytes(encoded);
    const ok = this.wasm.dsp_engine_set_params(this.engine, ptr, bytes);
    this.wasm.dsp_free(ptr, bytes);
    if (!ok) {
      this.port.postMessage({ type: "error", message: "engine parameters rejected" });
      return;
    }
    // A route-scale pass in flight forked the parameters it started on, so a
    // mix edit invalidates it. Unlike the loudness pass it owes the main
    // thread nothing, so it can simply be dropped and started again: the
    // engine asks for a new one whenever the edit touched the routing.
    this.endScale();
    this.channelCount = this.wasm.dsp_engine_output_channels(this.engine) || this.channelCount;
  }

  addStem(left, right) {
    if (!this.engine) return;
    const l = this.copyIn(left);
    const r = this.copyIn(right);
    this.wasm.dsp_engine_add_stem(this.engine, l.ptr, r.ptr, left.length);
    this.wasm.dsp_free(l.ptr, l.bytes);
    this.wasm.dsp_free(r.ptr, r.bytes);
    this.port.postMessage({
      type: "loaded",
      totalFrames: this.wasm.dsp_engine_total_frames(this.engine),
    });
  }

  // Taps arrive as raw f64 bytes rather than riding along in the JSON
  // parameter block — the decode bank alone is 16 ACN x 2 ears x several
  // thousand taps, and re-encoding/re-parsing that as float text on every
  // mix edit is most of what made loading slow. The engine keeps whatever
  // was set here across every later `updateParams` call.
  setDecodeTaps(taps) {
    if (!this.engine) return;
    const { ptr, bytes } = this.copyF64(taps);
    this.wasm.dsp_engine_set_decode_taps(this.engine, ptr, taps.length);
    this.wasm.dsp_free(ptr, bytes);
  }

  setXtcTaps(taps) {
    if (!this.engine) return;
    const { ptr, bytes } = this.copyF64(taps);
    this.wasm.dsp_engine_set_xtc_taps(this.engine, ptr, taps.length);
    this.wasm.dsp_free(ptr, bytes);
  }

  dispose() {
    this.endMeasure();
    this.endScale();
    if (this.engine) {
      this.wasm.dsp_engine_free(this.engine);
      this.engine = 0;
    }
    if (this.outPtr) {
      this.wasm.dsp_free(this.outPtr, this.outBytes);
      this.outPtr = 0;
    }
    if (this.meterPtr) {
      this.wasm.dsp_free(this.meterPtr, this.meterBytes);
      this.meterPtr = 0;
    }
    if (this.spectrumPtr) {
      this.wasm.dsp_free(this.spectrumPtr, this.spectrumBytes);
      this.spectrumPtr = 0;
    }
  }

  // Start measuring for real BS.1770 loudness and true peak — the correction
  // gain a bounce would need, rather than an estimate from a few seconds of
  // it. Runs in two stages: a fast pass over a handful of excerpts resolves
  // in seconds and is what the "calibrating loudness" UI waits for; an exact
  // whole-programme pass then starts automatically and refines the gain once
  // it lands. Both passes run on their own engine over the same stems,
  // advanced from `process` so the transport keeps its playhead and the
  // callback keeps its deadline.
  measure(weights) {
    if (!this.engine) return;
    this.measureWeights = weights.length ? weights : [1];
    this.measureReported = false;
    this.beginFastMeasure();
  }

  beginFastMeasure() {
    this.endMeasure();
    const excerptFrames = Math.round(FAST_EXCERPT_SECONDS * sampleRate);
    const prerollFrames = Math.round(FAST_EXCERPT_PREROLL_SECONDS * sampleRate);
    this.beginMeasurePass("fast", (weightPtr, weightCount) =>
      this.wasm.dsp_measure_begin_excerpts(
        this.engine,
        weightPtr,
        weightCount,
        FAST_EXCERPT_COUNT,
        excerptFrames,
        prerollFrames,
      ),
    );
  }

  beginMeasurePass(stage, begin) {
    const weights = this.measureWeights;
    const count = Math.max(weights.length, 1);
    const weightBytes = count * 8;
    const weightPtr = this.wasm.dsp_alloc(weightBytes);
    new Float64Array(this.wasm.memory.buffer, weightPtr, count).set(weights);
    this.measurePass = begin(weightPtr, weights.length);
    this.wasm.dsp_free(weightPtr, weightBytes);
    if (!this.measurePass) {
      this.port.postMessage({ type: "error", message: "measurement could not start" });
      return;
    }
    this.measureStage = stage;
    this.measureOut = this.wasm.dsp_alloc(16);
    this.measureReport = 0;
    this.port.postMessage({ type: "measuring", stage, progress: 0 });
  }

  // Measure every stem's route normalization, then hand it to the engine.
  //
  // The host serves an estimate built from the routing weights, which cannot
  // see how much of a stem a band-limited surround or height send actually
  // carries. The engine measures the real thing off the signals it routes and
  // renders on that instead.
  advanceScale() {
    if (!this.scalePass) {
      if (!this.wasm.dsp_engine_wants_route_scale(this.engine)) return;
      this.beginScale("fast");
      if (!this.scalePass) return;
    }
    const step = this.playing ? SCALE_FRAMES_PLAYING : SCALE_FRAMES_IDLE;
    const stage = this.scaleStage;
    if (!this.wasm.dsp_scale_advance(this.scalePass, this.engine, step)) return;
    this.endScale();
    // The engine renders on the excerpt answer from here; refine it against
    // the whole programme, which is what the export normalizes by.
    if (stage === "fast") {
      this.beginScale("exact");
    }
    // Every stem just moved by up to several dB, so a loudness correction
    // measured before this one landed is measuring a different programme.
    this.remeasure();
  }

  // Re-run the loudness pass against the levels the engine now renders at.
  // Only when one is already in flight or has already reported: the host
  // decides whether a programme is measured at all, and a pass in flight
  // forked before the scales landed, so it is as stale as a finished one.
  remeasure() {
    if (!this.measurePass && !this.measureReported) return;
    this.beginFastMeasure();
  }

  beginScale(stage) {
    this.scalePass =
      stage === "fast"
        ? this.wasm.dsp_scale_begin_excerpts(
            this.engine,
            SCALE_EXCERPT_COUNT,
            Math.round(SCALE_EXCERPT_SECONDS * sampleRate),
            Math.round(SCALE_EXCERPT_PREROLL_SECONDS * sampleRate),
          )
        : this.wasm.dsp_scale_begin(this.engine);
    this.scaleStage = this.scalePass ? stage : null;
  }

  endScale() {
    if (this.scalePass) {
      this.wasm.dsp_scale_free(this.scalePass);
      this.scalePass = 0;
    }
    this.scaleStage = null;
  }

  advanceMeasure() {
    this.advanceScale();
    if (!this.measurePass) return;
    // The exact pass only advances while paused; the fast pass also advances
    // during playback, in a small slice, so pressing play right away doesn't
    // stall the banner.
    if (this.playing && this.measureStage !== "fast") return;
    const step = this.playing
      ? MEASURE_FRAMES_FAST_PLAYING
      : this.measureStage === "fast"
        ? MEASURE_FRAMES_FAST_IDLE
        : MEASURE_FRAMES_IDLE;
    const done = this.wasm.dsp_measure_advance(this.measurePass, step, this.measureOut);
    if (!done) {
      this.measureReport -= 1;
      if (this.measureReport <= 0) {
        this.measureReport = 64;
        this.port.postMessage({
          type: "measuring",
          stage: this.measureStage,
          progress: this.wasm.dsp_measure_progress(this.measurePass),
        });
      }
      return;
    }
    const result = new Float64Array(this.wasm.memory.buffer, this.measureOut, 2);
    const [lkfs, dbtp] = [result[0], result[1]];
    const stage = this.measureStage;
    this.endMeasure();
    // The host awaits exactly one fast result, the one that clears its
    // banner; a fast pass the route-scale measurement forced a re-run of
    // arrives after that promise is settled, and travels the refinement
    // channel the exact pass uses instead.
    const reported = stage === "fast" && this.measureReported ? "exact" : stage;
    this.measureReported = true;
    this.port.postMessage({ type: "measured", stage: reported, lkfs, dbtp });
    if (stage === "fast") {
      this.beginMeasurePass("exact", (weightPtr, weightCount) =>
        this.wasm.dsp_measure_begin(this.engine, weightPtr, weightCount),
      );
    }
  }

  endMeasure() {
    if (this.measurePass) {
      this.wasm.dsp_measure_free(this.measurePass);
      this.measurePass = 0;
    }
    if (this.measureOut) {
      this.wasm.dsp_free(this.measureOut, 16);
      this.measureOut = 0;
    }
    this.measureStage = null;
  }

  report() {
    if (!this.engine) return;
    const capacity = 256;
    if (!this.meterPtr) {
      this.meterBytes = capacity * 4;
      this.meterPtr = this.wasm.dsp_alloc(this.meterBytes);
    }
    if (!this.spectrumPtr) {
      this.spectrumBytes = capacity * 4;
      this.spectrumPtr = this.wasm.dsp_alloc(this.spectrumBytes);
    }
    const written = this.wasm.dsp_engine_meters(this.engine, this.meterPtr, capacity);
    // The spectrum's FFT is too heavy for every quantum; it only runs here,
    // at report cadence (~30Hz) — see `PreviewEngine::stem_spectrum`.
    const spectrumWritten = this.wasm.dsp_engine_stem_spectrum(this.engine, this.spectrumPtr, capacity);
    this.port.postMessage({
      type: "frame",
      position: this.wasm.dsp_engine_position(this.engine),
      meters: Array.from(this.heapF32(this.meterPtr, written)),
      spectrum: Array.from(this.heapF32(this.spectrumPtr, spectrumWritten)),
    });
  }

  ensureOutput(frames) {
    const needed = this.channelCount * frames * 4;
    if (this.outBytes >= needed) return;
    if (this.outPtr) this.wasm.dsp_free(this.outPtr, this.outBytes);
    this.outPtr = this.wasm.dsp_alloc(needed);
    this.outBytes = needed;
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    if (!this.wasm || !this.engine || !output || output.length === 0) {
      return true;
    }
    const frames = output[0].length || RENDER_QUANTUM;
    if (!this.playing) {
      for (const channel of output) channel.fill(0);
      this.advanceMeasure();
      return true;
    }
    this.ensureOutput(frames);

    const written = this.wasm.dsp_engine_render(
      this.engine,
      this.outPtr,
      this.channelCount,
      frames,
    );
    const rendered = this.heapF32(this.outPtr, this.channelCount * frames);
    for (let channel = 0; channel < output.length; channel += 1) {
      const target = output[channel];
      if (channel < this.channelCount) {
        target.set(rendered.subarray(channel * frames, channel * frames + frames));
      } else {
        target.fill(0);
      }
    }

    if (written < frames) {
      if (this.loop) {
        this.wasm.dsp_engine_rewind(this.engine);
      } else if (!this.ended) {
        this.ended = true;
        this.playing = false;
        this.port.postMessage({ type: "ended" });
      }
    }

    // ~30 Hz is enough for a meter and a playhead; posting every quantum
    // would flood the main thread with 375 messages a second.
    this.reportCountdown -= frames;
    if (this.reportCountdown <= 0) {
      this.reportCountdown = sampleRate / 30;
      this.report();
    }
    this.advanceMeasure();
    return true;
  }
}

registerProcessor("upmixer-dsp-processor", UpmixerDspProcessor);
