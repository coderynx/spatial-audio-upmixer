import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Fader } from "./fader";

function renderFader(overrides: Partial<React.ComponentProps<typeof Fader>> = {}) {
  const onChange = vi.fn();
  render(
    <Fader
      label="Vocals gain"
      value={0}
      min={-12}
      max={6}
      step={0.1}
      detent={0}
      valueText="0.0 dB"
      onChange={onChange}
      {...overrides}
    />,
  );
  return { onChange, fader: screen.getByRole("slider", { name: "Vocals gain" }) };
}

describe("Fader", () => {
  it("exposes a vertical slider with the formatted readout as its value text", () => {
    const { fader } = renderFader({ value: -6, valueText: "-6.0 dB" });

    expect(fader).toHaveAttribute("aria-orientation", "vertical");
    expect(fader).toHaveAttribute("aria-valuemin", "-12");
    expect(fader).toHaveAttribute("aria-valuemax", "6");
    expect(fader).toHaveAttribute("aria-valuenow", "-6");
    expect(fader).toHaveAttribute("aria-valuetext", "-6.0 dB");
  });

  it("steps with the arrow keys and jumps ten steps with page keys", () => {
    const { onChange, fader } = renderFader({ value: 0 });

    fireEvent.keyDown(fader, { key: "ArrowUp" });
    expect(onChange).toHaveBeenLastCalledWith(0.1);

    fireEvent.keyDown(fader, { key: "ArrowDown" });
    expect(onChange).toHaveBeenLastCalledWith(-0.1);

    fireEvent.keyDown(fader, { key: "PageUp" });
    expect(onChange).toHaveBeenLastCalledWith(1);
  });

  it("clamps Home and End to the range ends", () => {
    const { onChange, fader } = renderFader({ value: 0 });

    fireEvent.keyDown(fader, { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith(-12);

    fireEvent.keyDown(fader, { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith(6);
  });

  it("restores the rest value on double-click", () => {
    const onReset = vi.fn();
    renderFader({ value: -4, onReset });

    fireEvent.doubleClick(screen.getByRole("slider", { name: "Vocals gain" }));
    expect(onReset).toHaveBeenCalled();
  });

  it("ignores keys and takes itself out of the tab order when disabled", () => {
    const { onChange, fader } = renderFader({ disabled: true });

    fireEvent.keyDown(fader, { key: "ArrowUp" });
    expect(onChange).not.toHaveBeenCalled();
    expect(fader).toHaveAttribute("tabindex", "-1");
  });
});
