# Upmixer Web Architecture

The web application is an additional delivery surface around the existing `upmixer` package. The CLI remains independent and the web worker calls the same public pipelines and manifest parser.

## Components

- `apps/api/` (`upmixer_web/`) exposes a versioned FastAPI API and OpenAPI document.
- `apps/web/` is a React and shadcn/ui client. It does not contain processing logic.
- SQLAlchemy persists imports, jobs, per-track progress, and artifacts. SQLite is the default; install the `web-postgres` extra and supply a PostgreSQL URL without changing repositories or models.
- `ObjectStorage`, `AudioSource`, and `AudioSink` isolate blob access. The first implementation uses local disk. An S3 implementation can materialize sources into worker scratch space and upload sink outputs without changing job orchestration.
- `WorkerManager` recovers interrupted jobs and bounds processing concurrency. Each job's actual pipeline work (`StemUpmixPipeline`/`UpmixPipeline.process_file`, including stem separation and mastering) runs in an isolated child process via `apps/api/src/worker/subprocess.py`, so a native crash (OS OOM-kill, CUDA/MPS driver crash, segfault) in that code fails only the one job, not the server. Progress and completion are relayed back over a queue; pause/delete requests terminate the child process.

`apps/api/src/` itself is organized as vertical slices (routes/service/views/schemas per feature, under `features/`) rather than by technical layer — see [Web API architecture](web_api_architecture.md) for the package layout and the convention new endpoints must follow.

## Durable state

Job states are `queued`, `running`, `pause_requested`, `paused`, `completed`, `failed`, and `deleting`. Completed track records are retained during resume, so album jobs continue at the first incomplete track after a pause or service restart.

Source files live under `imports/{import_id}` and outputs under `jobs/{job_id}`. Ordinary jobs use one shared stem cache root (`StemCache`, keyed by an inference-plan identity) so re-exporting the same track skips re-separating it. Projects never touch that cache: each track owns a plain stem store under `project-stems/{project_id}/{track_id}/stems/` (`PlainStemStore`, a flat directory with no identity key) that a real prepare pass writes and the web worker catalogues into `ProjectStem` rows so the browser can audition only that project's stems. Projects use defaults plus per-track, per-speaker-layout overrides (`ProjectTrack.layout_overrides`, keyed by `FORMAT_MAP` layout name — a track carries a complete independent mix, master and delivery per layout) and can prepare additional stems in the background. Exporting a project renders one layout as an ordinary, self-contained job: `project_export_job` takes the layout, includes only the tracks that carry it, resolves each one's stem routing and that layout's manifest overrides once at export time, and the job's worker reads them straight back from `job.project_snapshot` as plain data — it never imports project code or touches the shared stem cache. Project mastering references are trusted import-owned uploads and transfer to exported jobs. Stereo downmixes are server-managed output artifacts.

Waveform envelopes for the editor timeline are precomputed server-side while stems are catalogued, from the samples the preview proxy encode already holds in memory, and stored as one `peaks.bin` plus a `peaks.json` sidecar per track. Projects catalogued before peaks existed are backfilled from their preview proxies on a dedicated single-thread executor that coalesces repeat requests, the same scheduling shape `prepare_reference_match` uses; `ProjectView.peaks_pending` reports that state so the browser polls only until the asset lands.

The project editor renders an immediate stereo headphone preview alongside a live 3D source view (see "Preview audio engine" below for how). Browser preview code is delivery-layer behavior; separation and exports continue through `StemUpmixPipeline`.

Deleting a job removes its outputs and database records. Shared source imports and stem cache entries remain because other jobs may reference them. Future storage management can add reference-counted import and cache eviction without changing job deletion semantics.

## API

Interactive docs are served at `/api/docs`; the OpenAPI document is `/api/v1/openapi.json`.

- `POST /api/v1/imports` accepts one or more multipart files and matching `relative_paths`. ZIP files are expanded with path and size checks.
- `GET /api/v1/imports/{id}` returns album metadata and track order.
- `GET /api/v1/imports/{id}/assets/{asset_id}/audio` streams an imported source for browser audition and seeking.
- `GET /api/v1/configuration` returns manifest choices and runtime stem-separation capability.
- `POST /api/v1/jobs` creates a job from an import and a CLI-compatible manifest.
- `GET /api/v1/jobs` and `GET /api/v1/jobs/{id}` return durable state.
- `GET /api/v1/jobs/{id}/events` streams state changes as server-sent events.
- `POST /api/v1/jobs/{id}/pause` and `/resume` control execution.
- `POST /api/v1/jobs/{id}/clone` creates a stem-cache-backed remix.
- `DELETE /api/v1/jobs/{id}` removes a job and its outputs.
- `GET /api/v1/artifacts/{id}/download` downloads a track output or album ZIP.
- `POST /api/v1/projects` creates a stem-backed editable project from an import.
- `GET /api/v1/projects` and `/api/v1/projects/{id}` expose project state, tracks, stems, and export history.
- `PUT /api/v1/projects/{id}/settings`, track settings, and `POST /stems` persist edits and queue stem expansion.
- `GET /api/v1/projects/{id}/tracks/{track_id}/peaks` returns one binary waveform-envelope block per track for the editor timeline.
- `POST /api/v1/projects/{id}/exports` creates a linked standard job from an immutable project snapshot.

