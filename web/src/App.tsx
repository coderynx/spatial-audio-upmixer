import * as React from "react";
import { AppShell } from "@/app/AppShell";
import { JobComposer } from "@/features/composer/JobComposer";
import { JobsPage } from "@/features/jobs/JobsPage";
import { useJobs } from "@/features/jobs/useJobs";
import type { Job } from "@/api";

export default function App() {
  const { jobs, configuration, loading, error, refresh, action } = useJobs();
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
  return (
    <AppShell
      configuration={configuration}
      onRefresh={() => void refresh()}
      onCreate={createJob}
    >
      <JobsPage
        jobs={jobs}
        loading={loading}
        error={error}
        onAction={action}
        onRemix={remixJob}
        onCreate={createJob}
      />
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
