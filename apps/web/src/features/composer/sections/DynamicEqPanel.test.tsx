import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DynamicEqBand } from "@/lib/manifest";
import { DynamicEqPanel } from "./DynamicEqPanel";

const BAND: DynamicEqBand = {
  freq_hz: 3800,
  q: 2,
  threshold_db: -30,
  ratio: 3,
  attack_ms: 10,
  release_ms: 150,
};

function renderPanel(bands: DynamicEqBand[], maxBands: number | undefined = 4) {
  const onChange = vi.fn();
  const view = render(
    <DynamicEqPanel bands={bands} maxBands={maxBands} onChange={onChange} />,
  );
  return { onChange, view };
}

describe("DynamicEqPanel", () => {
  it("ships off, with no bands and no controls", () => {
    renderPanel([]);
    expect(screen.getByRole("switch", { name: "Dynamic EQ" })).not.toBeChecked();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  });

  it("switching on starts one band", () => {
    const { onChange } = renderPanel([]);
    fireEvent.click(screen.getByRole("switch", { name: "Dynamic EQ" }));
    expect(onChange).toHaveBeenCalledWith([BAND]);
  });

  it("switching off keeps the bands for the way back on", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <DynamicEqPanel bands={[BAND]} maxBands={4} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("switch", { name: "Dynamic EQ" }));
    expect(onChange).toHaveBeenCalledWith([]);

    rerender(<DynamicEqPanel bands={[]} maxBands={4} onChange={onChange} />);
    fireEvent.click(screen.getByRole("switch", { name: "Dynamic EQ" }));
    expect(onChange).toHaveBeenLastCalledWith([BAND]);
  });

  it("edits one band without touching the others", () => {
    const other = { ...BAND, freq_hz: 220 };
    const { onChange } = renderPanel([BAND, other]);
    const [ratio] = screen.getAllByRole("slider", { name: "Ratio" });
    fireEvent.keyDown(ratio, { key: "ArrowRight" });
    const [bands] = onChange.mock.calls.at(-1)!;
    expect(bands[0].ratio).toBeGreaterThan(BAND.ratio);
    expect(bands[1]).toEqual(other);
  });

  it("removes the band it was asked to", () => {
    const other = { ...BAND, freq_hz: 220 };
    const { onChange } = renderPanel([BAND, other]);
    fireEvent.click(screen.getByRole("button", { name: /Remove band 1/ }));
    expect(onChange).toHaveBeenCalledWith([other]);
  });

  it("offers another band below the served cap", () => {
    const { onChange } = renderPanel([BAND, BAND, BAND]);
    fireEvent.click(screen.getByRole("button", { name: /Add band/ }));
    expect(onChange).toHaveBeenCalledWith([BAND, BAND, BAND, BAND]);
  });

  it("stops offering one at the cap", () => {
    renderPanel([BAND, BAND, BAND, BAND]);
    expect(screen.queryByRole("button", { name: /Add band/ })).not.toBeInTheDocument();
  });

  it("cannot be switched on before the constants land", () => {
    render(<DynamicEqPanel bands={[]} maxBands={undefined} onChange={vi.fn()} />);
    expect(screen.getByRole("switch", { name: "Dynamic EQ" })).toBeDisabled();
  });
});
