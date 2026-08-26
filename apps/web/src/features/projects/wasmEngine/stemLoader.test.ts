import { describe, expect, it, vi } from "vitest";
import { loadBuffer } from "../audioLoaders";
import { loadStemsInto, previewWindowFrames } from "./stemLoader";

vi.mock("../audioLoaders", () => ({ loadBuffer: vi.fn() }));

describe("previewWindowFrames", () => {
  it("splits the PCM budget evenly across stereo stems", () => {
    expect(previewWindowFrames(2, 64)).toBe(4);
  });

  it("only transfers the leading window when the PCM budget is exceeded", async () => {
    vi.mocked(loadBuffer).mockResolvedValue({
      length: 5,
      sampleRate: 1,
      numberOfChannels: 2,
      getChannelData: () => new Float32Array([1, 2, 3, 4, 5]),
    } as unknown as AudioBuffer);
    const addStem = vi.fn();
    const onPreviewLimited = vi.fn();

    const duration = await loadStemsInto(
      { sampleRate: 1 } as AudioContext,
      { addStem } as never,
      [{ id: "stem", stem_key: "Vocals", preview_url: "/stem.ogg" } as never],
      { isCurrent: () => true, onProgress: () => {}, onDuration: () => {}, onPreviewLimited },
      16,
    );

    expect(addStem).toHaveBeenCalledWith(new Float32Array([1, 2]), new Float32Array([1, 2]));
    expect(duration).toBe(2);
    expect(onPreviewLimited).toHaveBeenCalledWith(2);
  });
});
