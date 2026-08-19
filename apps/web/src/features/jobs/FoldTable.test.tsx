import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FoldTable } from "./FoldTable";

const CLEAN = {
  nativeLkfs: -17.68,
  flagged: false,
  rows: [
    {
      key: "stereo" as const,
      label: "Stereo fold",
      lkfs: -18.95,
      tp_dbtp: -7.9,
      plr_db: 11.1,
      lkfs_delta_lu: -1.27,
      tp_compliant: true,
      loudness_divergent: false,
      flagged: false,
    },
  ],
};

describe("FoldTable", () => {
  it("shows each fold's delivered numbers against the bed", () => {
    render(<FoldTable folds={CLEAN} />);
    expect(screen.getByText("Stereo fold")).toBeTruthy();
    expect(screen.getByText("-18.9")).toBeTruthy();
    expect(screen.getByText("-1.27")).toBeTruthy();
    expect(screen.getByText("-7.9")).toBeTruthy();
    expect(screen.getByText(/-17.7 LKFS/)).toBeTruthy();
  });

  it("says a flagged fold is reported rather than corrected", () => {
    render(
      <FoldTable
        folds={{
          ...CLEAN,
          flagged: true,
          rows: [{ ...CLEAN.rows[0], tp_compliant: false, tp_dbtp: 3.55, flagged: true }],
        }}
      />,
    );
    expect(screen.getByText(/not corrected/)).toBeTruthy();
    expect(screen.getByTitle(/true-peak ceiling/)).toBeTruthy();
  });
});
