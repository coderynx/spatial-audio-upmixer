import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Pot } from "./pot";

function renderPot(overrides: Partial<React.ComponentProps<typeof Pot>> = {}) {
  const onChange = vi.fn();
  render(
    <Pot
      label="Threshold"
      value={-18}
      min={-40}
      max={0}
      step={0.5}
      suffix=" dB"
      onChange={onChange}
      {...overrides}
    />,
  );
  return { onChange, dial: screen.getByRole("slider", { name: overrides.label ?? "Threshold" }) };
}

describe("Pot", () => {
  it("exposes the value through slider semantics", () => {
    const { dial } = renderPot();
    expect(dial).toHaveAttribute("aria-valuenow", "-18");
    expect(dial).toHaveAttribute("aria-valuemin", "-40");
    expect(dial).toHaveAttribute("aria-valuemax", "0");
    expect(dial).toHaveAttribute("aria-valuetext", "-18.0 dB");
  });

  it("steps with the arrow keys and jumps with page keys", () => {
    const { onChange, dial } = renderPot();
    fireEvent.keyDown(dial, { key: "ArrowUp" });
    expect(onChange).toHaveBeenCalledWith(-17.5);
    fireEvent.keyDown(dial, { key: "PageDown" });
    expect(onChange).toHaveBeenCalledWith(-23);
  });

  it("clamps at both bounds", () => {
    const { onChange, dial } = renderPot({ value: 0 });
    fireEvent.keyDown(dial, { key: "ArrowUp" });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.keyDown(dial, { key: "Home" });
    expect(onChange).toHaveBeenCalledWith(-40);
  });

  it("drags vertically, with shift for fine adjustment", () => {
    const { onChange, dial } = renderPot();
    dial.setPointerCapture = vi.fn();
    dial.releasePointerCapture = vi.fn();
    fireEvent.pointerDown(dial, { button: 0, clientY: 200, pointerId: 1 });
    // 160px of travel covers the full 40dB range, so 40px up is +10dB.
    fireEvent.pointerMove(dial, { clientY: 160, pointerId: 1 });
    expect(onChange).toHaveBeenLastCalledWith(-8);
    fireEvent.pointerMove(dial, { clientY: 160, pointerId: 1, shiftKey: true });
    expect(onChange).toHaveBeenLastCalledWith(-15.5);
    // Dragging past the top of the range pins to the maximum.
    fireEvent.pointerMove(dial, { clientY: 0, pointerId: 1 });
    expect(onChange).toHaveBeenLastCalledWith(0);
    fireEvent.pointerUp(dial, { pointerId: 1 });
  });

  it("fills from the left when the range is unipolar", () => {
    const { dial } = renderPot({ label: "Ratio", value: 1, min: 1, max: 10, step: 0.1 });
    // At the minimum a left-origin pot has no value arc at all: only the
    // track, the cap and the pointer are drawn.
    expect(dial.querySelectorAll("path")).toHaveLength(1);
  });

  it("fills from the centre when the range spans zero", () => {
    const { dial } = renderPot({ label: "Sub gain", value: 0, min: -12, max: 12, step: 0.1 });
    // Zero is the origin for a bipolar pot, so again no value arc is drawn.
    expect(dial.querySelectorAll("path")).toHaveLength(1);
    const [, valueArc] = renderBipolarAt(6);
    expect(valueArc).toBeTruthy();
  });

  it("adjusts on wheel only while focused, so panels stay scrollable", () => {
    const { onChange, dial } = renderPot();
    const unfocused = new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -100 });
    dial.dispatchEvent(unfocused);
    expect(onChange).not.toHaveBeenCalled();
    expect(unfocused.defaultPrevented).toBe(false);

    dial.focus();
    const focused = new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -100 });
    dial.dispatchEvent(focused);
    expect(onChange).toHaveBeenCalledWith(-17.5);
    expect(focused.defaultPrevented).toBe(true);
  });

  it("calls onReset on double-click", () => {
    const onReset = vi.fn();
    const { dial } = renderPot({ onReset });
    fireEvent.doubleClick(dial);
    expect(onReset).toHaveBeenCalled();
  });

  it("marks an inherited value and hands over on the first edit", () => {
    const onChange = vi.fn();
    render(
      <Pot
        label="Makeup gain"
        value={0}
        min={0}
        max={12}
        step={0.5}
        onChange={onChange}
        inherited
        inheritedHint="from profile"
      />,
    );
    const dial = screen.getByRole("slider", { name: "Makeup gain" });
    expect(dial).toHaveAttribute("data-inherited");
    expect(dial).toHaveAttribute("aria-valuetext", "0.0 (from profile)");
    fireEvent.keyDown(dial, { key: "ArrowUp" });
    expect(onChange).toHaveBeenCalledWith(0.5);
  });

  it("ignores input while disabled", () => {
    const { onChange, dial } = renderPot({ disabled: true });
    fireEvent.keyDown(dial, { key: "ArrowUp" });
    expect(onChange).not.toHaveBeenCalled();
    expect(dial).toHaveAttribute("data-disabled");
    expect(dial).toHaveAttribute("tabindex", "-1");
  });
});

function renderBipolarAt(value: number) {
  const { unmount } = render(
    <Pot label="Mid-bass gain" value={value} min={-12} max={12} step={0.1} onChange={() => {}} />,
  );
  const dial = screen.getByRole("slider", { name: "Mid-bass gain" });
  const paths = Array.from(dial.querySelectorAll("path"));
  unmount();
  return paths;
}
