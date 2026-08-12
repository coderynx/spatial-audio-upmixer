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
    this.ended = false;

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

  handle(message) {
    if (!this.wasm) return;
    switch (message.type) {
      case "params":
        this.setParams(message.json);
        break;
      case "stem":
        this.addStem(message.left, message.right);
        break;
      case "rewind":
        if (this.engine) this.wasm.dsp_engine_rewind(this.engine);
        this.ended = false;
        break;
      case "dispose":
        this.dispose();
        break;
      default:
        break;
    }
  }

  setParams(json) {
    const encoded = new TextEncoder().encode(json);
    const ptr = this.wasm.dsp_alloc(encoded.length);
    new Uint8Array(this.wasm.memory.buffer, ptr, encoded.length).set(encoded);
    const engine = this.wasm.dsp_engine_new(sampleRate, ptr, encoded.length);
    this.wasm.dsp_free(ptr, encoded.length);

    if (!engine) {
      this.port.postMessage({ type: "error", message: "engine parameters rejected" });
      return;
    }
    if (this.engine) this.wasm.dsp_engine_free(this.engine);
    this.engine = engine;
    this.ended = false;
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

    if (written < frames && !this.ended) {
      this.ended = true;
      this.port.postMessage({ type: "ended" });
    }
    return true;
  }
}

registerProcessor("upmixer-dsp-processor", UpmixerDspProcessor);
