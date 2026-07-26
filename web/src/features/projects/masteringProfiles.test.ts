import { afterEach, describe, expect, it, vi } from "vitest";
import { computeRealSH } from "spherical-harmonic-transform";
import { EQ_FIR_ASSETS, STEM_EQ_FIR_ASSETS, VOICING_PARAMS, buildFirEqNode, fetchEqFirBuffer } from "./masteringProfiles";

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
    // doubled these on the web side (bass/air 2.0, presence 1.0, widen
    // 0.15) with nothing catching it.
    const listening = VOICING_PARAMS.listening;
    expect(listening.bassShelfGainDb).toBeCloseTo(1.0);
    expect(listening.airShelfGainDb).toBeCloseTo(1.0);
    expect(listening.presenceGainDb).toBeCloseTo(0.5);
    expect(listening.stereoWiden).toBeCloseTo(0.10);
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
