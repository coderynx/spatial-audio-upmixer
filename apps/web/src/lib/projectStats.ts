import type { Job, Project } from "@/api";

export type ProjectSize = {
  sources: number;
  stems: number;
  exports: number;
  total: number;
};

export function projectSize(project: Project): ProjectSize {
  const sources = project.tracks.reduce((total, track) => total + track.asset.size_bytes, 0);
  const stems = project.tracks.reduce(
    (total, track) => total + track.stems.reduce((sum, stem) => sum + stem.size_bytes, 0),
    0,
  );
  const exports = project.exports.reduce((total, job) => total + jobArtifactSize(job), 0);
  return { sources, stems, exports, total: sources + stems + exports };
}

export function jobArtifactSize(job: Job) {
  const top = job.artifacts.reduce((total, artifact) => total + artifact.size_bytes, 0);
  const perTrack = job.tracks.reduce(
    (total, track) => total + track.artifacts.reduce((sum, artifact) => sum + artifact.size_bytes, 0),
    0,
  );
  return top + perTrack;
}

export type StemCacheEntry = {
  stemKey: string;
  count: number;
  size: number;
  channels: number;
  sampleRate: number;
  projects: { id: string; name: string; count: number; size: number }[];
};

/** Content-addressed stems are shared across projects, so the cache view
 * groups every prepared stem by key rather than listing them per project. */
export function stemCacheEntries(projects: Project[]): StemCacheEntry[] {
  const entries = new Map<string, StemCacheEntry>();
  for (const project of projects) {
    for (const track of project.tracks) {
      for (const stem of track.stems) {
        const entry = entries.get(stem.stem_key) || {
          stemKey: stem.stem_key,
          count: 0,
          size: 0,
          channels: stem.channels,
          sampleRate: stem.sample_rate,
          projects: [],
        };
        entry.count += 1;
        entry.size += stem.size_bytes;
        const owner = entry.projects.find((item) => item.id === project.id);
        if (owner) {
          owner.count += 1;
          owner.size += stem.size_bytes;
        } else {
          entry.projects.push({ id: project.id, name: project.name, count: 1, size: stem.size_bytes });
        }
        entries.set(stem.stem_key, entry);
      }
    }
  }
  return [...entries.values()].sort((a, b) => b.size - a.size);
}
