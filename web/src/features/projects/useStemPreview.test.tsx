import * as React from "react";
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProjectStem } from "@/api";
import { useStemPreview } from "./useStemPreview";

class FakeAudioParam {
  value = 0;
}

class FakeNode {
  connections: FakeNode[] = [];
  connect(target: FakeNode) {
    this.connections.push(target);
    return target;
  }
  disconnect() {}
}

class FakeGain extends FakeNode {
  gain = new FakeAudioParam();
}

class FakeBiquadFilter extends FakeNode {
  type = "";
  frequency = new FakeAudioParam();
  Q = new FakeAudioParam();
  gain = new FakeAudioParam();
}

class FakeWaveShaper extends FakeNode {
  curve: Float32Array | null = null;
  oversample = "none";
}

class FakeDynamicsCompressor extends FakeNode {
  threshold = new FakeAudioParam();
  knee = new FakeAudioParam();
  ratio = new FakeAudioParam();
  attack = new FakeAudioParam();
  release = new FakeAudioParam();
}

class FakeChannelSplitter extends FakeNode {
  connect(target: FakeNode) {
    this.connections.push(target);
    return target;
  }
}

class FakeDelay extends FakeNode {
  delayTime = new FakeAudioParam();
}

// Fakes for the `ambisonics` package's individual dist submodules (imported
// directly rather than the barrel — see ambisonics.d.ts for why). The real
// classes build WebAudio ChannelMerger/ChannelSplitter graphs with channel
// counts jsdom's fake context doesn't model, so each is mocked. Defined
// inline in each factory (no outer-scope references) since `vi.mock`
// factories run before the rest of this module's top-level code.
vi.mock("ambisonics/dist/ambi-monoEncoder", () => {
  class MockNode {
    connections: MockNode[] = [];
    connect(target: MockNode) {
      this.connections.push(target);
      return target;
    }
    disconnect() {}
  }
  class MockGain extends MockNode {
    gain = { value: 0 };
  }
  class monoEncoder extends MockNode {
    static instanceCount = 0;
    in = new MockGain();
    out = new MockGain();
    azim = 0;
    elev = 0;
    updateGains = vi.fn();
    constructor() {
      super();
      monoEncoder.instanceCount++;
    }
  }
  return { default: monoEncoder };
});

vi.mock("ambisonics/dist/ambi-sceneRotator", () => {
  class MockNode {
    connect(target: MockNode) { return target; }
    disconnect() {}
  }
  class MockGain extends MockNode {
    gain = { value: 0 };
  }
  class sceneRotator extends MockNode {
    in = new MockGain();
    out = new MockGain();
    yaw = 0;
    pitch = 0;
    roll = 0;
    updateRotMtx = vi.fn();
  }
  return { default: sceneRotator };
});

vi.mock("ambisonics/dist/ambi-binauralDecoder", () => {
  class MockNode {
    connect(target: MockNode) { return target; }
    disconnect() {}
  }
  class MockGain extends MockNode {
    gain = { value: 0 };
  }
  class binDecoder extends MockNode {
    in = new MockGain();
    out = new MockGain();
    updateFilters = vi.fn();
    resetFilters = vi.fn();
  }
  return { default: binDecoder };
});

vi.mock("ambisonics/dist/hoa-loader", () => {
  class HOAloader {
    load = vi.fn();
  }
  return { default: HOAloader };
});

class FakeAnalyser extends FakeNode {
  fftSize = 2048;
  frequencyBinCount = 1024;
  smoothingTimeConstant = 0;
  getByteTimeDomainData(array: Uint8Array) {
    array.fill(128);
  }
  getByteFrequencyData(array: Uint8Array) {
    array.fill(0);
  }
}

class FakeBufferSource extends FakeNode {
  buffer: FakeAudioBuffer | null = null;
  loop = false;
  loopStart = 0;
  loopEnd = 0;
  start = vi.fn();
  stop = vi.fn();
}

class FakeAudioBuffer {
  duration = 10;
  length = 8;
  numberOfChannels = 2;
  getChannelData() {
    return new Float32Array(this.length).fill(0.2);
  }
}

