import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultManifest } from "@/lib/manifest";
import { MasteringSection } from "./MasteringSection";

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
});