## Preview audio engine

The preview does not send audio to the backend, and it no longer
re-implements the DSP either. `apps/web/public/dsp.worklet.js` hosts the
shared Rust core (`packages/dsp`) compiled to WebAssembly and renders the
whole mastered speaker bed itself — routing, mastering, and the
binaural/transaural/stereo collapse. Web Audio is left with decoding, one
node, a monitor gain, and the destination.

The worklet is the *source*, not an insert: the decoded stems live in the
wasm heap, so the engine always knows its input ahead of the playhead. That
is what lets it run the offline algorithms rather than causal approximations
of them — the mono-maker's zero-phase pass and the limiter's forward-window
minimum both get a queue in front of them, and nothing is emitted until its
full look-ahead exists. See `packages/dsp/crates/dsp-core/src/stream/`.

`audioEngine.ts` has no React import — it is the framework-free layer that
owns the `AudioContext`, the transport, and the translation from a project's
mix into the core's parameter block; `useStemPreview.ts` binds it to React
state. Every "the mix changed" path — mute, solo, rebalance, routing, stem
EQ, mastering, speaker mute, output mode, spatial and transaural profile —
resolves to one parameter block and one message. There is no per-control
rewiring, because there is no graph to rewire.

**Engine constants.** The client holds no hardcoded copy of the tunable DSP
values; it fetches them from `GET /api/v1/configuration`'s `constants` block
(the `apps/api` system slice's `engine_constants()`).
`masteringProfiles.ts` carries both shapes: `ServedEngineConstants` is the
wire shape (snake_case, matching the backend) and `EngineConstants` the
normalized shape the parameter builders consume. `resolveEngineConstants`
maps between them, and is the only place voicing params get their
snake_case → camelCase rename.

**Preview module layout.** `audioEngine.ts` keeps the context, transport and
monitor path; the pieces it delegates to live under
`src/features/projects/wasmEngine/` — `engineClient.ts` (worklet messaging),
`engineParams.ts` + `stemMix.ts` (mix → parameter block), `filterAssets.ts` +
`filterTaps.ts` (FIR fetch and per-profile tap cache), `stemLoader.ts`
(ordered concurrent stem decode), `meters.ts` (meter-frame unpacking), and
`engineTypes.ts` (the shared callback/param types).

**Sample rate.** The context is pinned to 48 kHz. Every shipped FIR (HRIR,
XTC, EQ) is designed at that rate, and the previous graph reinterpreted those
taps at whatever rate the device happened to run.

**Gain domains.** The core's output carries the PROGRAM domain — what a
bounce of the same parameters would contain, including the loudness and
true-peak correction. `monitorGain` is the MONITOR domain: the transport's
volume and mute, strictly after the render, so raising the slider can never
change what the limiter does. Meters are measured inside the core at the emit
position, so they never lead the audio.

**Loudness correction.** Measured once per output mode and profile by
rendering the programme through the same engine and taking the real BS.1770
integrated loudness and true peak — in two stages, a fast pass over a handful
of excerpts followed by an exact pass over the whole programme that refines
the gain once it lands in the background (see
`docs/contracts/preview_export_parity.md` P3). The measurement runs against
an uncorrected render, so a previous correction cannot fold into the next one.

**Filter assets.** The decode banks, XTC matrices, and EQ FIRs still ship as
WAVs under `apps/web/public/` and are handed to the core as taps; only the
asset *names* are served (`GET /api/v1/configuration`). The wasm artifact
itself lives at `apps/web/public/wasm/upmixer_dsp.wasm` and is committed, so
a frontend checkout needs no Rust toolchain — rebuild it with `npm run
build:wasm` after any change under `packages/dsp`.

## Extension boundaries

Dolby Encoding Engine integration belongs after `StorageAudioSink`. A future encoder sink can consume WAV or ADM-BWF artifacts, emit stream-ready artifacts, and attach them to the same job. Webhooks should subscribe to committed job transitions rather than pipeline callbacks. Object storage should implement `ObjectStorage`; external library or upload sources should implement `AudioSource`.

## Reverse proxy

Uvicorn trusts only `UPMIXER_FORWARDED_ALLOW_IPS`. Set it to the proxy address or network, not `*`, in exposed deployments. Set `UPMIXER_ROOT_PATH` when the proxy publishes the application beneath a path prefix. The frontend uses same-origin relative API URLs.

## Local development

Use Python 3.11, 3.12, or 3.13 for web stem jobs. Run `uv sync --all-packages --extra dev --extra web-dev --extra separation-cpu` from the repo root (see `AGENTS.md` Commands), then `uv run python -m upmixer_web`. The CPU extra also enables MPS acceleration on supported Apple Silicon Macs; reserve `separation-gpu` for NVIDIA CUDA hosts. In `apps/web/`, install packages and run `npm run dev`. Vite proxies `/api` to the backend.

For a GPU container, run `docker compose up --build`. The Compose configuration requests all available NVIDIA GPUs and persists database, imports, cache, and outputs in the `upmixer-data` volume.
