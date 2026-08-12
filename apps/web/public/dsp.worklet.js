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

  handle(message) {
    if (!this.wasm) return;
    switch (message.type) {
      case "params":
        this.setParams(message.bytes);
        break;
      case "stem":
        this.addStem(message.left, message.right);
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

  dispose() {
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

  // Renders the whole programme offline to get real BS.1770 loudness and
  // true peak, then rewinds — the correction gain a bounce would need,
  // rather than an estimate from a few seconds of it.
  measure(weights) {
    if (!this.engine) return;
    const weightBytes = Math.max(weights.length, 1) * 8;
    const weightPtr = this.wasm.dsp_alloc(weightBytes);
    new Float64Array(this.wasm.memory.buffer, weightPtr, Math.max(weights.length, 1)).set(
      weights.length ? weights : [1],
    );
    const outPtr = this.wasm.dsp_alloc(16);
    this.wasm.dsp_engine_measure(this.engine, weightPtr, weights.length, outPtr);
    const result = new Float64Array(this.wasm.memory.buffer, outPtr, 2);
    const [lkfs, dbtp] = [result[0], result[1]];
    this.wasm.dsp_free(weightPtr, weightBytes);
    this.wasm.dsp_free(outPtr, 16);
    this.port.postMessage({ type: "measured", lkfs, dbtp });
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
