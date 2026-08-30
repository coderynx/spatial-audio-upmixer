import * as React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { StemSelectorGrid } from "./StemSelectorGrid";

function StemHarness() {
  const [stems, setStems] = React.useState(["Vocals"]);
  return (
    <>
      <StemSelectorGrid
        available={["Vocals", "Bass", "Drums", "Lead Vocals", "Backing Vocals"]}
        selected={stems}
        onChange={setStems}
      />
      <output data-testid="stems">{JSON.stringify(stems)}</output>
    </>
  );
}

describe("StemSelectorGrid", () => {
  it("uses parent and child targets exclusively", async () => {
    const user = userEvent.setup();
    render(<StemHarness />);

    await user.click(screen.getByRole("button", { name: "Toggle Vocals components" }));
    await user.click(screen.getByRole("button", { name: "Lead Vocals" }));
    expect(screen.getByTestId("stems")).toHaveTextContent('["Lead Vocals"]');

    await user.click(screen.getByRole("button", { name: "Vocals" }));
    expect(screen.getByTestId("stems")).toHaveTextContent('["Vocals"]');
  });
});
