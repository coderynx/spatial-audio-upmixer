import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Job } from "@/api";
import { JobComposer } from "./JobComposer";

describe("JobComposer", () => {
  it("opens a cache-backed remix", () => {
    const remix: Job = {
      id: "job-1",
      import_id: "import-1",
      source_job_id: null,
      name: "Source master",
      status: "completed",
      progress: 1,
      status_message: "Complete",
      manifest: {},
      error: null,
      created_at: "2026-01-01T12:00:00Z",
      started_at: null,
      finished_at: null,
      updated_at: "2026-01-01T12:01:00Z",
      tracks: [],
      artifacts: [],
      mastering_reference: null,
    };
    render(
      <JobComposer
        open
        onOpenChange={vi.fn()}
        remix={remix}
        configuration={null}
        onCreated={vi.fn()}
      />,
    );
    expect(screen.getByText("Stem cache connected")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Source master remix")).toBeInTheDocument();
  });
});
