import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TimelineView } from "./TimelineView";

function renderTimeline(overrides: Partial<React.ComponentProps<typeof TimelineView>> = {}) {
  const handlers = {
    onSelectStem: vi.fn(),
    onToggleMute: vi.fn(),
    onToggleSolo: vi.fn(),
    onGain: vi.fn(),
    onDragStart: vi.fn(),
    onDragEnd: vi.fn(),
    onDropOn: vi.fn(),
    onBeginScrub: vi.fn(),
    onScrubTo: vi.fn(),
    onCommitScrub: vi.fn(),
  };
  render(
    <TimelineView
      stems={["Vocals", "Drums"]}
      peaks={null}
      loading={false}
      pending={false}
      mutedStems={[]}
      enabled={{}}
      solo={[]}
      gains={{}}
      stemLevels={{ current: new Map() }}
      stemChannelCounts={{}}
      draggedStem={null}
      selectedStem={null}
      duration={30}
      currentTimeRef={{ current: 0 }}
      playing={false}
      disabled={false}
      {...handlers}
      {...overrides}
    />,
  );
  return handlers;
}

describe("TimelineView stem lanes", () => {
  it("mirrors the stem rail: grip, mute and solo buttons per lane", () => {
    renderTimeline();

    expect(screen.getAllByLabelText(/^Mute /)).toHaveLength(2);
    expect(screen.getAllByLabelText(/^Solo /)).toHaveLength(2);
  });

  it("toggles mute without selecting the lane", () => {
    const { onToggleMute, onSelectStem } = renderTimeline();

    fireEvent.click(screen.getByRole("button", { name: "Mute Vocals" }));

    expect(onToggleMute).toHaveBeenCalledWith("Vocals");
    expect(onSelectStem).not.toHaveBeenCalled();
  });

  it("toggles solo without selecting the lane", () => {
    const { onToggleSolo, onSelectStem } = renderTimeline();

    fireEvent.click(screen.getByRole("button", { name: "Solo Drums" }));

    expect(onToggleSolo).toHaveBeenCalledWith("Drums");
    expect(onSelectStem).not.toHaveBeenCalled();
  });

  it("reflects explicit mute/solo state independent of implicit dimming", () => {
    renderTimeline({ enabled: { Vocals: false }, solo: ["Drums"], mutedStems: ["Vocals"] });

    expect(screen.getByRole("button", { name: "Enable Vocals" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Clear solo Drums" })).toHaveAttribute("aria-pressed", "true");
  });

  it("selects the lane on click", () => {
    const { onSelectStem } = renderTimeline();

    fireEvent.click(screen.getByTitle("Vocals"));

    expect(onSelectStem).toHaveBeenCalledWith("Vocals");
  });

  it("drives the shared reorder drag sequence, same contract as the stem rail", () => {
    const { onDragStart, onDropOn } = renderTimeline();
    const dataTransfer = { effectAllowed: "", dropEffect: "" };

    // Drag only starts from the grip handle now — starting it anywhere else
    // in the row would hijack a click-drag on the fader (see
    // `horizontal-fader.tsx`'s own comment on this). Native `dragstart` fires
    // with `event.target` set to the draggable row itself, never the
    // descendant the pointer actually landed on, so the row's own handler
    // reads that decision off `mousedown` instead — fired here on the handle
    // first, then `dragstart` on the row, matching the real browser sequence.
    const row = screen.getByTitle("Vocals");
    const handle = row.querySelector("[data-drag-handle]")!;
    fireEvent.mouseDown(handle);
    fireEvent.dragStart(row, { dataTransfer });
    expect(onDragStart).toHaveBeenCalledWith("Vocals");

    fireEvent.dragOver(screen.getByTitle("Drums"), { dataTransfer });
    fireEvent.drop(screen.getByTitle("Drums"), { dataTransfer });
    expect(onDropOn).toHaveBeenCalledWith("Drums");
  });

  it("does not start a reorder drag from anywhere else in the row", () => {
    const { onDragStart } = renderTimeline();
    const dataTransfer = { effectAllowed: "", dropEffect: "" };

    const row = screen.getByTitle("Vocals");
    fireEvent.mouseDown(row);
    fireEvent.dragStart(row, { dataTransfer });

    expect(onDragStart).not.toHaveBeenCalled();
  });

  it("dims the lane while it is being dragged", () => {
    renderTimeline({ draggedStem: "Vocals" });

    expect(screen.getByTitle("Vocals").className).toMatch(/opacity-40/);
  });
});
