import { afterEach, describe, expect, it, vi } from "vitest";
import { computeRealSH } from "spherical-harmonic-transform";
import {
  CROSSTALK_LOUDNESS_MAX_GAIN_DB,
  EQ_FIR_ASSETS,
  STEM_EQ_FIR_ASSETS,
  TRANSAURAL_VOICING_PARAMS,
  VOICING_PARAMS,
  XTC_FILTER_SET,
  buildFirEqNode,
  fetchEqFirBuffer,
  measureBufferTruePeakDbtp,
} from "./masteringProfiles";

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
class FakeConvolver extends FakeNode {
  buffer: unknown = null;
  normalize = true;
}
class FakeAudioContext {
  createGain() { return new FakeGain(); }
  createConvolver() { return new FakeConvolver(); }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("VOICING_PARAMS.listening", () => {
  it("matches the backend upmixer/binaural/profiles.py values exactly", () => {
    // Pins the values docs/standards/spatial_audio_engine.md §5 and
    // upmixer/binaural/profiles.py specify — a prior hand-sync drift
    // doubled analogous values on the web side with nothing catching it.
    const listening = VOICING_PARAMS.listening;
    expect(listening.crossfeedAmount).toBeCloseTo(0.10);
    expect(listening.bassShelfGainDb).toBeCloseTo(2.0);
    expect(listening.airShelfGainDb).toBeCloseTo(3.0);
    expect(listening.presenceGainDb).toBeCloseTo(1.5);
    expect(listening.stereoWiden).toBeCloseTo(0.15);
    expect(listening.loudnessTargetLkfs).toBeNull();
  });
});

describe("TRANSAURAL_VOICING_PARAMS", () => {
  it("matches the backend upmixer/crosstalk/profiles.py values exactly", () => {
    // Same drift-guard as VOICING_PARAMS.listening above, for the
    // crosstalk-cancellation (transaural) speaker profiles.
    expect(TRANSAURAL_VOICING_PARAMS.stereo.crossfeedAmount).toBe(0);
    expect(TRANSAURAL_VOICING_PARAMS.stereo.stereoWiden).toBe(0);

    const smartSpeaker = TRANSAURAL_VOICING_PARAMS.smart_speaker;
    expect(smartSpeaker.bassShelfHz).toBeCloseTo(150);
    expect(smartSpeaker.bassShelfGainDb).toBeCloseTo(1.5);
    expect(smartSpeaker.stereoWiden).toBeCloseTo(0.20);

    const car = TRANSAURAL_VOICING_PARAMS.car;
    expect(car.bassShelfHz).toBeCloseTo(120);
    expect(car.bassShelfGainDb).toBeCloseTo(2.5);
    expect(car.presenceHz).toBeCloseTo(2500);
    expect(car.presenceGainDb).toBeCloseTo(1.0);
    expect(car.stereoWiden).toBeCloseTo(0.10);
  });

  it("XTC_FILTER_SET names match the backend asset basenames", () => {
    expect(XTC_FILTER_SET.stereo).toBe("stereo_xtc");
    expect(XTC_FILTER_SET.smart_speaker).toBe("smart_speaker_xtc");
    expect(XTC_FILTER_SET.car).toBe("car_xtc");
  });

  it("CROSSTALK_LOUDNESS_MAX_GAIN_DB matches the backend ceiling", () => {
    expect(CROSSTALK_LOUDNESS_MAX_GAIN_DB).toBe(6.0);
  });
});

describe("ACN 12 N3D correction", () => {
  it("scaling computeRealSH's ACN 12 by 1/sqrt(7) matches the backend encoder", () => {
    // upmixer/binaural/ambisonics.py::encode_gains's ACN 12 (Y3^0)
    // deliberately omits the standard N3D sqrt(7) factor the decode filter
    // bank was fit against (see docs/standards/spatial_audio_engine.md §3).
    // useStemPreview.ts applies this same 1/sqrt(7) correction on its ACN 12
    // decode tap; this pins that the correction lands on the right value.
    const elevationDeg = 30;
    const elevationRad = (elevationDeg * Math.PI) / 180;
    const gains = computeRealSH(3, [[0, elevationRad]]);
    const acn12 = gains[12][0] * (1 / Math.sqrt(7));

    const sinD = Math.sin(elevationRad);
    const expected = 0.5 * sinD * (5 * sinD * sinD - 3);
    expect(acn12).toBeCloseTo(expected, 6);
  });
});

describe("FIR EQ (real backend filter, not a biquad approximation)", () => {
  // Fix 4's real fix: instead of approximating the backend's minimum-phase
  // FIR curve with a cascade of biquad filters (which always left some
  // magnitude-response gap, e.g. up to +2.4dB overshoot in the 2-6kHz
  // presence/nasal band no matter how the biquad Qs were tuned), the preview
  // now convolves against the *actual* FIR the backend computes — the same
  // asset scripts/build_eq_filters.py generates by calling
  // upmixer.mastering.eq._build_fir / upmixer.separation.stem_eq._build_fir
  // directly, shipped under web/public/eq_fir/.

  it("names an asset for every known master and stem EQ profile", () => {
    for (const name of Object.values(EQ_FIR_ASSETS)) {
      expect(name).toMatch(/^master_/);
    }
    for (const name of Object.values(STEM_EQ_FIR_ASSETS)) {
      expect(name).toMatch(/^stem_/);
    }
  });

  it("fetchEqFirBuffer fetches /eq_fir/<asset>.wav and decodes it", async () => {
    const decodeAudioData = vi.fn(async () => ({ duration: 1 }) as unknown as AudioBuffer);
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      expect(url).toBe("/eq_fir/master_spatial-present.wav");
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(8) };
    }));
    const ctx = { decodeAudioData } as unknown as BaseAudioContext;
    await fetchEqFirBuffer(ctx, "master_spatial-present");
    expect(decodeAudioData).toHaveBeenCalledOnce();
  });

  it("buildFirEqNode wires input -> [dry, convolver->wet] -> output with a silent (unset) buffer until loaded", () => {
    const ctx = new FakeAudioContext() as unknown as AudioContext;
    const node = buildFirEqNode(ctx, 0.6);

    expect(node.convolver.buffer).toBeNull();
    expect((node.dryGain as unknown as FakeGain).gain.value).toBeCloseTo(0.4);
    expect((node.wetGain as unknown as FakeGain).gain.value).toBeCloseTo(0.6);

    const input = node.input as unknown as FakeNode;
    expect(input.connections).toContain(node.dryGain);
    expect(input.connections).toContain(node.convolver);
  });
});

