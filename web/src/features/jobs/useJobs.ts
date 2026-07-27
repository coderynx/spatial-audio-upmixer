import * as React from "react";
import { api, type Configuration, type Job } from "@/api";

export type JobAction = "pause" | "resume" | "delete";

export function useJobs(poll: boolean) {
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [configuration, setConfiguration] =
    React.useState<Configuration | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const refresh = React.useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      setJobs(await api.listJobs());
      setError(null);
    } catch (nextError) {
      setError((nextError as Error).message);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);
  React.useEffect(() => {
    void refresh();
    void api
      .getConfiguration()
      .then(setConfiguration)
      .catch((nextError) => setError((nextError as Error).message));
  }, [refresh]);
  // Only `/jobs` and `/storage` render `jobs` (see App.tsx's `poll` argument)
  // — everywhere else (notably the project preview page) this poll would
  // fetch and discard the response every 2s. It can't be dropped on those
  // two routes: `pause_requested` -> `paused` and `deleting` -> removed are
  // worker-driven transitions only observable by re-listing, and there is
  // no collection-level jobs event stream to replace it with.
  React.useEffect(() => {
    if (!poll) return;
    void refresh(true);
    const timer = window.setInterval(() => void refresh(true), 2000);
    return () => window.clearInterval(timer);
  }, [poll, refresh]);
  const action = React.useCallback(
    async (name: JobAction, job: Job) => {
      try {
        if (name === "pause") await api.pauseJob(job.id);
        if (name === "resume") await api.resumeJob(job.id);
        if (
          name === "delete" &&
          window.confirm(
            `Delete “${job.name}” and its outputs? Shared stems remain available to other jobs.`,
          )
        )
          await api.deleteJob(job.id);
        await refresh(true);
      } catch (nextError) {
        setError((nextError as Error).message);
      }
    },
    [refresh],
  );
  return { jobs, configuration, loading, error, refresh, action };
}
