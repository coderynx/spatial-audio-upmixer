import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { KEY_COMMANDS } from "./keyCommands";
import { KeyCommandsDialog } from "./KeyCommandsDialog";

describe("KeyCommandsDialog", () => {
  it("renders one row per table entry, so a new binding can't be added without appearing here", () => {
    render(<KeyCommandsDialog open onOpenChange={vi.fn()} />);
    expect(screen.getAllByRole("definition")).toHaveLength(KEY_COMMANDS.length);
  });

  it("renders the three groups in Transport, Mixer, Help order", () => {
    render(<KeyCommandsDialog open onOpenChange={vi.fn()} />);
    const headings = screen.getAllByRole("heading", { level: 3 }).map((node) => node.textContent);
    expect(headings).toEqual(["Transport", "Mixer", "Help"]);
  });

  it("renders macOS glyphs with a spoken word, and plain words when mac is false", () => {
    render(<KeyCommandsDialog open onOpenChange={vi.fn()} mac />);
    const macRow = screen.getByText("Clear/Recall Solo").closest("div");
    expect(macRow?.textContent).toContain("⌥");
    expect(macRow?.querySelector(".sr-only")).toHaveTextContent("Option");

    render(<KeyCommandsDialog open onOpenChange={vi.fn()} mac={false} />);
    const otherRow = screen.getAllByText("Clear/Recall Solo")[1].closest("div");
    expect(otherRow?.textContent).toContain("Alt");
  });

  it("marks numpad rows with a keypad note", () => {
    render(<KeyCommandsDialog open onOpenChange={vi.fn()} />);
    expect(screen.getByText("Play").parentElement?.textContent).toContain("keypad");
    expect(screen.getByText("Toggle Cycle Mode").parentElement?.textContent).not.toContain("keypad");
  });

  it("opens the dialog from the status-bar trigger", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<KeyCommandsDialog open={false} onOpenChange={onOpenChange} />);
    const trigger = screen.getByRole("button", { name: "Keyboard shortcuts" });
    await user.click(trigger);
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });
});
