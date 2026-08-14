import * as React from "react";
import { api, type Configuration, type Project } from "@/api";

export function useProjects() {
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [configuration, setConfiguration] = React.useState<Configuration | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const configurationRef = React.useRef(configuration);
  configurationRef.current = configuration;
  // Deliberately excludes `configuration` from deps: `refresh` sets
  // `configuration`, so depending on it would recreate `refresh` on every
  // fetch and re-fire the mount effect below in a self-triggering loop.
  const refresh = React.useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [nextProjects, nextConfiguration] = await Promise.all([
        api.listProjects(),
        configurationRef.current ? Promise.resolve(configurationRef.current) : api.getConfiguration(),
      ]);
      setProjects(nextProjects);
      setConfiguration(nextConfiguration);
      setError(null);
    } catch (nextError) {
      setError((nextError as Error).message);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);
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
