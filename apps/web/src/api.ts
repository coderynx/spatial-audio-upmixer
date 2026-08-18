import type { ServedEngineConstants } from "@/features/projects/masteringProfiles"
import type { CodecChoice } from "@/lib/codecs"

export type Asset = {
  id: string
  position: number
  filename: string
  relative_path: string
  size_bytes: number
  title: string | null
  artist: string | null
  album: string | null
  release_date: string | null
  track_number: number | null
  duration_seconds: number | null
  sample_rate: number | null
  channels: number | null
  audio_url: string | null
}

export type ImportPreview = {
  id: string
  kind: "track" | "album"
  title: string | null
  artist: string | null
  release_date: string | null
  cover_url: string | null
  created_at: string
  assets: Asset[]
}

export type MasteringReference = {
  id: string
  filename: string
  size_bytes: number
  duration_seconds: number | null
  sample_rate: number | null
  channels: number | null
}

/**
 * A project's server-precomputed reference-match correction curve — see
 * docs/contracts/preview_export_parity.md Ledgers D12/D20.
 *
 * `fir_url` is a base URL the browser appends live `strength`/`max_db` query
 * params to, and is null when no curve is persisted yet.
 * `strength`/`spectrum`/`rms`/`max_db` are not server state — read them from
 * `Manifest.mastering.match_reference`. `rms_gain_db` still applies when
 * spectral matching is off.
 */
export type ReferenceMatchAsset = {
  fir_url: string | null
  channels: string[]
  rms_gain_db: number
  sample_rate: number
}

export type Artifact = {
  id: string
  kind: string
  filename: string
  content_type: string
  size_bytes: number
  download_url: string
}

export type JobTrack = {
  id: string
  position: number
  status: string
  progress: number
  result: Record<string, unknown> | null
  error: string | null
  asset: Asset
  artifacts: Artifact[]
}

export type Job = {
  id: string
  import_id: string
  source_job_id: string | null
  project_id?: string | null
  name: string
  status: string
  progress: number
  status_message: string
  manifest: Record<string, unknown>
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  updated_at: string
  tracks: JobTrack[]
  artifacts: Artifact[]
  mastering_reference?: MasteringReference | null
}

export type ProjectStem = {
  id: string
  stem_key: string
  sample_rate: number
  channels: number
  size_bytes: number
  audio_url: string | null
  preview_url: string | null
}

export type ProjectTrack = {
  id: string
  position: number
  status: string
  progress: number
  // `layouts` is the track's speaker-layout set, in display order;
  // `layout_overrides` holds each one's own mix/master/delivery block. A
  // listed layout with no stored block yet reads as an empty override over
  // the project manifest.
  layouts: string[]
  layout_overrides: Record<string, Record<string, unknown>>
  scene_overrides: Record<string, unknown>
  source_preview_url: string | null
  // Server-precomputed waveform envelopes, served as their own binary asset
  // rather than inlined here — see upmixer_web/project_storage.py's
  // write_track_peaks. `peaks_stem_keys` gives the block order inside it.
  peaks_url: string | null
  peaks_bins: number
  peaks_stem_keys: string[]
  peaks_duration_seconds: number | null
  error: string | null
  asset: Asset
  stems: ProjectStem[]
}

export type StemScene = Record<string, {
  enabled?: boolean
  azimuth_deg?: number
  elevation_deg?: number
}>

export type StemRouting = Record<string, Record<string, number>>

export type Project = {
  id: string
  import_id: string | null
  name: string
  notes: string | null
  status: string
  progress: number
  status_message: string
  progress_log: { ts: string; message: string; fraction: number }[]
  manifest: Record<string, unknown>
  scene: { stems?: StemScene }
  // Timeline/monitoring preferences (stem order, listening profile, master
  // volume, A/B bypass, haze/elevation intensity) — display and monitor
  // taste, not mix data, saved separately via `saveProjectViewState` so a
  // fader drag doesn't pay for the manifest-normalizing `/settings` route.
  // See ProjectViewState in `apps/api/src/features/projects/schemas.py`.
  view_state: Record<string, unknown>
  requested_stems: string[]
  prepared_stems: string[]
  stem_generation: number
  preview_quality: string
  revision: number
  error: string | null
  created_at: string
  updated_at: string
  tracks: ProjectTrack[]
  exports: Job[]
  mastering_reference?: MasteringReference | null
  // One correction curve per speaker layout in use — the curve is measured
  // off the mixed bed, so it cannot be shared across layouts.
  reference_match?: Record<string, ReferenceMatchAsset>
  // True while a reference-match recompute is queued or running on the
  // server (see upmixer_web/worker.py::WorkerManager.schedule_reference_match)
  // — the frontend keeps polling while this is set so `reference_match`
  // refreshes once the background pass lands.
  reference_match_pending?: boolean
  // True while a waveform-peaks backfill is queued or running for a project
  // catalogued before peaks existed (upmixer_web/worker.py::schedule_peaks).
  peaks_pending?: boolean
}

