import { fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MixerView } from "./MixerView";
import { stripMeterWidth } from "./StripMeter";
import type { MeterLevel } from "./useStemPreview";

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
  it("labels stereo and mono stem strips", () => {
    renderMixer();

    const stereo = screen.getByTitle("Vocals — stereo");
    const mono = screen.getByTitle("Bass — mono");

    expect(stereo).toBeInTheDocument();
    expect(mono).toBeInTheDocument();
    expect(stripMeterWidth(2)).toBeGreaterThan(stripMeterWidth(1));
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

  it("uses a shadcn separator for every strip boundary", () => {
    renderMixer();

    expect(screen.getByRole("separator", { name: "Resize Vocals strip" })).toHaveAttribute("aria-orientation", "vertical");
    expect(screen.getByRole("separator", { name: "Resize Bass strip" })).toHaveAttribute("aria-orientation", "vertical");
    expect(screen.getByRole("separator", { name: "Resize Anchor strip" })).toHaveAttribute("aria-orientation", "vertical");
  });

  it("keeps the master strip's resize boundary keyboard accessible", () => {
    renderMixer();
    expect(screen.getByRole("separator", { name: "Resize Master strip" })).toHaveAttribute("tabindex", "0");
  });

});
