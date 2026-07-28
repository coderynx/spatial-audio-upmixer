import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TimelineView } from "./TimelineView";

function renderTimeline(overrides: Partial<React.ComponentProps<typeof TimelineView>> = {}) {
  const handlers = {
    onSelectStem: vi.fn(),
    onToggleMute: vi.fn(),
    onToggleSolo: vi.fn(),
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
      draggedStem={null}
      selectedStem={null}
      duration={30}
      currentTime={0}
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

    fireEvent.dragStart(screen.getByTitle("Vocals"), { dataTransfer });
    expect(onDragStart).toHaveBeenCalledWith("Vocals");

    fireEvent.dragOver(screen.getByTitle("Drums"), { dataTransfer });
    fireEvent.drop(screen.getByTitle("Drums"), { dataTransfer });
    expect(onDropOn).toHaveBeenCalledWith("Drums");
  });

  it("dims the lane while it is being dragged", () => {
    renderTimeline({ draggedStem: "Vocals" });

    expect(screen.getByTitle("Vocals").className).toMatch(/opacity-40/);
  });
});