export type Configuration = {
  defaults: Record<string, unknown>
  manifest_keys: Record<string, string>
  choices: {
    channel_layouts: string[]
    output_types: string[]
    output_codecs?: CodecChoice[]
    output_subtypes: string[]
    sample_rates: number[]
    modes: string[]
    spatial_profiles: string[]
    binaural_profiles?: string[]
    binaural_beds?: string[]
    transaural_profiles?: string[]
    transaural_beds?: string[]
    eq_profiles: string[]
    compressor_profiles: string[]
    bass_profiles: string[]
    delivery_targets?: string[]
    bass_spreads?: string[]
    bass_lfe_modes?: string[]
    stem_eq_profiles: string[]
    stem_phase_fix_reference_models?: string[]
    stem_debleed_models?: string[]
    stem_routing_presets?: string[]
    layout_channels?: Record<string, string[]>
    stems: string[]
    preview_qualities?: string[]
  }
  constants: ServedEngineConstants
  capabilities: {
    stem_separation: {
      available: boolean
      backend: string | null
      accelerated: boolean
      accelerator_detected: boolean
      accelerator_issue: string | null
      platform: string
      install_message: string | null
    }
  }
}

const rootPath = document.querySelector<HTMLMetaElement>('meta[name="upmixer-root-path"]')?.content.replace(/\/$/, "") || ""

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${rootPath}${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail))
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  getConfiguration: () => request<Configuration>("/api/v1/configuration"),
  resolveStemRouting: (payload: { stems: string[]; channel_layout: string; preset: string }) =>
    request<StemRouting>("/api/v1/stem-routing/resolve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  getImport: (id: string) => request<ImportPreview>(`/api/v1/imports/${id}`),
  listJobs: () => request<Job[]>("/api/v1/jobs"),
  listProjects: () => request<Project[]>("/api/v1/projects"),
  getProject: (id: string) => request<Project>(`/api/v1/projects/${id}`),
  projectEventsUrl: (id: string) => `${rootPath}/api/v1/projects/${id}/events`,
  upload: async (items: { file: File; path: string }[]) => {
    const data = new FormData()
    for (const item of items) {
      data.append("files", item.file, item.file.name)
      data.append("relative_paths", item.path)
    }
    return request<ImportPreview>("/api/v1/imports", { method: "POST", body: data })
  },
  uploadMasteringReference: async (importId: string, file: File) => {
    const data = new FormData()
    data.append("file", file, file.name)
    return request<MasteringReference>(`/api/v1/imports/${importId}/mastering-references`, { method: "POST", body: data })
  },
  createJob: (payload: { import_id: string; name: string; manifest: Record<string, unknown>; start: boolean; mastering_reference_id: string | null }) =>
    request<Job>("/api/v1/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  cloneJob: (id: string, payload: { name: string; manifest: Record<string, unknown>; start: boolean; mastering_reference_id: string | null }) =>
    request<Job>(`/api/v1/jobs/${id}/clone`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  pauseJob: (id: string) => request(`/api/v1/jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: string) => request(`/api/v1/jobs/${id}/resume`, { method: "POST" }),
  deleteJob: (id: string) => request(`/api/v1/jobs/${id}`, { method: "DELETE" }),
  createProject: (payload: { name: string; notes?: string | null; manifest?: Record<string, unknown>; scene?: Record<string, unknown> }) =>
    request<Project>("/api/v1/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  addProjectAssets: (projectId: string, payload: { import_id: string; per_asset_overrides?: Record<string, Record<string, unknown>> }) =>
    request<Project>(`/api/v1/projects/${projectId}/assets`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  saveProject: (id: string, payload: { name?: string; notes?: string | null; manifest: Record<string, unknown>; scene: Record<string, unknown>; mastering_reference_id?: string | null; preview_quality?: string }) =>
    request<Project>(`/api/v1/projects/${id}/settings`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  saveProjectTrackLayout: (projectId: string, trackId: string, layout: string, payload: { manifest_overrides: Record<string, unknown>; scene_overrides: Record<string, unknown> }) =>
    request<Project>(`/api/v1/projects/${projectId}/tracks/${trackId}/layouts/${encodeURIComponent(layout)}/settings`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  setTrackLayouts: (projectId: string, trackId: string, layouts: string[]) =>
    request<Project>(`/api/v1/projects/${projectId}/tracks/${trackId}/layouts`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ layouts }) }),
  saveProjectViewState: (id: string, payload: Record<string, unknown>) =>
    request<void>(`/api/v1/projects/${id}/view-state`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  expandProjectStems: (id: string, stems: string[]) =>
    request<Project>(`/api/v1/projects/${id}/stems`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stems }) }),
  retryProject: (id: string) => request<Project>(`/api/v1/projects/${id}/retry`, { method: "POST" }),
  reprepareProjectStems: (id: string) => request<Project>(`/api/v1/projects/${id}/stems/reprepare`, { method: "POST" }),
  exportProject: (id: string, layout: string) =>
    request<Job>(`/api/v1/projects/${id}/exports`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ layout }) }),
  deleteProject: (id: string) => request(`/api/v1/projects/${id}`, { method: "DELETE" }),
  // DAW-style Save/Open: a portable .upmix.zip, distinct from exportProject
  // above (which renders a deliverable mix, not a re-editable workspace).
  projectArchiveUrl: (id: string) => `${rootPath}/api/v1/projects/${id}/archive`,
  trackStemsArchiveUrl: (projectId: string, trackId: string) =>
    `${rootPath}/api/v1/projects/${projectId}/tracks/${trackId}/stems/archive`,
  importProjectArchive: async (file: File) => {
    const data = new FormData()
    data.append("file", file, file.name)
    return request<Project>("/api/v1/projects/import", { method: "POST", body: data })
  },
}
