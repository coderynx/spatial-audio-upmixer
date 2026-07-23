import * as React from "react";
import { api, type Configuration, type Project } from "@/api";

export function useProjects() {
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [configuration, setConfiguration] = React.useState<Configuration | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const refresh = React.useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [nextProjects, nextConfiguration] = await Promise.all([
        api.listProjects(),
        configuration ? Promise.resolve(configuration) : api.getConfiguration(),
      ]);
      setProjects(nextProjects);
      setConfiguration(nextConfiguration);
      setError(null);
    } catch (nextError) {
      setError((nextError as Error).message);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [configuration]);
  React.useEffect(() => {
    void refresh();
  }, [refresh]);
  const deleteProject = React.useCallback(
    async (project: Project) => {
      if (
        !window.confirm(
          `Permanently delete "${project.name}"? This removes all separated stems, previews, and project settings and cannot be undone.`,
        )
      )
        return;
      try {
        await api.deleteProject(project.id);
        await refresh(true);
      } catch (nextError) {
        setError((nextError as Error).message);
      }
    },
    [refresh],
  );
  return { projects, configuration, loading, error, refresh, deleteProject };
}
