import * as React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { defaultManifest } from "@/lib/manifest";
import { normalizeStemHierarchy, StemsSection } from "./StemsSection";

function StemHarness({
  stems = defaultManifest.engine.stems,
}: {
  stems?: string[];
}) {
  const [manifest, setManifest] = React.useState({
    ...defaultManifest,
    engine: { ...defaultManifest.engine, stems },
  });
  return (
    <>
      <StemsSection
        manifest={manifest}
        setManifest={setManifest}
        configuration={null}
      />
      <output data-testid="stems">
        {JSON.stringify(manifest.engine.stems)}
      </output>
    </>
  );
}

function selectedStems() {
  return JSON.parse(
    screen.getByTestId("stems").textContent || "[]",
  ) as string[];
}

describe("normalizeStemHierarchy", () => {
  it("keeps children when a saved manifest also contains their parent", () => {
    expect(
      normalizeStemHierarchy(["Vocals", "Lead Vocals", "Backing Vocals"]),
    ).toEqual(["Lead Vocals", "Backing Vocals"]);
    expect(normalizeStemHierarchy(["Drums", "Kick", "Snare"])).toEqual([
      "Kick",
      "Snare",
    ]);
  });
});

describe("StemsSection", () => {
  it("selects vocal children, then restores whole vocals", async () => {
    const user = userEvent.setup();
    render(<StemHarness />);

    await user.click(
      screen.getByRole("button", { name: "Toggle Vocals components" }),
    );
    await user.click(screen.getByRole("button", { name: "Lead Vocals" }));
    await user.click(screen.getByRole("button", { name: "Backing Vocals" }));
    expect(selectedStems()).toEqual([
      "Lead Vocals",
      "Backing Vocals",
      "Bass",
      "Drums",
      "Guitar",
      "Piano",
      "Other",
    ]);

    await user.click(screen.getByRole("button", { name: "Vocals" }));
    expect(selectedStems()).toEqual(defaultManifest.engine.stems);
  });

  it("allows individual refined children to be omitted", async () => {
    const user = userEvent.setup();
    render(<StemHarness />);

    await user.click(
      screen.getByRole("button", { name: "Toggle Vocals components" }),
    );
    await user.click(screen.getByRole("button", { name: "Lead Vocals" }));
    await user.click(screen.getByRole("button", { name: "Backing Vocals" }));
    await user.click(screen.getByRole("button", { name: "Backing Vocals" }));

    expect(selectedStems()).not.toContain("Vocals");
    expect(selectedStems()).toContain("Lead Vocals");
    expect(selectedStems()).not.toContain("Backing Vocals");
  });

  it("explains crowd-only output", async () => {
    const user = userEvent.setup();
    render(<StemHarness stems={[]} />);

    await user.click(screen.getByRole("button", { name: "Crowd" }));
    expect(screen.getByText(/This job keeps Crowd only/)).toBeInTheDocument();
  });
});