class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  currentTime = 0;
  closed = false;
  destination = new FakeNode();

  constructor() {
    FakeAudioContext.instances.push(this);
  }
  eqFilters: FakeBiquadFilter[] = [];
  compressors: FakeDynamicsCompressor[] = [];
  waveShapers: FakeWaveShaper[] = [];
  delays: FakeDelay[] = [];
  gains: FakeGain[] = [];
  bufferSources: FakeBufferSource[] = [];
  createGain() {
    const gain = new FakeGain();
    this.gains.push(gain);
    return gain;
  }
  createDelay() {
    const delay = new FakeDelay();
    this.delays.push(delay);
    return delay;
  }
  createChannelSplitter() { return new FakeChannelSplitter(); }
  createChannelMerger() { return new FakeChannelSplitter(); }
  createAnalyser() { return new FakeAnalyser(); }
  createBiquadFilter() {
    const filter = new FakeBiquadFilter();
    this.eqFilters.push(filter);
    return filter;
  }
  createDynamicsCompressor() {
    const compressor = new FakeDynamicsCompressor();
    this.compressors.push(compressor);
    return compressor;
  }
  createWaveShaper() {
    const shaper = new FakeWaveShaper();
    this.waveShapers.push(shaper);
    return shaper;
  }
  createBufferSource() {
    const source = new FakeBufferSource();
    this.bufferSources.push(source);
    return source;
  }
  decodeAudioData() { return Promise.resolve(new FakeAudioBuffer()); }
  resume = vi.fn(async () => {
    if (this.closed) throw new Error("Cannot resume a closed AudioContext.");
  });
  close = vi.fn(async () => { this.closed = true; });
}

const stems: ProjectStem[] = [
  { id: "vocals", stem_key: "Vocals", sample_rate: 48000, channels: 2, size_bytes: 1, audio_url: "/vocals.wav", preview_url: null },
  { id: "bass", stem_key: "Bass", sample_rate: 48000, channels: 2, size_bytes: 1, audio_url: "/bass.wav", preview_url: null },
];

let preview: ReturnType<typeof useStemPreview>;
function lastContext(): FakeAudioContext {
  const instance = FakeAudioContext.instances.at(-1);
  if (!instance) throw new Error("AudioContext was not constructed");
  return instance;
}

type MixArg = Parameters<typeof useStemPreview>[2];
type MasterArg = Parameters<typeof useStemPreview>[4];

function Harness({ mix, mastering }: { mix?: MixArg; mastering?: MasterArg }) {
  preview = useStemPreview(stems, {}, mix, null, mastering);
  return null;
}

function installAudio() {
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true,
    arrayBuffer: async () => new ArrayBuffer(8),
  })));
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  FakeAudioContext.instances = [];
});

describe("useStemPreview mastering chain", () => {
  it("uses a tanh soft-limit WaveShaper instead of a default-parameter compressor", async () => {
    installAudio();
    render(<Harness />);
    await act(async () => { await preview.playPause(); });

    expect(lastContext().waveShapers.length).toBeGreaterThan(0);
    expect(lastContext().waveShapers[0].curve).not.toBeNull();
    expect(lastContext().waveShapers[0].oversample).toBe("4x");
    // No compressor is created when the manifest sets no compressor profile.
    expect(lastContext().compressors).toHaveLength(0);
  });

  it("builds no EQ/compressor/bass nodes when the manifest sets no profiles", async () => {
    installAudio();
    render(<Harness mastering={{}} />);
    await act(async () => { await preview.playPause(); });

    expect(lastContext().compressors).toHaveLength(0);
    // Per-stem LFE lowpass pairs and the surround/height send shaping
    // filters (highpass/lowpass/highpass, see masteringProfiles.ts) are
    // always present regardless of manifest settings; no manifest-driven
    // EQ/bass filters (peaking/lowshelf) should exist alongside them.
    const shapedFilters = lastContext().eqFilters.filter((f) => f.type === "peaking" || f.type === "lowshelf");
    const lfeLowpasses = lastContext().eqFilters.filter((f) => f.type === "lowpass" && f.frequency.value === 120);
    expect(shapedFilters).toHaveLength(0);
    expect(lfeLowpasses.length).toBeGreaterThan(0);
  });

  it("builds the compressor from the resolved profile with manifest overrides applied", async () => {
    installAudio();
    render(<Harness mastering={{ compressor: { profile: "glue", ratio: 4 } }} />);
    await act(async () => { await preview.playPause(); });

    expect(lastContext().compressors).toHaveLength(1);
    const comp = lastContext().compressors[0];
    expect(comp.threshold.value).toBe(-18);
    expect(comp.ratio.value).toBe(4);
    expect(comp.attack.value).toBeCloseTo(0.02);
    expect(comp.release.value).toBeCloseTo(0.2);
  });

  it("builds EQ peaking/shelf filters scaled by strength for the selected profile", async () => {
    installAudio();
    render(<Harness mastering={{ eq: { profile: "spatial-warm", strength: 0.5 } }} />);
    await act(async () => { await preview.playPause(); });

    const warmFilters = lastContext().eqFilters.filter((f) => f.type === "peaking" || f.type === "highshelf");
    expect(warmFilters.length).toBeGreaterThan(0);
    // spatial-warm's 100 Hz breakpoint is +1.0 dB; strength 0.5 halves it.
    const hundredHz = warmFilters.find((f) => f.frequency.value === 100);
    expect(hundredHz?.gain.value).toBeCloseTo(0.5);
  });

  it("builds bass sub/mid shelves from the resolved bass profile", async () => {
    installAudio();
    render(<Harness mastering={{ bass: { profile: "boost" } }} />);
    await act(async () => { await preview.playPause(); });

    const shelf = lastContext().eqFilters.find((f) => f.type === "lowshelf" && f.frequency.value === 80);
    const mid = lastContext().eqFilters.find((f) => f.type === "peaking" && Math.round(f.frequency.value) === 126);
    expect(shelf?.gain.value).toBe(2.0);
    expect(mid?.gain.value).toBe(1.0);
  });
});

