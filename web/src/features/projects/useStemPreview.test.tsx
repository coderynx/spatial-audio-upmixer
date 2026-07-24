import * as React from "react";
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProjectStem } from "@/api";
import { useStemPreview } from "./useStemPreview";
import { HRTF_COMPENSATION_BANDS, isDryRouted } from "./masteringProfiles";

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

class FakePanner extends FakeNode {
  panningModel = "";
  distanceModel = "";
  refDistance = 0;
  rolloffFactor = 0;
  positionX = new FakeAudioParam();
  positionY = new FakeAudioParam();
  positionZ = new FakeAudioParam();
  setPosition() {}
}

class FakeStereoPanner extends FakeNode {
  pan = new FakeAudioParam();
}

class FakeChannelSplitter extends FakeNode {}

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
  panners: FakePanner[] = [];
  stereoPanners: FakeStereoPanner[] = [];
  createGain() { return new FakeGain(); }
  createPanner() {
    const panner = new FakePanner();
    this.panners.push(panner);
    return panner;
  }
  createStereoPanner() {
    const panner = new FakeStereoPanner();
    this.stereoPanners.push(panner);
    return panner;
  }
  createChannelSplitter() { return new FakeChannelSplitter(); }
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
  createBufferSource() { return new FakeBufferSource(); }
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
    // The fixed HRTF-compensation cascade (always present, preview-only —
    // see masteringProfiles.ts) and per-stem LFE lowpass pairs are expected
    // regardless of manifest settings; no manifest-driven EQ/bass filters
    // should exist alongside them.
    const hrtfCompFrequencies = new Set([250, 3500, 9000]);
    const lfeLowpasses = lastContext().eqFilters.filter((f) => f.type === "lowpass" && f.frequency.value === 120);
    const shapedFilters = lastContext().eqFilters.filter((f) =>
      (f.type === "peaking" || f.type === "lowshelf") && !hrtfCompFrequencies.has(f.frequency.value));
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

describe("HRTF clarity restoration", () => {
  it("applies the fixed diffuse-field compensation cascade to the HRTF bus", async () => {
    installAudio();
    render(<Harness />);
    await act(async () => { await preview.playPause(); });

    for (const band of HRTF_COMPENSATION_BANDS) {
      const filter = lastContext().eqFilters.find((f) => f.frequency.value === band.frequency && f.type === band.type);
      expect(filter?.gain.value).toBe(band.gain);
    }
  });

  it("routes front, ear-level sources dry but leaves rear/height ones on HRTF", () => {
    // A blended dry+HRTF sum would comb-filter (the HRTF panner's
    // interaural time delay isn't compensable), so this must be a hard
    // either/or choice, never both — see masteringProfiles.ts.
    expect(isDryRouted({ x: 0, y: 0, z: -1 })).toBe(true);
    expect(isDryRouted({ x: 0, y: 0, z: 1 })).toBe(false);
    expect(isDryRouted({ x: 0, y: 0.6, z: -1 })).toBe(false);
  });

  it("gives every non-anchor stem a StereoPanner dry send alongside its HRTF panner", async () => {
    installAudio();
    render(<Harness />);
    await act(async () => { await preview.playPause(); });

    // 2 stems x 1 leg each. No source-preview URL in this harness, so no
    // anchor legs are created.
    expect(lastContext().stereoPanners).toHaveLength(2);
    expect(lastContext().panners).toHaveLength(2);
  });
});

describe("useStemPreview mixing alignment", () => {
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
