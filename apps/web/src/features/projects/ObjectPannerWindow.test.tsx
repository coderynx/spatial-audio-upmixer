import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { getStemColor } from "@/lib/stems";
import { ObjectPannerWindow, objectChannelPositions, placementFromPannerPosition } from "./ObjectPannerWindow";
import type { StemPlacement } from "./wasmEngine/panner";

const PLACEMENT: StemPlacement = { azimuth_deg: 0, elevation_deg: 0, width_deg: 0, object_size: 0.5 };

describe("ObjectPannerWindow", () => {
  it("moves horizontal and vertical placement from the panner controls", async () => {
    const user = userEvent.setup();
    const onPlacement = vi.fn();
    render(<ObjectPannerWindow stemName="Vocals" placement={PLACEMENT} maxElevationDeg={35} onPlacement={onPlacement} />);

    await user.click(screen.getByRole("button", { name: "Object panner" }));
    expect(screen.getByRole("dialog", { name: "Object panner" })).toHaveAttribute("aria-modal", "false");
    const title = screen.getByText("Vocals Panner");
    expect(title.previousElementSibling).toHaveStyle({ color: getStemColor("Vocals") });
    expect(screen.getByRole("dialog", { name: "Object panner" })).toHaveStyle({ borderColor: `${getStemColor("Vocals")}40` });
    expect(screen.getByLabelText("Move object panner window")).toHaveClass("cursor-default");
    expect(screen.getByLabelText("Move object panner window")).not.toHaveClass("bg-secondary");
    fireEvent.keyDown(screen.getByRole("group", { name: "Left/right and back/front" }), { key: "ArrowRight" });
    expect(onPlacement).toHaveBeenLastCalledWith(expect.objectContaining({ azimuth_deg: expect.any(Number) }));

    const elevationPanner = screen.getByRole("group", { name: "Elevation" });
    fireEvent.keyDown(elevationPanner, { key: "ArrowUp" });
    expect(onPlacement).toHaveBeenLastCalledWith(expect.objectContaining({ elevation_deg: 1 }));

    fireEvent.keyDown(elevationPanner, { key: "ArrowRight" });
    expect(onPlacement).toHaveBeenLastCalledWith(expect.objectContaining({ azimuth_deg: expect.any(Number) }));
  });

  it("shows the planar current position instead of button text or an icon", () => {
    const { container } = render(<ObjectPannerWindow stemName="Vocals" placement={{ ...PLACEMENT, azimuth_deg: 90 }} maxElevationDeg={35} onPlacement={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Object panner" })).toHaveClass("h-10", "w-10");
    expect(screen.getByRole("button", { name: "Object panner" })).toHaveTextContent("");
    const puck = container.querySelector("[data-panner-preview-puck]") as HTMLElement;
    expect(puck).toHaveStyle({ left: "0%" });
    expect(Number.parseFloat(puck.style.top)).toBeCloseTo(50, 9);
    expect(container.querySelectorAll("[data-panner-preview-channel]")).toHaveLength(0);
  });

  it("keeps the centre handle at its free Cartesian position", async () => {
    const user = userEvent.setup();
    const { container } = render(<ObjectPannerWindow stemName="Vocals" placement={PLACEMENT} maxElevationDeg={35} onPlacement={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Object panner" }));

    const panner = screen.getByRole("group", { name: "Left/right and back/front" });
    fireEvent.keyDown(panner, { key: "ArrowDown" });
    fireEvent.keyDown(panner, { key: "ArrowDown" });

    expect(container.ownerDocument.querySelector('[data-drag-handle="horizontal"]')).toHaveStyle({ top: "4%" });
  });

  it("does not jump to a stale placement echo during a drag", async () => {
    const user = userEvent.setup();
    const onPlacement = vi.fn();
    const { rerender } = render(<ObjectPannerWindow stemName="Vocals" placement={PLACEMENT} maxElevationDeg={35} onPlacement={onPlacement} />);
    await user.click(screen.getByRole("button", { name: "Object panner" }));

    const panner = screen.getByRole("group", { name: "Left/right and back/front" });
    let captured = false;
    Object.assign(panner, {
      setPointerCapture: () => { captured = true; },
      hasPointerCapture: () => captured,
      releasePointerCapture: () => { captured = false; },
      getBoundingClientRect: () => ({ left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100, x: 0, y: 0, toJSON: () => ({}) }),
    });
    fireEvent.pointerDown(panner, { pointerId: 1, button: 0, clientX: 20, clientY: 20 });
    const stalePlacement = onPlacement.mock.calls.at(-1)?.[0] as StemPlacement;
    fireEvent.pointerMove(panner, { pointerId: 1, clientX: 30, clientY: 30 });

    rerender(<ObjectPannerWindow stemName="Vocals" placement={stalePlacement} maxElevationDeg={35} onPlacement={onPlacement} />);

    expect(document.querySelector('[data-drag-handle="horizontal"]')).toHaveStyle({ left: "30%", top: "30%" });
    fireEvent.pointerUp(panner, { pointerId: 1 });
  });

  it("leaves controls beneath the modeless window interactive", async () => {
    const user = userEvent.setup();
    const underneath = vi.fn();
    render(<><button onClick={underneath}>Underneath</button><ObjectPannerWindow stemName="Vocals" placement={PLACEMENT} maxElevationDeg={35} onPlacement={vi.fn()} /></>);

    await user.click(screen.getByRole("button", { name: "Object panner" }));
    await user.click(screen.getByRole("button", { name: "Underneath" }));

    expect(underneath).toHaveBeenCalledOnce();
    expect(screen.getByRole("dialog", { name: "Object panner" })).toBeInTheDocument();
  });

  it("moves the floating window from its title bar", async () => {
    const user = userEvent.setup();
    render(<ObjectPannerWindow stemName="Vocals" placement={PLACEMENT} maxElevationDeg={35} onPlacement={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Object panner" }));

    const floatingWindow = screen.getByRole("dialog", { name: "Object panner" });
    vi.spyOn(floatingWindow, "getBoundingClientRect").mockReturnValue({
      left: 100, top: 80, right: 520, bottom: 680, width: 420, height: 600, x: 100, y: 80, toJSON: () => ({}),
    });
    fireEvent.keyDown(screen.getByLabelText("Move object panner window"), { key: "ArrowRight" });

    expect(floatingWindow).toHaveStyle({ left: "110px", top: "80px" });
  });

  it("keeps stereo width and spread when the centre handle moves", () => {
    expect(placementFromPannerPosition({ ...PLACEMENT, width_deg: 90, object_size: 0.5 }, { lateral: 0.75, depth: 0.25 })).toMatchObject({
      width_deg: 90,
      object_size: 0.5,
      elevation_deg: 0,
    });
  });

  it("updates width and size independently of the centre position", async () => {
    const user = userEvent.setup();
    const onPlacement = vi.fn();
    render(<ObjectPannerWindow stemName="Vocals" placement={PLACEMENT} maxElevationDeg={35} onPlacement={onPlacement} />);
    await user.click(screen.getByRole("button", { name: "Object panner" }));

    fireEvent.keyDown(screen.getByLabelText("Stereo spread"), { key: "ArrowRight" });
    expect(onPlacement).toHaveBeenLastCalledWith(expect.objectContaining({ width_deg: 1, object_size: 0.5, azimuth_deg: 0 }));

    fireEvent.keyDown(screen.getByLabelText("Object size"), { key: "ArrowRight" });
    expect(onPlacement).toHaveBeenLastCalledWith(expect.objectContaining({ width_deg: 0, object_size: 0.51, azimuth_deg: 0 }));
  });

  it("shows width only for linked stereo objects", async () => {
    const user = userEvent.setup();
    render(<ObjectPannerWindow stemName="Vocals" placement={PLACEMENT} maxElevationDeg={35} objectMode="mono" onPlacement={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Object panner" }));

    expect(screen.queryByLabelText("Stereo spread")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Object size")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-channel]")).toHaveLength(0);
  });

  it("resets pucks to Logic's default positions with Option-click", async () => {
    const user = userEvent.setup();
    const onPlacement = vi.fn();
    render(<ObjectPannerWindow stemName="Vocals" placement={{ ...PLACEMENT, azimuth_deg: 90, elevation_deg: 20 }} maxElevationDeg={35} onPlacement={onPlacement} />);
    await user.click(screen.getByRole("button", { name: "Object panner" }));

    fireEvent.pointerDown(screen.getByRole("group", { name: "Left/right and back/front" }), { altKey: true });
    const front = onPlacement.mock.calls.at(-1)?.[0] as StemPlacement;
    expect(front.azimuth_deg).toBeCloseTo(0, 9);
    expect(front.elevation_deg).toBe(20);

    fireEvent.pointerDown(screen.getByRole("group", { name: "Elevation" }), { altKey: true });
    expect(onPlacement).toHaveBeenLastCalledWith(expect.objectContaining({ elevation_deg: 0 }));
  });

  it("keeps direct-image and object send controls in the panner window", async () => {
    const user = userEvent.setup();
    const onObjectMode = vi.fn();
    const onRoute = vi.fn();
    const onAmbient = vi.fn();
    render(<ObjectPannerWindow stemName="Vocals" placement={PLACEMENT} maxElevationDeg={35}
      channels={["FL", "FR", "LFE", "SL", "SR", "TFL", "TFR"]} route={{ LFE: 0.5 }}
      ambientRear={0.5} ambientHeight={0.5} onPlacement={vi.fn()}
      onObjectMode={onObjectMode} onRoute={onRoute} onAmbient={onAmbient} />);
    await user.click(screen.getByRole("button", { name: "Object panner" }));

    fireEvent.change(screen.getByLabelText("Direct image"), { target: { value: "mono" } });
    fireEvent.keyDown(screen.getByLabelText("Ambience to rear"), { key: "ArrowRight" });
    fireEvent.keyDown(screen.getByLabelText("Ambience to height"), { key: "ArrowRight" });
    fireEvent.keyDown(screen.getByLabelText("LFE send"), { key: "ArrowRight" });

    expect(onObjectMode).toHaveBeenCalledWith("mono");
    expect(onAmbient).toHaveBeenCalledWith({ rear: 0.51 });
    expect(onAmbient).toHaveBeenCalledWith({ height: 0.51 });
    expect(onRoute).toHaveBeenCalledWith({ LFE: 0.51 });
  });

  it("places the L and R markers at the ends of the stereo image width", () => {
    const channels = objectChannelPositions({ ...PLACEMENT, width_deg: 60 });

    expect(channels.left.lateral).toBeCloseTo(0.25, 9);
    expect(channels.right.lateral).toBeCloseTo(0.75, 9);
    expect(channels.left.depth).toBeCloseTo(channels.right.depth, 9);
  });

  it("keeps the L and R markers at a freely placed centre's radius", () => {
    const channels = objectChannelPositions(
      { ...PLACEMENT, width_deg: 60 },
      { lateral: 0.5, depth: 0.4 },
    );

    expect(Math.hypot(channels.left.lateral - 0.5, channels.left.depth - 0.5)).toBeCloseTo(0.1, 9);
    expect(Math.hypot(channels.right.lateral - 0.5, channels.right.depth - 0.5)).toBeCloseTo(0.1, 9);
  });
});
