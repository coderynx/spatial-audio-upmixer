import * as React from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "@/app/AppShell";
import { JobComposer } from "@/features/composer/JobComposer";
import { JobsPage } from "@/features/jobs/JobsPage";
import { useJobs } from "@/features/jobs/useJobs";
import type { Job } from "@/api";
import { ProjectDetailPage } from "@/features/projects/ProjectDetailPage";
import { ProjectNewPage } from "@/features/projects/ProjectNewPage";
import { ProjectsPage } from "@/features/projects/ProjectsPage";
import { useProjects } from "@/features/projects/useProjects";

export default function App() {
  const { jobs, configuration, loading, error, refresh, action } = useJobs();
  const projectsState = useProjects();
  const location = useLocation();
  const navigate = useNavigate();
  const [composerOpen, setComposerOpen] = React.useState(false);
  const [remix, setRemix] = React.useState<Job | null>(null);
  const createJob = () => {
    setRemix(null);
    setComposerOpen(true);
  };
  const projectRoute = location.pathname.startsWith("/projects");
  const remixJob = (job: Job) => {
    setRemix(job);
    setComposerOpen(true);
  };
  return (
    <AppShell
      configuration={configuration}
      onRefresh={() => projectRoute ? void projectsState.refresh() : void refresh()}
      onCreate={projectRoute ? () => navigate("/projects/new") : createJob}
      createLabel={projectRoute ? "New project" : "New job"}
    >
      <Routes>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route path="/projects" element={<ProjectsPage projects={projectsState.projects} loading={projectsState.loading} error={projectsState.error} onRefresh={() => void projectsState.refresh()} onDelete={(project) => void projectsState.deleteProject(project)} />} />
        <Route path="/projects/new" element={<ProjectNewPage configuration={projectsState.configuration || configuration} />} />
        <Route path="/projects/:projectId" element={<ProjectDetailPage configuration={projectsState.configuration || configuration} />} />
        <Route path="/jobs" element={<JobsPage jobs={jobs} loading={loading} error={error} onAction={action} onRemix={remixJob} onCreate={createJob} onRefresh={() => void refresh()} />} />
      </Routes>
      <JobComposer open={composerOpen} onOpenChange={setComposerOpen} remix={remix} configuration={configuration} onCreated={() => void refresh(true)} />
    </AppShell>
  );
}
