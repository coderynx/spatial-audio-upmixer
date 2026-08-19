import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GainReductionMeters, LoudnessReadout } from "./LoudnessMeters";
import { SILENT_MASTER_METERS } from "./wasmEngine/meters";

const SILENT_PAIR = {
  current: {
    left: { rms: 0, peak: 0, clipped: false },
    right: { rms: 0, peak: 0, clipped: false },
  },
};

const LOUDNESS = {
  integratedLkfs: -17.4,
  truePeakDbtp: -1.2,
  targetLkfs: -18,
  ceilingDbtp: -1,
  bypassMatchDb: 3.25,
};

describe("LoudnessReadout", () => {
  it("shows the measured programme against its delivery target", () => {
    render(
      <LoudnessReadout
        loudness={LOUDNESS}
        masterMeters={{ current: SILENT_MASTER_METERS }}
        headphoneLevels={SILENT_PAIR}
        active={false}
        bypassed={false}
      />,
    );
    expect(screen.getByText("-17.4")).toBeTruthy();
    expect(screen.getByText("-1.2")).toBeTruthy();
    expect(screen.getByText("-18 / -1.0")).toBeTruthy();
    // PLR: -1.2 dBTP over -17.4 LKFS.
    expect(screen.getByText("16.2")).toBeTruthy();
    expect(screen.queryByText("A/B")).toBeNull();
  });

  it("names the monitor match only while the chain is bypassed", () => {
    render(
      <LoudnessReadout
        loudness={LOUDNESS}
        masterMeters={{ current: SILENT_MASTER_METERS }}
        headphoneLevels={SILENT_PAIR}
        active={false}
        bypassed
      />,
    );
    expect(screen.getByText("A/B")).toBeTruthy();
    expect(screen.getByText("+3.3")).toBeTruthy();
  });
});

describe("GainReductionMeters", () => {
  it("gives the LFE its own bar only where the layout has one", () => {
    const { rerender } = render(
      <GainReductionMeters
        masterMeters={{ current: SILENT_MASTER_METERS }}
        headphoneLevels={SILENT_PAIR}
        active={false}
        hasLfe
      />,
    );
    expect(screen.getAllByRole("meter")).toHaveLength(3);

    rerender(
      <GainReductionMeters
        masterMeters={{ current: SILENT_MASTER_METERS }}
        headphoneLevels={SILENT_PAIR}
        active={false}
        hasLfe={false}
      />,
    );
    expect(screen.getAllByRole("meter")).toHaveLength(2);
  });
});
