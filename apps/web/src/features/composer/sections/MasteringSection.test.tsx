import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultManifest } from "@/lib/manifest";
import type { Configuration } from "@/api";
import { TEST_SERVED_CONSTANTS } from "@/features/projects/engineConstants.fixture";
import { MasteringSection } from "./MasteringSection";

const configuration: Configuration = {
  defaults: {},
  manifest_keys: {},
  choices: {
    channel_layouts: [],
    output_types: [],
    output_subtypes: [],
    sample_rates: [],
    modes: [],
    spatial_profiles: [],
    eq_profiles: ["neutral"],
    compressor_profiles: ["transparent", "glue"],
    bass_profiles: ["tight"],
    stem_eq_profiles: [],
    stems: [],
  },
  constants: TEST_SERVED_CONSTANTS,
  capabilities: {
    stem_separation: {
      available: true,
      backend: "cpu",
      accelerated: false,
      accelerator_detected: false,
      accelerator_issue: null,
      platform: "test",
      install_message: null,
    },
  },
};

describe("MasteringSection", () => {
  it("uploads a reference and exposes match controls", () => {
    const onReferenceUpload = vi.fn();
    render(
      <MasteringSection
        manifest={defaultManifest}
        setManifest={vi.fn()}
        configuration={null}
        masteringReference={null}
        referenceUploading={false}
        referenceError={null}
        onReferenceUpload={onReferenceUpload}
        onReferenceClear={vi.fn()}
      />,
    );

    expect(screen.getByText("Reference EQ match")).toBeInTheDocument();
    expect(screen.getAllByRole("slider")[0]).toHaveAttribute("data-disabled");
    const file = new File(["audio"], "reference.wav", { type: "audio/wav" });
    fireEvent.change(screen.getByLabelText("Reference audio track"), {
      target: { files: [file] },
    });
    expect(onReferenceUpload).toHaveBeenCalledWith(file);
  });

  it("shows a selected reference and removes it", () => {
    const onReferenceClear = vi.fn();
    render(
      <MasteringSection
        manifest={defaultManifest}
        setManifest={vi.fn()}
        configuration={null}
        masteringReference={{
          id: "reference-1",
          filename: "reference.flac",
          size_bytes: 2048,
          duration_seconds: 30,
          sample_rate: 48000,
          channels: 2,
        }}
        referenceUploading={false}
        referenceError={null}
        onReferenceUpload={vi.fn()}
        onReferenceClear={onReferenceClear}
      />,
    );

    expect(screen.getByText("reference.flac")).toBeInTheDocument();
    expect(screen.getAllByRole("slider")[0]).not.toHaveAttribute(
      "data-disabled",
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onReferenceClear).toHaveBeenCalledOnce();
  });

  it("uses the header switch as the effect's power button", () => {
    const setManifest = vi.fn();
    render(
      <MasteringSection
        manifest={defaultManifest}
        setManifest={setManifest}
        configuration={null}
        masteringReference={null}
        referenceUploading={false}
        referenceError={null}
        onReferenceUpload={vi.fn()}
        onReferenceClear={vi.fn()}
      />,
    );

    // The compressor ships with a profile, so its switch reads as on.
    const compressor = screen.getByRole("switch", { name: "Bus compressor" });
    expect(compressor).toBeChecked();
    fireEvent.click(compressor);
    const [patch] = setManifest.mock.calls.at(-1)!;
    expect(patch.mastering.compressor.profile).toBeNull();
  });

  it("restores the previous profile when an effect is switched back on", () => {
    const setManifest = vi.fn();
    const { rerender } = render(
      <MasteringSection
        manifest={defaultManifest}
        setManifest={setManifest}
        configuration={configuration}
        masteringReference={null}
        referenceUploading={false}
        referenceError={null}
        onReferenceUpload={vi.fn()}
        onReferenceClear={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("switch", { name: "Bus compressor" }));
    const off = setManifest.mock.calls.at(-1)![0];
    rerender(
      <MasteringSection
        manifest={off}
        setManifest={setManifest}
        configuration={configuration}
        masteringReference={null}
        referenceUploading={false}
        referenceError={null}
        onReferenceUpload={vi.fn()}
        onReferenceClear={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("switch", { name: "Bus compressor" }));
    const back = setManifest.mock.calls.at(-1)![0];
    expect(back.mastering.compressor.profile).toBe(
      defaultManifest.mastering.compressor.profile,
    );
  });
});
