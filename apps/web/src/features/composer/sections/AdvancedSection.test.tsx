import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AdvancedSection } from "./AdvancedSection";

describe("AdvancedSection", () => {
  it("exposes JSON validation errors and changes", () => {
    const onChange = vi.fn();
    render(
      <AdvancedSection
        rawManifest="{"
        rawError="Invalid JSON"
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText("Complete job manifest"), {
      target: { value: "{}" },
    });
    expect(onChange).toHaveBeenCalledWith("{}");
    expect(screen.getByText("Invalid JSON")).toBeInTheDocument();
  });
});
