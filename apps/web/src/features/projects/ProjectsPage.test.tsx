import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Project } from "@/api";
import { HeaderSlotProvider } from "@/app/HeaderSlot";
import { ProjectsPage } from "./ProjectsPage";

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "project-1",
    import_id: "import-1",
    name: "Editable master",
    notes: null,
    status: "ready",
    progress: 1,
    status_message: "Project stems ready",
    progress_log: [],
    manifest: { mixing: { channel_layout: "7.1.4" } },
    scene: {},
    requested_stems: ["Vocals"],
    prepared_stems: ["Vocals"],
    stem_generation: 1,
    preview_quality: "high",
    revision: 1,
    error: null,
    created_at: "2026-01-01T12:00:00Z",
    updated_at: "2026-01-01T12:01:00Z",
    tracks: [],
    exports: [],
    ...overrides,
  };
}

function renderPage(projects: Project[]) {
  const onDelete = vi.fn();
  const onCreate = vi.fn();
  const onImported = vi.fn();
  render(
    <MemoryRouter>
      <HeaderSlotProvider>
        <ProjectsPage
          projects={projects}
          loading={false}
          error={null}
          onDelete={onDelete}
          onCreate={onCreate}
          onImported={onImported}
        />
      </HeaderSlotProvider>
    </MemoryRouter>,
  );
  return { onDelete, onCreate, onImported };
}

describe("ProjectsPage", () => {
  it("deletes a project without navigating", () => {
    const project = makeProject();
    const { onDelete } = renderPage([project]);
    fireEvent.click(screen.getByLabelText("Delete Editable master"));
    expect(onDelete).toHaveBeenCalledWith(project);
  });

  it("filters the project list by status facet", () => {
    const separating = makeProject({ id: "project-2", name: "Still separating", status: "separating" });
    renderPage([makeProject(), separating]);

    expect(screen.getAllByText("Editable master").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Status: separating (1)" }));

    expect(screen.queryByText("Editable master")).not.toBeInTheDocument();
    expect(screen.getAllByText("Still separating").length).toBeGreaterThan(0);
  });

  it("inspects the selected project's layout and preview quality", () => {
    renderPage([makeProject()]);
    expect(screen.getByText("Preview quality")).toBeInTheDocument();
    expect(screen.getAllByText("7.1.4").length).toBeGreaterThan(0);
    expect(screen.getByText("Revision")).toBeInTheDocument();
  });
});
