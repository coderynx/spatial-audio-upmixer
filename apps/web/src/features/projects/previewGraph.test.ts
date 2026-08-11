import { describe, expect, it, vi } from "vitest";
import { buildMasteringGraph, monitorMastering, type MasterPreview, type MasteringChannelPort } from "./previewGraph";
import type { EngineConstants } from "./masteringProfiles";

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

function fakePort(): MasteringChannelPort {
  return {
    input: new FakeGain() as unknown as AudioNode,
    output: new FakeGain() as unknown as AudioNode,
  };
}

describe("buildMasteringGraph — reference match stage", () => {
  // LFE never appears in `channelPorts` — it's bridged separately by the
  // caller (audioEngine.ts), RMS-only, since match_reference.py never
  // spectrally corrects LFE. These tests cover the bed channels this
  // function does wire.

  it("fetches the FIR once and shares the same buffer across every channel, with strength/max_db in the URL", async () => {
    const ctx = new FakeAudioContext() as unknown as BaseAudioContext;
    const channelPorts = new Map<string, MasteringChannelPort>([
      ["FL", fakePort()], ["FR", fakePort()], ["C", fakePort()],
    ]);
    const sharedBuffer = { duration: 1 } as unknown as AudioBuffer;
    const loader = vi.fn(async () => sharedBuffer);
    const mastering: MasterPreview = {
      match_reference: {
        fir_url: "/api/v1/projects/p1/reference-match/fir?v=sig",
        strength: 0.7, spectrum: true, rms: false, max_db: 6,
      },
    };
    const handle = buildMasteringGraph(
      ctx, channelPorts, mastering, new Map(), {} as EngineConstants,
      { refMatchBufferCache: new Map(), refMatchLoader: loader },
    );
    await Promise.resolve();
    await Promise.resolve();

    expect(loader).toHaveBeenCalledTimes(1);
    expect(loader).toHaveBeenCalledWith(
      ctx, "/api/v1/projects/p1/reference-match/fir?v=sig&strength=0.7&max_db=6",
    );

    const convolvers = (handle.nodes as unknown as FakeNode[])
      .filter((node) => node instanceof FakeConvolver) as unknown as FakeConvolver[];
    expect(convolvers).toHaveLength(3);
    for (const conv of convolvers) expect(conv.buffer).toBe(sharedBuffer);
  });

  it("does not fetch when spectrum is off or strength is 0", () => {
    const ctx = new FakeAudioContext() as unknown as BaseAudioContext;
    const loader = vi.fn(async () => ({ duration: 1 }) as unknown as AudioBuffer);

    buildMasteringGraph(
      ctx, new Map([["FL", fakePort()]]),
      { match_reference: { fir_url: "/fir", strength: 0.7, spectrum: false, rms: false, max_db: 6 } },
      new Map(), {} as EngineConstants,
      { refMatchBufferCache: new Map(), refMatchLoader: loader },
    );
    buildMasteringGraph(
      ctx, new Map([["FL", fakePort()]]),
      { match_reference: { fir_url: "/fir", strength: 0, spectrum: true, rms: false, max_db: 6 } },
      new Map(), {} as EngineConstants,
      { refMatchBufferCache: new Map(), refMatchLoader: loader },
    );

    expect(loader).not.toHaveBeenCalled();
  });

  it("starts full dry (not silent) and flips to full wet once the buffer loads — a slow/failed fetch must degrade to unmatched audio, not silence", async () => {
    const ctx = new FakeAudioContext() as unknown as BaseAudioContext;
    const port = fakePort();
    const loader = vi.fn(async () => ({ duration: 1 }) as unknown as AudioBuffer);

    buildMasteringGraph(
      ctx, new Map([["FL", port]]),
      { match_reference: { fir_url: "/fir", strength: 0.3, spectrum: true, rms: false, max_db: 6 } },
      new Map(), {} as EngineConstants,
      { refMatchBufferCache: new Map(), refMatchLoader: loader },
    );

    // rms is off, so port.input connects directly into the FIR node's own
    // input gain, which fans out to [dryGain, convolver] (see buildFirEqNode).
    const portInput = port.input as unknown as FakeGain;
    const firInput = portInput.connections[0] as unknown as FakeGain;
    const [dryGain, convolver] = firInput.connections as unknown as [FakeGain, FakeConvolver];
    const wetGain = convolver.connections[0] as unknown as FakeGain;

    // Before the fetch resolves: full dry, silent wet — audible, unmatched.
    expect(dryGain.gain.value).toBeCloseTo(1);
    expect(wetGain.gain.value).toBeCloseTo(0);

    await Promise.resolve();
    await Promise.resolve();

    // After: flipped to full wet, matching the server-baked strength.
    expect(dryGain.gain.value).toBeCloseTo(0);
    expect(wetGain.gain.value).toBeCloseTo(1);
  });

  it("stays full dry, never silent, when the fetch fails", async () => {
    const ctx = new FakeAudioContext() as unknown as BaseAudioContext;
    const port = fakePort();
    const loader = vi.fn(async () => { throw new Error("network error"); });

    buildMasteringGraph(
      ctx, new Map([["FL", port]]),
      { match_reference: { fir_url: "/fir", strength: 1, spectrum: true, rms: false, max_db: 6 } },
      new Map(), {} as EngineConstants,
      { refMatchBufferCache: new Map(), refMatchLoader: loader },
    );

    const portInput = port.input as unknown as FakeGain;
    const firInput = portInput.connections[0] as unknown as FakeGain;
    const [dryGain] = firInput.connections as unknown as [FakeGain, FakeConvolver];

    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(dryGain.gain.value).toBeCloseTo(1);
  });
});

describe("monitorMastering", () => {
  it("passes the mastering config through unchanged when not bypassed", () => {
    const mastering = { eq: { profile: "spatial-warm" }, loudness: { target: -16 } };
    expect(monitorMastering(mastering, false)).toBe(mastering);
  });

  it("strips every stage but loudness when bypassed", () => {
    const mastering = {
      eq: { profile: "spatial-warm" },
      compressor: { profile: "glue" },
      bass: { profile: "boost" },
      match_reference: { fir_url: "/ref.wav" },
      loudness: { normalize: true, target: -16, max_tp: -1 },
    };
    expect(monitorMastering(mastering, true)).toEqual({ loudness: mastering.loudness });
  });

  it("returns undefined when bypassed and there is no loudness block to preserve", () => {
    expect(monitorMastering({ eq: { profile: "spatial-warm" } }, true)).toBeUndefined();
    expect(monitorMastering(undefined, true)).toBeUndefined();
  });
});
