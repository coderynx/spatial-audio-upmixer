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

describe("MasteringSection bass controls", () => {
  const withLayout = (channels: Record<string, string[]>) => ({
    ...configuration,
    choices: {
      ...configuration.choices,
      bass_profiles: ["deep", "cinema"],
      bass_spreads: ["front", "bed", "all"],
      bass_lfe_modes: ["off", "add", "split"],
      layout_channels: channels,
    },
  });

  const LAYOUTS = {
    stereo: ["FL", "FR"],
    "5.0": ["FL", "FR", "C", "SL", "SR"],
    "7.1.4": ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR"],
  };

  const renderAt = (layout: string, profile: string | null = "deep") =>
    render(
      <MasteringSection
        manifest={{
          ...defaultManifest,
          mixing: { ...defaultManifest.mixing, channel_layout: layout },
          mastering: {
            ...defaultManifest.mastering,
            bass: { ...defaultManifest.mastering.bass, profile },
          },
        }}
        setManifest={vi.fn()}
        configuration={withLayout(LAYOUTS) as Configuration}
        masteringReference={null}
        referenceUploading={false}
        referenceError={null}
        onReferenceUpload={vi.fn()}
        onReferenceClear={vi.fn()}
      />,
    );

  // `SelectField` renders a bare <Label>, so the select is reached through
  // its wrapper rather than by label association. Pots carry `aria-label`.
  const select = (label: string) =>
    screen.getByText(label).parentElement!.querySelector("select")!;

  it("names the profile in plain language rather than by its manifest value", () => {
    renderAt("7.1.4");
    // "deep" is the stored value; "Deep / Bass from every speaker" is the
    // documentation, carried on the option itself.
    expect(screen.getAllByText("Deep").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Bass from every speaker").length).toBeGreaterThan(0);
  });

  it("hides spread and the subwoofer on a layout with nowhere to place bass", () => {
    // Stereo has two bed channels and no LFE, so redistribution has nothing
    // to act on. Showing those dimmed is what made the panel unreadable.
    renderAt("stereo");
    expect(screen.queryByText("Spread")).toBeNull();
    expect(screen.queryByText("Subwoofer")).toBeNull();
    // Width still applies: decorrelating FL against FR is the stereo case the
    // stage exists for, so the group itself stays.
    expect(screen.getByText("Placement")).toBeInTheDocument();
    expect(screen.getByLabelText("Width")).toBeInTheDocument();
  });

  it("shows spread but not the subwoofer on a layout with no LFE", () => {
    renderAt("5.0");
    expect(screen.getByText("Placement")).toBeInTheDocument();
    expect(select("Spread")).toBeInTheDocument();
    expect(screen.queryByText("Subwoofer")).toBeNull();
    expect(screen.queryByLabelText("Sub level")).toBeNull();
  });

  it("shows every placement control on a layout that has an LFE", () => {
    renderAt("7.1.4");
    expect(select("Spread")).toBeInTheDocument();
    expect(select("Subwoofer")).toBeInTheDocument();
    expect(screen.getByLabelText("Sub level")).toBeInTheDocument();
    expect(screen.getByLabelText("Sub trim")).toBeInTheDocument();
  });

  it("keeps the tone controls on every layout", () => {
    renderAt("stereo");
    expect(screen.getByText("Tone")).toBeInTheDocument();
    for (const label of ["Sub gain", "Mid-bass", "Crossover", "Punch"]) {
      expect(screen.getByLabelText(label)).not.toHaveAttribute("data-disabled");
    }
  });

  it("disables the body while the effect is switched off", () => {
    renderAt("7.1.4", null);
    expect(screen.getByLabelText("Sub gain")).toHaveAttribute("data-disabled");
    expect(select("Spread")).toBeDisabled();
  });
});
