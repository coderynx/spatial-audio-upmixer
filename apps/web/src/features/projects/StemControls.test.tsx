import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StemControls, StemProcessingControls, azimuthFromPosition, positionFromAzimuth } from "./StemControls";
import type { StemPlacement } from "./wasmEngine/panner";

const FULL = ["FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR", "TFL", "TFR", "TBL", "TBR"];

/** A wide front placement, the shape a preset hands a guitar. */
const WIDE: StemPlacement = {
  azimuth_deg: 0, elevation_deg: 0, width_deg: 128, object_size: 0.5,
};

function renderControls(props: Partial<React.ComponentProps<typeof StemControls>> = {}) {
  const onPlacement = vi.fn();
  const onAmbient = vi.fn();
  const onDynamics = vi.fn();
  render(
    <StemControls
      placement={WIDE}
      route={{}}
      channels={FULL}
      eq=""
      maxElevationDeg={35}
      ambientRear={0}
      ambientHeight={0}
      ambientHeightCrossoverHz={2000}
      onPlacement={onPlacement}
      onRoute={vi.fn()}
      onEq={vi.fn()}
      onDynamics={onDynamics}
      onAmbient={onAmbient}
      {...props}
    />,
  );
  return { onPlacement, onAmbient, onDynamics };
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
  it("stacks the processing chain and lets each active effect be disabled", () => {
    const onEq = vi.fn();
    const onDynamicEq = vi.fn();
    const onDynamics = vi.fn();
    render(<StemProcessingControls
      stemName="Vocals"
      eq="vocal-presence"
      onEq={onEq}
      dynamicEq={{ enabled: true, profile: null, bands: [], mix: 100 }}
      onDynamicEq={onDynamicEq}
      dynamics={{ enabled: true, profile: null, threshold_db: -18, ratio: 1.5, attack_ms: 30, release_ms: 250, mix: 100 }}
      onDynamics={onDynamics}
    />);

    const eq = screen.getByRole("button", { name: "Open stem EQ" });
    const tame = screen.getByRole("button", { name: "Open tame dynamic EQ" });
    const dynamics = screen.getByRole("button", { name: "Open gentle dynamics" });
    expect(eq).toHaveClass("bg-primary");
    expect(tame).toHaveClass("bg-primary");
    expect(dynamics).toHaveClass("bg-primary");
    expect(eq.compareDocumentPosition(tame) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(tame.compareDocumentPosition(dynamics) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Disable EQ" }));
    fireEvent.click(screen.getByRole("button", { name: "Disable Tame" }));
    fireEvent.click(screen.getByRole("button", { name: "Disable Dynamics" }));
    expect(onEq).toHaveBeenLastCalledWith(expect.objectContaining({ bypass: true }));
    expect(onDynamicEq).toHaveBeenLastCalledWith(expect.objectContaining({ enabled: false }));
    expect(onDynamics).toHaveBeenLastCalledWith(expect.objectContaining({ enabled: false }));
  });

  it("enables a muted effect from its power button", () => {
    const onDynamicEq = vi.fn();
    render(<StemProcessingControls
      stemName="Vocals"
      eq=""
      onEq={vi.fn()}
      onDynamicEq={onDynamicEq}
      onDynamics={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Enable Tame" }));
    expect(onDynamicEq).toHaveBeenLastCalledWith(expect.objectContaining({ enabled: true }));
  });

  it("writes gentle dynamics through only its selected-stem handler", () => {
    const { onDynamics } = renderControls();
    fireEvent.click(screen.getByRole("button", { name: "Open gentle dynamics" }));
    fireEvent.click(screen.getByRole("switch", { name: "Dynamics enabled" }));
    expect(onDynamics).toHaveBeenLastCalledWith(expect.objectContaining({ enabled: true }));
  });

  it("limits every processor's presets to the selected stem", () => {
    renderControls({
      stemName: "Lead Vocals",
      stemEqProfiles: ["vocal-presence", "bass-warmth"],
      dynamicEqProfiles: { "vocal-sibilance": [], "bass-bloom": [] },
      dynamicsProfiles: {
        "vocal-control": { enabled: false, threshold_db: -20, ratio: 1.8, attack_ms: 15, release_ms: 180, mix: 100 },
        "bass-control": { enabled: false, threshold_db: -20, ratio: 2, attack_ms: 35, release_ms: 300, mix: 100 },
      },
      stemProcessingPresets: {
        eq: { "Lead Vocals": ["vocal-presence"] },
        dynamic_eq: { "Lead Vocals": ["vocal-sibilance"] },
        dynamics: { "Lead Vocals": ["vocal-control"] },
      },
    });

    for (const [button, toggle, select, preset] of [["Open stem EQ", "EQ enabled", "EQ preset", "vocal-presence"], ["Open tame dynamic EQ", "Tame enabled", "Tame preset", "vocal-sibilance"], ["Open gentle dynamics", "Dynamics enabled", "Dynamics preset", "vocal-control"]] as const) {
      fireEvent.click(screen.getByRole("button", { name: button }));
      expect(screen.getByRole("switch", { name: toggle })).toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: "Reset" })).not.toHaveLength(0);
      expect(Array.from((screen.getByLabelText(select) as HTMLSelectElement).options, (option) => option.value)).toEqual(["custom", preset]);
    }
  });

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
      expect(placement.object_size).toBe(WIDE.object_size);
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

  it("reads a preset's image width back onto the front/back slider", () => {
    renderControls({ placement: { ...WIDE, width_deg: 180 } });

    expect(screen.getByLabelText("Front to back")).toHaveAttribute("aria-valuenow", "0.5");
  });

  it("reads a rear placement as behind, however wide it is", () => {
    renderControls({ placement: { ...WIDE, azimuth_deg: 180, width_deg: 120 } });

    const value = screen.getByLabelText("Front to back").getAttribute("aria-valuenow");
    expect(Number(value)).toBeCloseTo(1 - 120 / 360, 9);
  });

  it("wraps the image outward over the slider's front half", () => {
    const { onPlacement } = renderControls({ placement: { ...WIDE, width_deg: 0 } });

    step("Front to back", 1);

    const next = onPlacement.mock.calls.at(-1)?.[0] as StemPlacement;
    expect(next.width_deg).toBeCloseTo(0.01 * 360, 9);
    expect(next.azimuth_deg).toBeCloseTo(0, 9);
  });

  it("turns the image around past the halfway point without moving it sideways", () => {
    const { onPlacement } = renderControls({ placement: { ...WIDE, width_deg: 180 } });

    step("Front to back", 1);

    const next = onPlacement.mock.calls.at(-1)?.[0] as StemPlacement;
    expect(Math.abs(next.azimuth_deg)).toBeCloseTo(180, 9);
    expect(next.width_deg).toBeCloseTo(0.49 * 360, 9);
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

describe("ambience sends", () => {
  it("reports the rear send as a fraction, from the value it was given", () => {
    const { onAmbient } = renderControls({ ambientRear: 0.5 });
    step("Ambience to rear", 1);
    expect(onAmbient).toHaveBeenLastCalledWith({ rear: 0.51 });
  });

  it("offers no height send on a layout without height speakers", () => {
    renderControls({ channels: ["FL", "FR", "C", "LFE", "SL", "SR"] });
    expect(screen.getByLabelText("Ambience to rear")).toBeInTheDocument();
    expect(screen.queryByLabelText("Ambience to height")).toBeNull();
    expect(screen.queryByLabelText("Height crossover")).toBeNull();
  });

  it("offers neither send on a stereo layout", () => {
    renderControls({ channels: ["FL", "FR"] });
    expect(screen.queryByLabelText("Ambience to rear")).toBeNull();
    expect(screen.queryByLabelText("Ambience to height")).toBeNull();
  });

  it("writes a logarithmic height crossover", () => {
    const { onAmbient } = renderControls({ ambientHeightCrossoverHz: 2000 });
    step("Height crossover", 1);
    expect(onAmbient).toHaveBeenLastCalledWith({ heightCrossoverHz: expect.any(Number) });
  });
});
