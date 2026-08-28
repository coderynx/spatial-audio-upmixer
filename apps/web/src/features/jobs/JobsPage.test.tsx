import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Job } from "@/api";
import { HeaderSlotProvider } from "@/app/HeaderSlot";
import { JobsPage } from "./JobsPage";

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    import_id: "import-1",
    source_job_id: null,
    name: "Album master",
    status: "running",
    progress: 0.5,
    status_message: "Rendering",
    manifest: {
      mixing: { channel_layout: "7.1.4" },
      engine: { mode: "stem" },
      format: { type: "multichannel", codec: "wav_pcm" },
    },
    error: null,
    created_at: "2026-01-01T12:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-01-01T12:01:00Z",
    tracks: [],
    artifacts: [],
    mastering_reference: null,
    ...overrides,
  };
}

function renderPage(jobs: Job[]) {
  const onAction = vi.fn();
  const onCreate = vi.fn();
  render(
    <HeaderSlotProvider>
      <JobsPage
        jobs={jobs}
        loading={false}
        error={null}
        onAction={onAction}
        onRemix={vi.fn()}
        onCreate={onCreate}
      />
    </HeaderSlotProvider>,
  );
  return { onAction, onCreate };
}

describe("JobsPage", () => {
  it("shows an actionable operational queue", () => {
    const job = makeJob();
    const { onAction } = renderPage([job]);
    expect(screen.getAllByText("Album master").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7.1.4").length).toBeGreaterThan(0);
    expect(screen.getByText("Multichannel WAV")).toBeInTheDocument();
    fireEvent.click(screen.getAllByLabelText("Pause job")[0]);
    expect(onAction).toHaveBeenCalledWith("pause", job);
  });

  it.each([
    ["binaural", "Binaural"],
    ["transaural", "Transaural"],
    ["adm-bwf", "ADM-BWF"],
  ])("shows %s as %s", (type, label) => {
    renderPage([makeJob({ manifest: { format: { type } } })]);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("uses the resolved delivery format", () => {
    renderPage([
      makeJob({
        delivery_formats: [{ type: "adm-bwf", codec: "wav_pcm" }],
      }),
    ]);
    expect(screen.getByText("ADM-BWF")).toBeInTheDocument();
    expect(screen.queryByText("Multichannel WAV")).not.toBeInTheDocument();
  });

  it("shows an empty-state job action", () => {
    const { onCreate } = renderPage([]);
    fireEvent.click(screen.getByRole("button", { name: "Create job" }));
    expect(onCreate).toHaveBeenCalledOnce();
  });

  it("filters the queue by status facet", () => {
    renderPage([makeJob(), makeJob({ id: "job-2", name: "Single master", status: "completed" })]);

    expect(screen.getAllByText("Single master").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Status: running (1)" }));

    expect(screen.queryByText("Single master")).not.toBeInTheDocument();
    expect(screen.getAllByText("Album master").length).toBeGreaterThan(0);
  });
});
