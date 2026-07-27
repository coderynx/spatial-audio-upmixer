import * as React from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "@/app/AppShell";
import { JobComposer } from "@/features/composer/JobComposer";
import { JobsPage } from "@/features/jobs/JobsPage";
import { useJobs } from "@/features/jobs/useJobs";
import type { Job } from "@/api";
import { StemCachePage } from "@/features/cache/StemCachePage";
import { ProjectDetailPage } from "@/features/projects/ProjectDetailPage";
import { ProjectNewPage } from "@/features/projects/ProjectNewPage";
import { ProjectsPage } from "@/features/projects/ProjectsPage";
import { useProjects } from "@/features/projects/useProjects";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { StoragePage } from "@/features/storage/StoragePage";

export default function App() {
  const location = useLocation();
  const projectRoute = location.pathname.startsWith("/projects");
  const jobsRoute = location.pathname.startsWith("/jobs");
  const storageRoute = location.pathname.startsWith("/storage");
  const { jobs, configuration, loading, error, refresh, action } = useJobs(jobsRoute || storageRoute);
  const projectsState = useProjects();
  const navigate = useNavigate();
  const [composerOpen, setComposerOpen] = React.useState(false);
  const [remix, setRemix] = React.useState<Job | null>(null);
  const createJob = () => {
    setRemix(null);
    setComposerOpen(true);
  };
  const remixJob = (job: Job) => {
    setRemix(job);
    setComposerOpen(true);
  };
  const effectiveConfiguration = projectsState.configuration || configuration;
  // Projects and jobs are fetched independently; the cache and storage views
  // read both, so a refresh from those routes has to reload each of them.
  const refreshAll = () => {
    void projectsState.refresh();
    void refresh();
  };
  return (
    <AppShell
      configuration={effectiveConfiguration}
      onRefresh={projectRoute ? () => void projectsState.refresh() : jobsRoute ? () => void refresh() : refreshAll}
      onCreate={projectRoute ? () => navigate("/projects/new") : jobsRoute ? createJob : undefined}
      createLabel={projectRoute ? "New project" : jobsRoute ? "New job" : undefined}
    >
      <Routes>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route
          path="/projects"
          element={
            <ProjectsPage
              projects={projectsState.projects}
              loading={projectsState.loading}
              error={projectsState.error}
              onDelete={(project) => void projectsState.deleteProject(project)}
            />
          }
        />
        <Route path="/projects/new" element={<ProjectNewPage configuration={effectiveConfiguration} />} />
        <Route path="/projects/:projectId" element={<ProjectDetailPage configuration={effectiveConfiguration} />} />
        <Route
          path="/jobs"
          element={
            <JobsPage
              jobs={jobs}
              loading={loading}
              error={error}
              onAction={action}
              onRemix={remixJob}
              onCreate={createJob}
            />
          }
        />
        <Route
          path="/stem-cache"
          element={
            <StemCachePage
              projects={projectsState.projects}
              loading={projectsState.loading}
              error={projectsState.error}
            />
          }
        />
        <Route
          path="/storage"
          element={
            <StoragePage
              projects={projectsState.projects}
              jobs={jobs}
              loading={projectsState.loading || loading}
              error={projectsState.error || error}
            />
          }
        />
        <Route path="/settings" element={<SettingsPage configuration={effectiveConfiguration} />} />
      </Routes>
      <JobComposer
        open={composerOpen}
        onOpenChange={setComposerOpen}
        remix={remix}
        configuration={configuration}
        onCreated={() => void refresh(true)}
      />
    </AppShell>
  );
}
