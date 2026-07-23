import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Project } from "@/api";
import { ProjectsPage } from "./ProjectsPage";

const project: Project = {
  id: "project-1",
  import_id: "import-1",
  name: "Editable master",
  status: "ready",
  progress: 1,
  status_message: "Project stems ready",
  manifest: {},
  scene: {},
  requested_stems: ["Vocals"],
  prepared_stems: ["Vocals"],
  stem_generation: 1,
  revision: 1,
  error: null,
  created_at: "2026-01-01T12:00:00Z",
  updated_at: "2026-01-01T12:01:00Z",
  tracks: [],
  exports: [],
};

describe("ProjectsPage", () => {
  it("triggers a manual refresh", () => {
    const onRefresh = vi.fn();
    render(
      <MemoryRouter>
        <ProjectsPage
          projects={[project]}
          loading={false}
          error={null}
          onRefresh={onRefresh}
          onDelete={vi.fn()}
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("deletes a project without navigating", () => {
    const onDelete = vi.fn();
    render(
      <MemoryRouter>
        <ProjectsPage
          projects={[project]}
          loading={false}
          error={null}
          onRefresh={vi.fn()}
          onDelete={onDelete}
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByLabelText("Delete Editable master"));
    expect(onDelete).toHaveBeenCalledWith(project);
  });
});
