import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api, type Asset, type Project } from "@/api";
import { ProjectDetailPage } from "./ProjectDetailPage";

const asset: Asset = {
  id: "asset-1", position: 0, filename: "track.wav", relative_path: "track.wav",
  size_bytes: 1, title: "Track One", artist: null, album: null, release_date: null,
  track_number: null, duration_seconds: 30, sample_rate: 48000, channels: 2, audio_url: "/track.wav",
};

const project: Project = {
  id: "project-1", import_id: "import-1", name: "Editable master", status: "ready", progress: 1,
  status_message: "Project stems ready", manifest: {}, scene: {}, requested_stems: ["Vocals"],
  prepared_stems: ["Vocals"], stem_generation: 1, revision: 1, error: null,
  created_at: "2026-01-01T12:00:00Z", updated_at: "2026-01-01T12:01:00Z",
  tracks: [{
    id: "track-1", position: 0, status: "ready", progress: 1, manifest_overrides: {}, scene_overrides: {},
    source_preview_url: null, error: null, asset,
    stems: [{ id: "stem-1", stem_key: "Vocals", sample_rate: 48000, channels: 2, size_bytes: 1, audio_url: "/vocals.wav", preview_url: null }],
  }],
  exports: [],
};

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(async () => project),
      exportProject: vi.fn(async () => ({ id: "job-1" })),
      saveProject: vi.fn(async () => project),
      saveProjectTrack: vi.fn(async () => project),
      retryProject: vi.fn(async () => project),
      resolveStemRouting: vi.fn(async () => ({})),
    },
  };
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/projects/project-1"]}>
      <Routes>
        <Route path="/projects/:projectId" element={<ProjectDetailPage configuration={null} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProjectDetailPage tabs", () => {
  it("defaults to the Mixing tab with preview and routing graph visible", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    expect(screen.getByRole("tab", { name: "Mixing" })).toHaveAttribute("data-state", "active");
    expect(screen.getByText("Routing preset")).toBeInTheDocument();
    // Preview transport and speaker graph render regardless of tab.
    expect(screen.getByRole("button", { name: /^(Play|Pause)$/i })).toBeInTheDocument();
  });

  it("switches to the Mastering tab and keeps preview/graph mounted", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("tab", { name: "Mastering" }));

    expect(screen.getByText("Spectral EQ")).toBeInTheDocument();
    expect(screen.getByText("Loudness normalization")).toBeInTheDocument();
    expect(screen.getByText("Reference EQ match")).toBeInTheDocument();
    // Still visible.
    expect(screen.getByRole("button", { name: /^(Play|Pause)$/i })).toBeInTheDocument();
  });

  it("switches to the Delivery tab and exports from there", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    await user.click(screen.getByRole("tab", { name: "Delivery" }));

    expect(screen.getByText("Container")).toBeInTheDocument();
    expect(screen.getByText("Normalize output")).toBeInTheDocument();
    const exportButton = screen.getByRole("button", { name: /Export project/ });
    fireEvent.click(exportButton);
    await waitFor(() => expect(api.exportProject).toHaveBeenCalledWith("project-1"));
  });

  it("writes mastering edits to the project manifest even while the mixing tab is track-scoped", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("Editable master")).toBeInTheDocument());

    // Switch mixing edit scope to "track" first.
    fireEvent.change(screen.getByLabelText("Edit scope"), { target: { value: "track" } });

    await user.click(screen.getByRole("tab", { name: "Mastering" }));
    const loudnessToggle = screen.getByText("Loudness normalization")
      .closest("div")!.parentElement!.querySelector("button")!;
    fireEvent.click(loudnessToggle);

    await waitFor(() => expect(api.saveProject).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.saveProject).mock.calls.at(-1)!;
    const savedManifest = payload.manifest as unknown as { mastering: { loudness: { normalize: boolean } } };
    expect(savedManifest.mastering.loudness.normalize).toBe(false);
  });
});