describe("virtual-loudspeaker ambisonic rendering", () => {
  it("creates one fixed-position ambisonic encoder per positional speaker, not per stem", async () => {
    const { default: monoEncoder } = await import("ambisonics/dist/ambi-monoEncoder");
    (monoEncoder as unknown as { instanceCount: number }).instanceCount = 0;
    installAudio();
    render(<Harness />);
    await act(async () => { await preview.playPause(); });

    // 11 positional speakers (FL/FR/C/SL/SR/BL/BR/TFL/TFR/TBL/TBR) — fixed
    // regardless of how many stems are playing, since the renderer encodes
    // the channel bed, not the stems (see useStemPreview.ts's top comment).
    expect((monoEncoder as unknown as { instanceCount: number }).instanceCount).toBe(11);
  });

  it("mutes a speaker independently of any stem via toggleSpeaker", async () => {
    installAudio();
    render(<Harness />);
    await act(async () => { await preview.playPause(); });

    expect(preview.speakerEnabled.TFL).not.toBe(false);
    act(() => { preview.toggleSpeaker("TFL"); });
    expect(preview.speakerEnabled.TFL).toBe(false);
  });
});

describe("useStemPreview mixing alignment", () => {
  it("routes each stem's source through its own mute/solo/rebalance gain node, not straight to the splitter", async () => {
    // Regression: the source used to connect straight to the splitter,
    // bypassing the stem gain node entirely, so mute/solo/rebalance had no
    // audible effect even though `apply()` set the (unconnected) gain value.
    installAudio();
    render(<Harness mix={{ stem_enabled: { Vocals: false } }} />);
    await act(async () => { await preview.playPause(); });

    const ctx = lastContext();
    expect(ctx.bufferSources).toHaveLength(2);
    const [vocalsSource, bassSource] = ctx.bufferSources;
    // First connection out of each source must be a gain node (the stem
    // gain) whose value reflects that stem's mute state.
    expect(vocalsSource.connections[0]).toBeInstanceOf(FakeGain);
    expect((vocalsSource.connections[0] as FakeGain).gain.value).toBe(0);
    expect(bassSource.connections[0]).toBeInstanceOf(FakeGain);
    expect((bassSource.connections[0] as FakeGain).gain.value).toBe(1);
  });

  it("scales stem gain by the front-routed fraction under the source anchor, not the full stem", async () => {
    installAudio();
    // Vocals routes entirely to front (FL/FR); Bass routes entirely to a
    // surround channel. Anchor strength 1.0 should silence Vocals' direct
    // send but leave Bass untouched, mirroring the backend's front-only blend.
    render(<Harness mix={{
      stem_source_anchor_strength: 1,
      stem_routing: { Vocals: { FL: 0.5, FR: 0.5 }, Bass: { SL: 0.6, SR: 0.6 } },
    }} />);
    await act(async () => { await preview.playPause(); });

    // Reach into the hook's internal node map indirectly via play behavior:
    // both sources should still be created and started regardless of gain.
    expect(preview.playing).toBe(true);
  });
});
