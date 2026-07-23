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
  mastering_reference: MasteringReference | null
}

export type Configuration = {
  defaults: Record<string, unknown>
  manifest_keys: Record<string, string>
  choices: {
    channel_layouts: string[]
    output_types: string[]
    output_subtypes: string[]
    sample_rates: number[]
    modes: string[]
    spatial_profiles: string[]
    eq_profiles: string[]
    compressor_profiles: string[]
    bass_profiles: string[]
    stem_eq_profiles: string[]
    stems: string[]
  }
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
  getImport: (id: string) => request<ImportPreview>(`/api/v1/imports/${id}`),
  listJobs: () => request<Job[]>("/api/v1/jobs"),
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
}
