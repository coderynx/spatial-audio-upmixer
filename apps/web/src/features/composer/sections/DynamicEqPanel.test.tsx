import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DynamicEqPanel } from "./DynamicEqPanel";

const PROFILES = ["clear-low-mid", "immersive-polish", "tame-harshness"];

function renderPanel(profile: string | null, profiles = PROFILES) {
  const onChange = vi.fn();
  render(<DynamicEqPanel profile={profile} profiles={profiles} onChange={onChange} />);
  return onChange;
}

describe("DynamicEqPanel", () => {
  it("ships off, with no profile selected", () => {
    renderPanel(null);
    expect(screen.getByRole("switch", { name: "Dynamic EQ" })).not.toBeChecked();
    expect(screen.getByLabelText("Profile")).toBeDisabled();
  });

  it("switching on takes the first served profile", () => {
    const onChange = renderPanel(null);
    fireEvent.click(screen.getByRole("switch", { name: "Dynamic EQ" }));
    expect(onChange).toHaveBeenCalledWith("clear-low-mid");
  });

  it("switching off clears the profile and remembers it for the way back", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <DynamicEqPanel profile="tame-harshness" profiles={PROFILES} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("switch", { name: "Dynamic EQ" }));
    expect(onChange).toHaveBeenCalledWith(null);

    rerender(<DynamicEqPanel profile={null} profiles={PROFILES} onChange={onChange} />);
    fireEvent.click(screen.getByRole("switch", { name: "Dynamic EQ" }));
    expect(onChange).toHaveBeenLastCalledWith("tame-harshness");
  });

  it("offers every served profile and says what it is for", () => {
    renderPanel("tame-harshness");
    const select = screen.getByLabelText("Profile");
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "Clear Low Mid", "Immersive Polish", "Tame Harshness",
    ]);
    expect(screen.getByText(/3-4 kHz glare/)).toBeInTheDocument();
  });

  it("cannot be switched on before the profile list lands", () => {
    render(<DynamicEqPanel profile={null} profiles={undefined} onChange={vi.fn()} />);
    expect(screen.getByRole("switch", { name: "Dynamic EQ" })).toBeDisabled();
  });
});
