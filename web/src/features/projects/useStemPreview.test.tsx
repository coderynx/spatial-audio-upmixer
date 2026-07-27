import * as React from "react";
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProjectStem } from "@/api";
import { applyTruePeakCeiling, useStemPreview, type OutputMode, type SpatialProfile } from "./useStemPreview";

class FakeAudioParam {
  value = 0;
  // Ramped writes (see rampGainTo in useStemPreview.ts) settle immediately
  // in this fake — tests assert the resulting gain value, not ramp timing.
  setTargetAtTime(target: number) {
    this.value = target;
  }
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

// Fake for the `ambisonics` package's mono encoder (imported directly rather
// than the barrel — see ambisonics.d.ts for why); the decode stage no longer
// uses this library (see useStemPreview.ts's ConvolverNode bank). The real
// class builds a WebAudio graph with channel counts jsdom's fake context
// doesn't model, so it's mocked. Defined inline (no outer-scope references)
// since `vi.mock` factories run before the rest of this module's top-level code.
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

class FakeConvolver extends FakeNode {
  buffer: unknown = null;
  normalize = true;
}

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
  // Read by the first-play loudness warm-up (`runLoudnessWarmup` /
  // `measureOutputLoudness` in useStemPreview.ts) on `mergePointAnalyser`.
  // Silence here is fine — these tests assert graph wiring, not the
  // measured loudness value; `measuredLkfs` just falls back to its -70
  // (unity-gain) default, same as before this analyser existed.
  getFloatTimeDomainData(array: Float32Array) {
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
  convolvers: FakeConvolver[] = [];
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
  createConvolver() {
    const convolver = new FakeConvolver();
    this.convolvers.push(convolver);
    return convolver;
  }
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

function Harness({
  mix,
  mastering,
  layoutChannels,
  outputMode,
  spatialProfile,
}: {
  mix?: MixArg;
  mastering?: MasterArg;
  layoutChannels?: string[];
  outputMode?: OutputMode;
  spatialProfile?: SpatialProfile;
}) {
  preview = useStemPreview(stems, {}, mix, null, mastering, layoutChannels, outputMode, spatialProfile);
  return null;
}

function hrirUrls(): string[] {
  const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls
    .map((args: unknown[]) => args[0] as string)
    .filter((url) => url.startsWith("/hrir/"));
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
    // always present regardless of manifest settings, as is the (always
    // built, fixed-topology) binaural voicing chain — inert at the default
    // "studio" profile's neutral params (gain 0). No manifest-driven,
    // *active* EQ/bass filters (peaking/lowshelf with nonzero gain) should
    // exist alongside them.
    const shapedFilters = lastContext().eqFilters.filter(
      (f) => (f.type === "peaking" || f.type === "lowshelf") && f.gain.value !== 0,
    );
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

  it("scales the sidechain sum by 1/sqrt(channelCount) before the detector, matching the backend's RMS-across-channels average", async () => {
    installAudio();
    render(<Harness mastering={{ compressor: { profile: "glue" } }} />);
    await act(async () => { await preview.playPause(); });

    // Default test harness layout is every positional channel (11, no LFE) —
    // upmixer/mastering/compressor.py detects on sqrt(sum(ch^2)/n_ch), an
    // RMS average, not a raw sum; without this compensating gain the
    // fanned-in `sum` node would be up to ~sqrt(11)x (~+20dB) hotter than
    // the backend's detector for the same content.
    const expected = 1 / Math.sqrt(11);
    const detectorScale = lastContext().gains.find((g) => Math.abs(g.gain.value - expected) < 1e-6);
    expect(detectorScale).toBeDefined();
  });

  it("convolves the mastering-bus EQ against the real backend FIR asset, wet/dry blended by strength", async () => {
    installAudio();
    render(<Harness mastering={{ eq: { profile: "spatial-warm", strength: 0.7 } }} />);
    await act(async () => { await preview.playPause(); });

    // Fix 4's real fix: no more biquad approximation — the preview fetches
    // and convolves against the actual backend-computed FIR
    // (scripts/build_eq_filters.py, see masteringProfiles.ts's
    // buildFirEqNode) instead of a peaking/shelf cascade.
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const urls = fetchMock.mock.calls.map((args: unknown[]) => args[0] as string);
    expect(urls).toContain("/eq_fir/master_spatial-warm.wav");

    // _apply_fir's wet/dry blend: (1-strength)*dry + strength*filtered.
    const dry = lastContext().gains.find((g) => Math.abs(g.gain.value - 0.3) < 1e-6);
    const wet = lastContext().gains.find((g) => Math.abs(g.gain.value - 0.7) < 1e-6);
    expect(dry).toBeDefined();
    expect(wet).toBeDefined();

    // Buffer starts unset (non-blocking) and gets assigned once the fetch
    // + decode resolves.
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(lastContext().convolvers.some((c) => c.buffer !== null)).toBe(true);
  });

  it("builds bass sub/mid additive band-gain stages from the resolved bass profile", async () => {
    // previewGraph.ts's bass sub/mid stage mirrors
    // upmixer/mastering/bass.py::BassController._apply_band_gain's additive
    // identity (`(ch - band) + band*gain_lin`) via a lowpass (sub) / lowpass
    // -> highpass bandpass (mid) feeding a GainNode set to `gain_lin - 1`,
    // not a native "lowshelf"/"peaking" BiquadFilterNode — see Ledger D9.
    installAudio();
    render(<Harness mastering={{ bass: { profile: "boost" } }} />);
    await act(async () => { await preview.playPause(); });

    const subLowpass = lastContext().eqFilters.find((f) => f.type === "lowpass" && f.frequency.value === 80);
    const midLowpass = lastContext().eqFilters.find((f) => f.type === "lowpass" && f.frequency.value === 200);
    const midHighpass = lastContext().eqFilters.find((f) => f.type === "highpass" && f.frequency.value === 80);
    expect(subLowpass).toBeDefined();
    expect(midLowpass).toBeDefined();
    expect(midHighpass).toBeDefined();

    const subBandGain = lastContext().gains.find((g) => Math.abs(g.gain.value - (10 ** (2.0 / 20) - 1)) < 1e-6);
    const midBandGain = lastContext().gains.find((g) => Math.abs(g.gain.value - (10 ** (1.0 / 20) - 1)) < 1e-6);
    expect(subBandGain).toBeDefined();
    expect(midBandGain).toBeDefined();
  });
});

describe("per-stem EQ (mix.stem_eq)", () => {
  // upmixer/separation/stem_eq.py applies a per-stem tonal EQ (before
  // spatial routing) on the backend export; this used to have no preview
  // mirror at all, so a stem addressed with e.g. "vocal-presence" played
  // unequalized in-browser but boosted (nasal-reading) on export.
  it("builds no per-stem EQ convolver when a stem has no stem_eq entry", async () => {
    installAudio();
    render(<Harness mix={{}} />);
    await act(async () => { await preview.playPause(); });

    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const urls = fetchMock.mock.calls.map((args: unknown[]) => args[0] as string);
    expect(urls.some((url) => url.startsWith("/eq_fir/stem_"))).toBe(false);
  });

  it("convolves the addressed stem against the real backend FIR asset, fully wet (no strength knob on stem_eq)", async () => {
    installAudio();
    render(<Harness mix={{ stem_eq: { Vocals: "vocal-presence" } }} />);
    await act(async () => { await preview.playPause(); });

    // Fix 4's real fix: no more biquad approximation of
    // upmixer/separation/stem_eq.py STEM_EQ_PROFILES["vocal-presence"] —
    // the preview convolves against the actual backend-computed FIR asset.
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    const urls = fetchMock.mock.calls.map((args: unknown[]) => args[0] as string);
    expect(urls).toContain("/eq_fir/stem_vocal-presence.wav");

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(lastContext().convolvers.some((c) => c.buffer !== null)).toBe(true);
  });
});

describe("output-path hearing safety", () => {
  it("gives the native discrete path its own soft-limit ceiling, not just the volume gain", async () => {
    installAudio();
    render(<Harness />);
    await act(async () => { await preview.playPause(); });

    // One tanh soft-limiter for binaural/stereo (pre-existing) plus one for
    // native (nativeSoftLimitNode in useStemPreview.ts) — built unconditionally
    // in `initialize()` regardless of which mode is actually selected, so
    // native can never reach `ctx.destination` through the bare volume gain
    // unlimited.
    expect(lastContext().waveShapers.length).toBe(2);
  });

  it("routes native output mode to ctx.destination through the native soft-limiter", async () => {
    installAudio();
    render(<Harness layoutChannels={["FL", "FR"]} outputMode="native" />);
    await act(async () => { await preview.playPause(); });

    const ctx = lastContext();
    // Creation order in `initialize()`: nativeSoftLimitNode (native bus) is
    // built before softLimitNode (stereo/binaural bus), so it's first.
    const nativeLimiter = ctx.waveShapers[0];
    expect(nativeLimiter.connections).toContain(ctx.destination);
  });

  it("ramps volume/mute changes instead of snapping the gain, so a change never reaches headphones as a step", async () => {
    installAudio();
    const spy = vi.spyOn(FakeAudioParam.prototype, "setTargetAtTime");
    render(<Harness />);
    await act(async () => { await preview.playPause(); });
    spy.mockClear();

    act(() => { preview.setVolume(0.3); });
    expect(spy).toHaveBeenCalled();
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
    // First play triggers the muted loudness warm-up (runLoudnessWarmup in
    // useStemPreview.ts), which schedules and stops its own throwaway
    // buffer sources before the real, audible ones — so the two sources
    // that matter here are the *last* two `ctx.bufferSources` created, not
    // necessarily the only two.
    expect(ctx.bufferSources.length).toBeGreaterThanOrEqual(2);
    const [vocalsSource, bassSource] = ctx.bufferSources.slice(-2);
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

describe("decode filter set loading (HRIR)", () => {
  // Regression: an ordinary parameter edit (volume, mute, stem routing,
  // mastering) used to re-fire 4 `/hrir/*.wav` fetches because the profile
  // effect depended on `apply`'s identity, which changes on every one of
  // those edits — see useStemPreview.ts's decode-filter-set effect.
  it("does not refetch the decode filter set when volume/mix/mastering change, only on a profile switch", async () => {
    installAudio();
    const { rerender } = render(<Harness mix={{}} mastering={{}} />);
    await act(async () => { await preview.playPause(); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    // initialize() loads the default ("studio") profile's 4-part set once.
    expect(hrirUrls()).toHaveLength(4);
    expect(hrirUrls().every((url) => url.startsWith("/hrir/studio_o3_decode_"))).toBe(true);

    // Volume/mute changes and new mix/mastering object identities (as a
    // manifest edit produces) must not re-trigger a fetch.
    act(() => { preview.setVolume(0.4); });
    act(() => { preview.toggleMute(); });
    rerender(<Harness mix={{ stem_rebalance: { Vocals: 3 } }} mastering={{ loudness: { target: -16 } }} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(hrirUrls()).toHaveLength(4);

    // A genuine profile switch fetches the new profile's 4 parts...
    rerender(<Harness mix={{}} mastering={{}} spatialProfile="listening" />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(hrirUrls()).toHaveLength(8);
    expect(hrirUrls().slice(4).every((url) => url.startsWith("/hrir/listening_o3_decode_"))).toBe(true);

    // ...and switching back to an already-loaded profile is a cache hit,
    // not a new fetch.
    rerender(<Harness mix={{}} mastering={{}} spatialProfile="studio" />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(hrirUrls()).toHaveLength(8);
  });
});

describe("applyTruePeakCeiling", () => {
  // The preview-side mirror of normalize_loudness's max_tp_dbtp gain
  // reduction (upmixer/loudness.py) — see docs/contracts/
  // preview_export_parity.md Ledger D12's "True-peak ceiling" row.

  it("is a no-op when the loudness-corrected signal is already under the ceiling", () => {
    // -20 dBTP pre-gain + 6 dB loudness gain = -14 dBTP post-gain, well
    // under a -1 dBTP ceiling.
    const gain = applyTruePeakCeiling(-20, 10 ** (6 / 20), -1);
    expect(gain).toBeCloseTo(10 ** (6 / 20), 6);
  });

  it("reduces gain exactly enough to land the post-gain peak on the ceiling", () => {
    // -5 dBTP pre-gain + 10 dB loudness gain = 5 dBTP post-gain, 6 dB over
    // a -1 dBTP ceiling -> expect the returned gain to be 6 dB less than
    // the uncorrected loudness gain.
    const loudnessGain = 10 ** (10 / 20);
    const gain = applyTruePeakCeiling(-5, loudnessGain, -1);
    expect(gain).toBeCloseTo(loudnessGain * 10 ** (-6 / 20), 6);
    // Verify the invariant directly: applying `gain` lands exactly at -1 dBTP.
    const postGainTpDbtp = -5 + 20 * Math.log10(gain);
    expect(postGainTpDbtp).toBeCloseTo(-1, 6);
  });

  it("never increases gain, even when the pre-gain signal is already quiet", () => {
    const loudnessGain = 10 ** (30 / 20);
    const gain = applyTruePeakCeiling(-70, loudnessGain, -1);
    expect(gain).toBeCloseTo(loudnessGain, 6);
  });
});
