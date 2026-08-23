import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StemControls, azimuthFromPosition, positionFromAzimuth } from "./StemControls";
import type { StemPlacement } from "./wasmEngine/panner";

const FULL = ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"];

/** A wide front placement, the shape a preset hands a guitar. */
const WIDE: StemPlacement = {
  azimuth_deg: 0, elevation_deg: 0, width_deg: 128, spread_deg: 70,
};

function renderControls(props: Partial<React.ComponentProps<typeof StemControls>> = {}) {
  const onPlacement = vi.fn();
  render(
    <StemControls
      placement={WIDE}
      route={{}}
      channels={FULL}
      eq=""
      maxElevationDeg={35}
      onPlacement={onPlacement}
      onRoute={vi.fn()}
      onEq={vi.fn()}
      {...props}
    />,
  );
  return { onPlacement };
}

/** Radix sliders respond to keyboard stepping in jsdom, where pointer drags
 * need layout the test environment does not provide. */
function step(label: string, times: number, key: "ArrowRight" | "ArrowLeft" = "ArrowRight") {
  const slider = screen.getByLabelText(label);
  slider.focus();
  for (let index = 0; index < times; index += 1) fireEvent.keyDown(slider, { key });
}

describe("placement geometry", () => {
  it("maps the cardinal positions to the geometry convention", () => {
    expect(azimuthFromPosition({ lateral: 0.5, depth: 0 })).toBeCloseTo(0, 9);
    expect(azimuthFromPosition({ lateral: 0, depth: 0.5 })).toBeCloseTo(90, 9);
    expect(azimuthFromPosition({ lateral: 1, depth: 0.5 })).toBeCloseTo(-90, 9);
    expect(Math.abs(azimuthFromPosition({ lateral: 0.5, depth: 1 }))).toBeCloseTo(180, 9);
  });

  it("round-trips a position through the azimuth it names", () => {
    for (const azimuth of [0, 30, -30, 90, -90, 135, 180, -179]) {
      expect(azimuthFromPosition(positionFromAzimuth(azimuth))).toBeCloseTo(azimuth, 9);
    }
  });

  it("resolves dead centre to the front rather than dividing by zero", () => {
    expect(azimuthFromPosition({ lateral: 0.5, depth: 0.5 })).toBe(0);
  });
});

describe("StemControls", () => {
  it("rotates the azimuth when the left/right slider moves", () => {
    const { onPlacement } = renderControls();

    step("Left to right", 1);

    expect(onPlacement).toHaveBeenCalled();
    const next = onPlacement.mock.calls.at(-1)?.[0] as StemPlacement;
    expect(next.azimuth_deg).toBeLessThan(0);
  });

  it("keeps the preset's image width and spread while moving a stem", () => {
    const { onPlacement } = renderControls();

    step("Left to right", 3);

    for (const [placement] of onPlacement.mock.calls as [StemPlacement][]) {
      expect(placement.width_deg).toBe(WIDE.width_deg);
      expect(placement.spread_deg).toBe(WIDE.spread_deg);
      expect(placement.elevation_deg).toBe(WIDE.elevation_deg);
    }
  });

  it("moves left and right in opposite directions", () => {
    const { onPlacement } = renderControls();

    step("Left to right", 1, "ArrowLeft");
    const left = (onPlacement.mock.calls.at(-1)?.[0] as StemPlacement).azimuth_deg;
    step("Left to right", 2, "ArrowRight");
    const right = (onPlacement.mock.calls.at(-1)?.[0] as StemPlacement).azimuth_deg;

    expect(left).toBeGreaterThan(right);
  });

  it("scales the height slider by what the layout can reproduce", () => {
    const { onPlacement } = renderControls({ maxElevationDeg: 35 });

    step("Floor to height", 1);

    const next = onPlacement.mock.calls.at(-1)?.[0] as StemPlacement;
    expect(next.elevation_deg).toBeCloseTo(0.01 * 35, 9);
    expect(next.azimuth_deg).toBe(WIDE.azimuth_deg);
  });

  it("reads an elevated placement back onto the height slider", () => {
    renderControls({ placement: { ...WIDE, elevation_deg: 17.5 }, maxElevationDeg: 35 });

    expect(screen.getByLabelText("Floor to height")).toHaveAttribute("aria-valuenow", "0.5");
  });

  it("offers only the left/right control on a stereo layout", () => {
    renderControls({ channels: ["FL", "FR"] });

    expect(screen.getByLabelText("Left to right")).toBeInTheDocument();
    expect(screen.queryByLabelText("Front to back")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Floor to height")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("LFE send")).not.toBeInTheDocument();
  });

  it("hides the height control on a layout with no height pair", () => {
    renderControls({ channels: ["FL", "FR", "C", "LFE", "SL", "SR"] });

    expect(screen.getByLabelText("Front to back")).toBeInTheDocument();
    expect(screen.queryByLabelText("Floor to height")).not.toBeInTheDocument();
  });

  it("routes the LFE send through the gain table, not the placement", () => {
    const onRoute = vi.fn();
    const { onPlacement } = renderControls({ route: { LFE: 0.5 }, onRoute });

    step("LFE send", 1);

    expect(onRoute).toHaveBeenCalledWith({ LFE: expect.any(Number) });
    expect(onPlacement).not.toHaveBeenCalled();
  });
});
