import * as React from "react";
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProjectStem } from "@/api";
import { useStemPreview } from "./useStemPreview";

class FakeNode {
  connect() { return this; }
  disconnect() {}
}

class FakeGain extends FakeNode {
  gain = { value: 1 };
}

class FakeBiquadFilter extends FakeNode {
  type = "";
  frequency = { value: 0 };
  gain = { value: 0 };
}

class FakePanner extends FakeNode {
  panningModel = "";
  distanceModel = "";
  refDistance = 0;
  rolloffFactor = 0;
  positionX = { value: 0 };
  positionY = { value: 0 };
  positionZ = { value: 0 };
  setPosition() {}
}

class FakeAudioContext {
  currentTime = 0;
  closed = false;
  destination = new FakeNode();
  createDynamicsCompressor() { return new FakeNode(); }
  createGain() { return new FakeGain(); }
  createPanner() { return new FakePanner(); }
  createBiquadFilter() { return new FakeBiquadFilter(); }
  createMediaElementSource() { return new FakeNode(); }
  resume = vi.fn(async () => {
    if (this.closed) throw new Error("Cannot resume a closed AudioContext.");
  });
  close = vi.fn(async () => { this.closed = true; });
}

class FakeAudio extends EventTarget {
  preload = "";
  crossOrigin: string | null = null;
  readyState = HTMLMediaElement.HAVE_FUTURE_DATA;
  duration = 30;
  error: MediaError | null = null;
  paused = true;
  play = vi.fn(async () => { this.paused = false; });
  pause = vi.fn(() => { this.paused = true; });
  private value = 0;

  get currentTime() { return this.value; }

  set currentTime(value: number) {
    this.value = value;
    this.dispatchEvent(new Event("seeked"));
  }
}

const stems: ProjectStem[] = [
  { id: "vocals", stem_key: "Vocals", sample_rate: 48000, channels: 2, size_bytes: 1, audio_url: "/vocals.wav" },
  { id: "drums", stem_key: "Drums", sample_rate: 48000, channels: 2, size_bytes: 1, audio_url: "/drums.wav" },
];

let preview: ReturnType<typeof useStemPreview>;

function Harness() {
  preview = useStemPreview(stems, {});
  return null;
}

function DynamicHarness({ nextStems }: { nextStems: ProjectStem[] }) {
  preview = useStemPreview(nextStems, {});
  return null;
}

function installAudio(failSecond = false) {
  const elements: FakeAudio[] = [];
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("Audio", class extends FakeAudio {
    constructor() {
      super();
      if (failSecond && elements.length === 1) this.play.mockRejectedValue(new Error("blocked"));
      elements.push(this);
    }
  });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  return elements;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useStemPreview", () => {
  it("recreates its AudioContext after StrictMode effect cleanup", async () => {
    const elements = installAudio();
    render(<React.StrictMode><Harness /></React.StrictMode>);

    await act(async () => { await preview.playPause(); });

    expect(elements).toHaveLength(4);
    expect(preview.playing).toBe(true);
    expect(preview.error).toBeNull();
  });

  it("initializes when project stems arrive after first render", async () => {
    const elements = installAudio();
    const rendered = render(<DynamicHarness nextStems={[]} />);

    await act(async () => {
      rendered.rerender(<DynamicHarness nextStems={stems} />);
    });

    expect(elements).toHaveLength(2);
    expect(preview.ready).toBe(true);
  });

  it("does not leave a partial preview playing when one stem rejects playback", async () => {
    const elements = installAudio(true);
    render(<Harness />);

    await act(async () => { await preview.playPause(); });

    expect(elements).toHaveLength(2);
    expect(elements[0].play).toHaveBeenCalledOnce();
    expect(elements[1].play).toHaveBeenCalledOnce();
    expect(elements.every((element) => element.paused)).toBe(true);
    expect(preview.playing).toBe(false);
    expect(preview.error).toBe("Unable to play every preview stem: blocked");
  });

  it("seeks every stem once, then resumes them together", async () => {
    const elements = installAudio();
    render(<Harness />);

    await act(async () => { await preview.playPause(); });
    await act(async () => {
      preview.beginScrub();
      preview.scrubTo(12.5);
      await preview.commitScrub(12.5);
    });

    expect(elements.every((element) => element.currentTime === 12.5)).toBe(true);
    expect(elements.every((element) => element.play.mock.calls.length === 2)).toBe(true);
    expect(preview.currentTime).toBe(12.5);
    expect(preview.playing).toBe(true);
  });
});
