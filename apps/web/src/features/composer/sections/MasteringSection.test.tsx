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
    expect(screen.getByRole("slider", { name: "Max correction" })).not.toHaveAttribute(
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

  it("ships the head and the clipper off, and keeps their settings across the switch", () => {
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

    for (const [name, block] of [
      ["Subsonic filter", "highpass"],
      ["Soft clip", "clip"],
    ] as const) {
      const control = screen.getByRole("switch", { name });
      expect(control).not.toBeChecked();
      fireEvent.click(control);
      const [patch] = setManifest.mock.calls.at(-1)!;
      expect(patch.mastering[block]).toEqual({
        ...defaultManifest.mastering[block],
        enabled: true,
      });
    }
    expect(screen.getByRole("slider", { name: "Cutoff" })).toHaveAttribute("data-disabled");
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
  const renderBass = (bass: Partial<typeof defaultManifest.mastering.bass> = {}) => {
    const setManifest = vi.fn();
    render(
      <MasteringSection
        manifest={{
          ...defaultManifest,
          mastering: {
            ...defaultManifest.mastering,
            bass: { ...defaultManifest.mastering.bass, ...bass },
          },
        }}
        setManifest={setManifest}
        configuration={configuration}
        masteringReference={null}
        referenceUploading={false}
        referenceError={null}
        onReferenceUpload={vi.fn()}
        onReferenceClear={vi.fn()}
      />,
    );
    return setManifest;
  };

  it("shows only mix-facing bass controls", () => {
    renderBass();
    for (const label of ["Preset", "Low end", "Body", "Punch", "Harmonics"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    for (const label of ["Exciter", "Crossover", "Spread", "Width", "Subwoofer", "Sub level", "Sub trim"]) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
    expect(screen.getByRole("switch", { name: "Bass" })).not.toBeChecked();
  });

  it("turns the bass module on from the header", () => {
    const setManifest = renderBass();
    fireEvent.click(screen.getByRole("switch", { name: "Bass" }));
    expect(setManifest.mock.calls.at(-1)![0].mastering.bass.enabled).toBe(true);
  });

  it("toggles the bass module from its header and keeps tuned values", () => {
    const setManifest = renderBass({ enabled: true, sub_gain_db: 2 });
    const toggle = screen.getByRole("switch", { name: "Bass" });
    expect(toggle).toBeChecked();

    fireEvent.click(toggle);
    expect(setManifest.mock.calls.at(-1)![0].mastering.bass).toMatchObject({
      enabled: false,
      sub_gain_db: 2,
    });
  });

  it("disables the bass controls while the module is bypassed", () => {
    renderBass({ enabled: false, sub_gain_db: 2 });
    expect(screen.getByRole("switch", { name: "Bass" })).not.toBeChecked();
    expect(screen.getByRole("slider", { name: "Low end" })).toHaveAttribute("data-disabled");
  });

  it("preserves legacy routing until the first bass edit", () => {
    const setManifest = renderBass({ profile: "deep" });
    expect(screen.getByRole("status")).toHaveTextContent("Legacy bass routing is active");
    expect(setManifest).not.toHaveBeenCalled();

    fireEvent.keyDown(screen.getByRole("slider", { name: "Low end" }), { key: "ArrowUp" });
    const next = setManifest.mock.calls.at(-1)![0].mastering.bass;
    expect(next).toMatchObject({
      enabled: true,
      profile: null,
      sub_gain_db: 1.1,
      mid_gain_db: 0.5,
      punch: 0.25,
      harmonics: 1,
      unify_hz: 90,
      spread: "bed",
      excite: null,
      lfe_mode: "off",
      lfe_send: 0,
      lfe_gain_db: 0,
      decorrelate: 0,
    });
  });

  it("applies a preset as concrete safe values", () => {
    const setManifest = renderBass({ enabled: true });
    fireEvent.click(screen.getByRole("combobox", { name: "Preset" }));
    fireEvent.click(screen.getByRole("option", { name: /Enhance/ }));

    expect(setManifest.mock.calls.at(-1)![0].mastering.bass).toEqual({
      enabled: true,
      profile: null,
      sub_gain_db: 1.5,
      mid_gain_db: 0.5,
      unify_hz: 90,
      spread: "bed",
      punch: 0.2,
      harmonics: 1,
      excite: null,
      lfe_mode: "off",
      lfe_send: 0,
      lfe_gain_db: 0,
      decorrelate: 0,
    });
  });
});
