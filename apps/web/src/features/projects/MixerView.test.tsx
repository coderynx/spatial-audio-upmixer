import { fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MixerView } from "./MixerView";
import { stripMeterWidth } from "./StripMeter";
import type { MeterLevel } from "./useStemPreview";

// This test environment has no real `window.localStorage` (it's `undefined`,
// not merely empty), which the app's own persistence code already tolerates
// via try/catch — but that means persistence needs a stand-in store to be
// exercised here at all. A small in-memory `Storage` fills that gap for the
// one test that checks a resize survives a remount; every other test runs
// with no storage, same as the rest of the suite, matching production
// behaviour in a browser that blocks storage.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();
  get length() { return this.store.size; }
  clear() { this.store.clear(); }
  getItem(key: string) { return this.store.has(key) ? this.store.get(key)! : null; }
  key(index: number) { return [...this.store.keys()][index] ?? null; }
  removeItem(key: string) { this.store.delete(key); }
  setItem(key: string, value: string) { this.store.set(key, value); }
}

afterEach(() => {
  try { window.localStorage.clear(); } catch { /* storage unavailable in this environment */ }
});

function renderMixer(overrides: Partial<React.ComponentProps<typeof MixerView>> = {}) {
  const stemLevels = { current: new Map<string, MeterLevel[]>() };
  const headphoneLevels = {
    current: {
      left: { rms: 0, peak: 0, clipped: false },
      right: { rms: 0, peak: 0, clipped: false },
    },
  };
  return render(
    <MixerView
      stems={["Vocals", "Bass"]}
      stemChannels={{ Vocals: 2, Bass: 1 }}
      selectedStem={null}
      onSelectStem={vi.fn()}
      gains={{}}
      onGain={vi.fn()}
      enabled={{}}
      solo={[]}
      onToggleMute={vi.fn()}
      onToggleSolo={vi.fn()}
      stemLevels={stemLevels}
      anchorStrength={0}
      onAnchorStrength={vi.fn()}
      headphoneLevels={headphoneLevels}
      volume={1}
      onVolume={vi.fn()}
      muted={false}
      onToggleMasterMute={vi.fn()}
      active={false}
      disabled={false}
      {...overrides}
    />,
  );
}

describe("MixerView channel strips", () => {
  it("sizes a stereo stem's meter for two bars and a mono stem's for one", () => {
    renderMixer();

    const stereo = screen.getByTitle("Vocals — stereo");
    const mono = screen.getByTitle("Bass — mono");

    expect(stereo).toBeInTheDocument();
    expect(mono).toBeInTheDocument();
    // The strip is wider for the stereo stem by exactly the extra bar.
    const stereoStrip = stereo.closest("div")!;
    const monoStrip = mono.closest("div")!;
    const widthOf = (node: HTMLElement) => Number.parseFloat(node.style.width);
    expect(widthOf(stereoStrip) - widthOf(monoStrip)).toBe(
      stripMeterWidth(2) - stripMeterWidth(1),
    );
  });

  it("treats an unknown channel count as mono rather than guessing stereo", () => {
    renderMixer({ stems: ["Other"], stemChannels: {} });

    expect(screen.getByTitle("Other — mono")).toBeInTheDocument();
  });

  it("caps a multichannel stem at the two bars a strip can show", () => {
    renderMixer({ stems: ["Bed"], stemChannels: { Bed: 6 } });

    expect(screen.getByTitle("Bed — stereo")).toBeInTheDocument();
  });

  it("gives every stem a fader plus the master monitor fader", () => {
    renderMixer();

    expect(screen.getByRole("slider", { name: "Vocals gain" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Bass gain" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Monitor level" })).toBeInTheDocument();
  });

  it("places stem-specific controls above the matching fader", () => {
    renderMixer({ topControlForStem: (stem) => stem === "Vocals" && <button type="button">Open Vocals panner</button> });

    const control = screen.getByRole("button", { name: "Open Vocals panner" });
    const fader = screen.getByRole("slider", { name: "Vocals gain" });
    expect(control.compareDocumentPosition(fader) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Open Bass panner" })).not.toBeInTheDocument();
  });

  it("labels the master fader's readout as monitor gain, not program gain", () => {
    renderMixer();

    expect(screen.getByText("Monitor")).toBeInTheDocument();
  });

  it("gives the source anchor its own fader, distinct from a stem's", () => {
    renderMixer({ anchorStrength: 0.62 });

    const fader = screen.getByRole("slider", { name: "Source anchor blend" });
    expect(fader).toHaveAttribute("aria-valuenow", "62");
    expect(fader).toHaveAttribute("aria-valuetext", "62% blend");
    expect(screen.getByText("62%")).toBeInTheDocument();
    // No M/S on the anchor strip — there is nothing to mute or solo about
    // "some of the original track".
    expect(screen.queryByRole("button", { name: /Mute Source anchor|Solo Source anchor/ })).not.toBeInTheDocument();
  });

  it("reports the anchor blend as a fraction, not a percentage, to the manifest", () => {
    const onAnchorStrength = vi.fn();
    renderMixer({ onAnchorStrength });

    fireEvent.keyDown(screen.getByRole("slider", { name: "Source anchor blend" }), { key: "ArrowUp" });

    expect(onAnchorStrength).toHaveBeenCalledWith(0.01);
  });

  it("gives every strip kind its own resize handle, independent of the others", () => {
    renderMixer();

    const widthOf = (node: Element) => Number.parseFloat((node.closest("[style*='width']") as HTMLElement).style.width);
    const vocalsWidthBefore = widthOf(screen.getByRole("button", { name: "Mute Vocals" }));
    const bassWidthBefore = widthOf(screen.getByRole("button", { name: "Mute Bass" }));
    const masterWidthBefore = widthOf(screen.getByRole("slider", { name: "Monitor level" }));

    fireEvent.keyDown(screen.getByRole("slider", { name: "Resize Vocals strip" }), { key: "ArrowRight" });

    expect(widthOf(screen.getByRole("button", { name: "Mute Vocals" }))).toBeGreaterThan(vocalsWidthBefore);
    expect(widthOf(screen.getByRole("button", { name: "Mute Bass" }))).toBe(bassWidthBefore);
    expect(widthOf(screen.getByRole("slider", { name: "Monitor level" }))).toBe(masterWidthBefore);
  });

  it("narrows and widens with the arrow keys, and resets to the minimum on double-click", () => {
    renderMixer();
    const handle = screen.getByRole("slider", { name: "Resize Vocals strip" });

    fireEvent.keyDown(handle, { key: "ArrowRight" });
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(handle).toHaveAttribute("aria-valuenow", "16");

    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(handle).toHaveAttribute("aria-valuenow", "8");

    fireEvent.doubleClick(handle);
    expect(handle).toHaveAttribute("aria-valuenow", "0");
  });

  it("persists a resized strip's width across a remount", () => {
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", { value: new MemoryStorage(), configurable: true });
    try {
      const { unmount } = renderMixer();
      fireEvent.keyDown(screen.getByRole("slider", { name: "Resize Vocals strip" }), { key: "PageUp" });
      unmount();

      renderMixer();

      expect(screen.getByRole("slider", { name: "Resize Vocals strip" })).toHaveAttribute("aria-valuenow", "24");
    } finally {
      if (original) Object.defineProperty(window, "localStorage", original);
    }
  });
});
