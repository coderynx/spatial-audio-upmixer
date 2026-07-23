import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Job } from "@/api";
import { JobsPage } from "./JobsPage";

const job: Job = {
  id: "job-1",
  import_id: "import-1",
  source_job_id: null,
  name: "Album master",
  status: "running",
  progress: 0.5,
  status_message: "Rendering",
  manifest: { mixing: { channel_layout: "7.1.4" }, engine: { mode: "stem" } },
  error: null,
  created_at: "2026-01-01T12:00:00Z",
  started_at: null,
  finished_at: null,
  updated_at: "2026-01-01T12:01:00Z",
  tracks: [],
  artifacts: [],
  mastering_reference: null,
};

describe("JobsPage", () => {
  it("shows an actionable operational queue", () => {
    const onAction = vi.fn();
    render(
      <JobsPage
        jobs={[job]}
        loading={false}
        error={null}
        onAction={onAction}
        onRemix={vi.fn()}
        onCreate={vi.fn()}
      />,
    );
    expect(screen.getAllByText("Album master").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7.1.4").length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByLabelText("Pause job")[0]);
    expect(onAction).toHaveBeenCalledWith("pause", job);
  });

  it("shows an empty-state job action", () => {
    const onCreate = vi.fn();
    render(
      <JobsPage
        jobs={[]}
        loading={false}
        error={null}
        onAction={vi.fn()}
        onRemix={vi.fn()}
        onCreate={onCreate}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create job" }));
    expect(onCreate).toHaveBeenCalledOnce();
  });
});
