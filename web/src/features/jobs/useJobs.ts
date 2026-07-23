import * as React from "react";
import { api, type Configuration, type Job } from "@/api";

export type JobAction = "pause" | "resume" | "delete";

export function useJobs() {
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