describe("measureBufferTruePeakDbtp", () => {
  // Shared with render-preview-golden.mjs's cross-engine true-peak metric
  // (see docs/contracts/preview_export_parity.md Ledger D12) — this is the
  // one implementation of the 4x-oversampled approximation, so these tests
  // cover both the live preview's true-peak safety net and the harness.

  it("reads close to 0 dBTP for a full-scale constant signal", () => {
    // The 32-tap Hann-windowed-sinc kernel isn't a perfect unity-gain
    // interpolator (that's the Tier-3 approximation this contract accepts,
    // see docs/contracts/preview_export_parity.md §3) — it has a small,
    // fixed overshoot even for a flat DC input, so this is a loose sanity
    // bound, not an exact-0 assertion.
    const buf = new Float32Array(64).fill(1.0);
    const dbtp = measureBufferTruePeakDbtp(buf);
    expect(dbtp).toBeGreaterThan(-1.5);
    expect(dbtp).toBeLessThan(1.5);
  });

  it("scales with amplitude: halving the signal drops the reading by ~6.02 dB", () => {
    const full = measureBufferTruePeakDbtp(new Float32Array(64).fill(1.0));
    const half = measureBufferTruePeakDbtp(new Float32Array(64).fill(0.5));
    expect(full - half).toBeCloseTo(6.02, 1);
  });

  it("reads a very negative value for silence", () => {
    const buf = new Float32Array(64).fill(0);
    expect(measureBufferTruePeakDbtp(buf)).toBeLessThan(-100);
  });

  it("finds an inter-sample peak above 0 dBTP for a full-scale Nyquist square wave", () => {
    // The classic true-peak scenario: every discrete sample is exactly
    // +-1.0 (no sample-peak clipping), but the reconstructed waveform
    // between samples overshoots — a sample-peak-only measurement would
    // read 0 dBTP here and miss it entirely.
    const buf = new Float32Array(64);
    for (let i = 0; i < buf.length; i++) buf[i] = i % 2 === 0 ? 1 : -1;
    expect(measureBufferTruePeakDbtp(buf)).toBeGreaterThan(0);
  });
});
