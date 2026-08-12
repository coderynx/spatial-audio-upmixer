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

// Frames of the measurement pass to advance per quantum while the transport
// is idle. Measuring the whole programme costs ~0.12x realtime, so it cannot
// happen in one call without starving the callback; it is spread across quanta
// instead, using the budget the render is not using.
//
// It advances only while paused. Playing already spends most of the quantum,
// and both the render and the measurement have periodic look-ahead strides
// that overrun it when they land together. A pass is kept, not dropped, when
// playback starts, so it resumes where it left off on the next pause.
const MEASURE_FRAMES_IDLE = 384;

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
    this.ended = false;
    this.playing = false;
    this.loop = false;
    this.reportCountdown = 0;
    this.measurePass = 0;
    this.measureOut = 0;
    this.measureReport = 0;

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
          this.report();
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
    const { ptr, bytes } = this.copyBytes(encoded);
    const ok = this.wasm.dsp_engine_set_params(this.engine, ptr, bytes);
    this.wasm.dsp_free(ptr, bytes);
    if (!ok) {
      this.port.postMessage({ type: "error", message: "engine parameters rejected" });
      return;
    }
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
  }

  // Start measuring the whole programme for real BS.1770 loudness and true
  // peak — the correction gain a bounce would need, rather than an estimate
  // from a few seconds of it. The pass runs on its own engine over the same
  // stems, advanced from `process` so the transport keeps its playhead and the
  // callback keeps its deadline.
  measure(weights) {
    if (!this.engine) return;
    this.endMeasure();
    const count = Math.max(weights.length, 1);
    const weightBytes = count * 8;
    const weightPtr = this.wasm.dsp_alloc(weightBytes);
    new Float64Array(this.wasm.memory.buffer, weightPtr, count).set(
      weights.length ? weights : [1],
    );
    this.measurePass = this.wasm.dsp_measure_begin(this.engine, weightPtr, weights.length);
    this.wasm.dsp_free(weightPtr, weightBytes);
    if (!this.measurePass) {
      this.port.postMessage({ type: "error", message: "measurement could not start" });
      return;
    }
    this.measureOut = this.wasm.dsp_alloc(16);
    this.port.postMessage({ type: "measuring", progress: 0 });
  }

  advanceMeasure() {
    if (!this.measurePass || this.playing) return;
    const done = this.wasm.dsp_measure_advance(
      this.measurePass,
      MEASURE_FRAMES_IDLE,
      this.measureOut,
    );
    if (!done) {
      this.measureReport = (this.measureReport || 0) - 1;
      if (this.measureReport <= 0) {
        this.measureReport = 64;
        this.port.postMessage({
          type: "measuring",
          progress: this.wasm.dsp_measure_progress(this.measurePass),
        });
      }
      return;
    }
    const result = new Float64Array(this.wasm.memory.buffer, this.measureOut, 2);
    const [lkfs, dbtp] = [result[0], result[1]];
    this.endMeasure();
    this.port.postMessage({ type: "measured", lkfs, dbtp });
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
  }

  report() {
    if (!this.engine) return;
    const capacity = 256;
    if (!this.meterPtr) {
      this.meterBytes = capacity * 4;
      this.meterPtr = this.wasm.dsp_alloc(this.meterBytes);
    }
    const written = this.wasm.dsp_engine_meters(this.engine, this.meterPtr, capacity);
    this.port.postMessage({
      type: "frame",
      position: this.wasm.dsp_engine_position(this.engine),
      meters: Array.from(this.heapF32(this.meterPtr, written)),
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
    return true;
  }
}

registerProcessor("upmixer-dsp-processor", UpmixerDspProcessor);
