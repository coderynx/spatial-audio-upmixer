import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ProjectDeliverySection } from "./ProjectDeliverySection";
import { defaultProjectManifest, type Manifest } from "@/lib/manifest";

function renderSection(format: Partial<Manifest["format"]> = {}, layout = "7.1.4") {
  const onChange = vi.fn();
  const manifest: Manifest = {
    ...defaultProjectManifest,
    mixing: { ...defaultProjectManifest.mixing, channel_layout: layout },
    format: { ...defaultProjectManifest.format, ...format },
  };
  render(
    <ProjectDeliverySection manifest={manifest} configuration={null} onChange={onChange} />,
  );
  return { onChange };
}

function codecSelect() {
  return screen.getByRole("combobox", { name: "Codec" });
}

function codecOption(label: RegExp) {
  return within(codecSelect()).getByRole("option", { name: label }) as HTMLOptionElement;
}

describe("ProjectDeliverySection", () => {
  // Radix's Select menu cannot open under jsdom (no pointer capture), so the
  // format names are read off the closed trigger, one render per format.
  it.each([
    ["multichannel", "7.1.4", "Multichannel audio"],
    ["multichannel", "stereo", "Stereo audio"],
    ["adm-bwf", "7.1.4", "ADM Broadcast Wave Format"],
    ["binaural", "7.1.4", "Binaural"],
    ["transaural", "7.1.4", "Transaural"],
  ])("names the %s delivery on a %s layout", (type, layout, label) => {
    renderSection({ type }, layout);
    expect(
      within(screen.getByRole("combobox", { name: "Format" })).getByText(label),
    ).toBeInTheDocument();
  });

  it("disables FLAC for a bed wider than eight channels", () => {
    renderSection({}, "7.1.4");
    expect(codecOption(/FLAC — Max 8 ch/).disabled).toBe(true);
  });

  it("offers FLAC once the delivery fits inside eight channels", () => {
    renderSection({}, "5.1");
    expect(codecOption(/^FLAC$/).disabled).toBe(false);
  });

  it("offers FLAC for a binaural render off a wide bed", () => {
    renderSection({ type: "binaural" }, "7.1.4");
    expect(codecOption(/^FLAC$/).disabled).toBe(false);
  });

  it("disables Opus away from its supported sample rates", () => {
    renderSection({ sample_rate: 44100 });
    expect(codecOption(/Opus/).disabled).toBe(true);
  });

  it("locks the codec to WAV for an ADM-BWF master", () => {
    renderSection({ type: "adm-bwf" });
    expect(codecSelect()).toBeDisabled();
    expect(screen.getByText("ADM-BWF is a WAV container.")).toBeInTheDocument();
  });

  it("disables the bit depth for a lossy codec", () => {
    renderSection({ codec: "ogg_vorbis" });
    expect(screen.getByRole("combobox", { name: "Bit depth" })).toBeDisabled();
    expect(screen.getByText("Lossy codec — no bit depth.")).toBeInTheDocument();
  });

  it("narrows the bit depth to what FLAC can carry", () => {
    renderSection({ codec: "flac" }, "5.1");
    const depths = within(screen.getByRole("combobox", { name: "Bit depth" }))
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(depths).toEqual(["PCM_16", "PCM_24"]);
  });

  it("retargets a codec the new sample rate cannot carry", async () => {
    const { onChange } = renderSection({ codec: "ogg_opus", sample_rate: 48000 });
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Sample rate" }),
      "96000",
    );
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        format: expect.objectContaining({ codec: "wav_pcm", sample_rate: 96000 }),
      }),
    );
  });
});
