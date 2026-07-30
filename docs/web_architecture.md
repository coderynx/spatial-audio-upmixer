# Upmixer Web Architecture

The web application is an additional delivery surface around the existing `upmixer` package. The CLI remains independent and the web worker calls the same public pipelines and manifest parser.

## Components

- `upmixer_web/` exposes a versioned FastAPI API and OpenAPI document.
- `web/` is a React and shadcn/ui client. It does not contain processing logic.
- SQLAlchemy persists imports, jobs, per-track progress, and artifacts. SQLite is the default; install the `web-postgres` extra and supply a PostgreSQL URL without changing repositories or models.
- `ObjectStorage`, `AudioSource`, and `AudioSink` isolate blob access. The first implementation uses local disk. An S3 implementation can materialize sources into worker scratch space and upload sink outputs without changing job orchestration.
- `WorkerManager` recovers interrupted jobs and bounds processing concurrency. Each job's actual pipeline work (`StemUpmixPipeline`/`UpmixPipeline.process_file`, including stem separation and mastering) runs in an isolated child process via `upmixer_web/worker/subprocess.py`, so a native crash (OS OOM-kill, CUDA/MPS driver crash, segfault) in that code fails only the one job, not the server. Progress and completion are relayed back over a queue; pause/delete requests terminate the child process.

`upmixer_web/` itself is organized as vertical slices (routes/service/views/schemas per feature, under `features/`) rather than by technical layer — see [Web API architecture](web_api_architecture.md) for the package layout and the convention new endpoints must follow.

## Durable state

Job states are `queued`, `running`, `pause_requested`, `paused`, `completed`, `failed`, and `deleting`. Completed track records are retained during resume, so album jobs continue at the first incomplete track after a pause or service restart.

Source files live under `imports/{import_id}` and outputs under `jobs/{job_id}`. Ordinary jobs use one shared stem cache root. Projects instead own isolated stem storage under `project-stems/{project_id}/{track_id}`; the web worker catalogues the cache files after processing so the browser can audition only that project’s stems. Projects use defaults plus optional per-track overrides, can prepare additional stems in the background, and create normal linked jobs for exports. Project mastering references are trusted import-owned uploads and transfer to exported jobs. Stereo downmixes are server-managed output artifacts.

Waveform envelopes for the editor timeline are precomputed server-side while stems are catalogued, from the samples the preview proxy encode already holds in memory, and stored as one `peaks.bin` plus a `peaks.json` sidecar per track. Projects catalogued before peaks existed are backfilled from their preview proxies on a dedicated single-thread executor that coalesces repeat requests, the same scheduling shape `prepare_reference_match` uses; `ProjectView.peaks_pending` reports that state so the browser polls only until the asset lands.

The project editor uses the Web Audio API HRTF panner for an immediate stereo headphone preview and a live 3D source view. This is an approximate binaural audition, not a Dolby renderer or a substitute for the final pipeline export. Browser preview code is delivery-layer behavior; separation and exports continue through `StemUpmixPipeline`.

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

## Preview audio graph (`audioEngine.ts`)

`audioEngine.ts` has no React import — it is the framework-free DAW audio
layer (graph construction, transport, DSP parameter application, metering)
that `useStemPreview.ts` binds to React state/effects via `EngineRef<T>`, a
plain `{ current: T }` box matching the shape `useRef` returns so the same
field can be handed straight through to consumers that already read
`.current` off the hook's returned refs.

**Speaker buses.** One `SpeakerBus` per positional channel: an ambisonic
mono encoder pointed at that speaker's fixed direction feeds the shared HOA
bus, gated by a `muteGain` so a speaker can be silenced independently of any
stem (the same "render the channel bed, not the objects" model Apple's
Spatial Audio renderer uses). `masterIn`/`masterOut` are stable per-channel
mastering insert points; `buildMasteringTopology` wires a fresh EQ →
compressor-gain → bass chain (or a passthrough when mastering is inactive)
between them on every rebuild, before the binaural/spatial render — see
`docs/contracts/preview_export_parity.md` §1. `stereoSend` is present only
for channels the BS.775 downmix uses (see `STEREO_DOWNMIX_GAINS`, excludes
height channels and LFE); `nativeIndex` is the channel's input index on the
native discrete `ChannelMergerNode`, or -1 if the current layout omits it.

**Stem sources.** One `AudioNodeSet` per playable source (an ordinary stem,
or the dry stereo source anchor). `stemGain` (mute/solo/rebalance/anchor-duck)
sits upstream of the splitter; the anchor has none, since its two sends are
driven directly by anchor strength instead. `postEqGain` is the fixed
post-EQ insert point `createStemSends` reads from — `buildStemEqChains`
rebuilds the `stemGain -> [EQ filters] -> postEqGain` chain whenever
`mix.stem_eq` changes (mirrors `upmixer/separation/stem_eq.py`; the anchor
has none, since the backend's dry-source blend bypasses stem EQ entirely).
`sends` holds one gain node per positional channel the source can reach,
each feeding straight into that channel's `SpeakerBus.muteGain`, so route
weights and speaker mute compose for free. `lfeGain`/`lfeFilters` are absent
for the anchor (the backend never routes its dry blend through LFE), as is
`analyser` (the passive level tap for the 3D scene's audio-reactive halos)
and `meterSplitter`/`meterAnalysers` (one passive analyser per source
channel, feeding the mixer strip's meters, so a stereo stem shows two
independent bars instead of being summed away).

**Channel-bed router history.** The preview used to binauralize each stem as
a point object via a Web Audio HRTF `PannerNode` — that convolves every
source with one generic, non-personalized HRIR with a diffuse-field
high-frequency rolloff (duller than the dry final master, worse than a
single EQ shelf could fix), and the API exposes no way to load a different
HRTF. An earlier fix layered a fixed compensation EQ on the HRTF bus and
hard-switched some sources to a dry stereo pan to dodge comb filtering
against the panner's unqueryable ITD — both hacks are gone now. The preview
now mirrors the backend exactly: stems route into the same 11-speaker
channel bed `upmixer/separation/stem_router.py::StemRouter.route` builds,
and that channel bed — not the individual stems — is what gets encoded to
ambisonics and binauralized (the "virtual loudspeaker" model, see below).

**Routing.** `createStemSends` builds the shaped-signal set a stem needs
(raw L/R, mono downmix, surround send, height send) and one gain node per
positional channel wiring the right shaped signal in, mirroring
`upmixer/separation/stem_router.py::route()` — done once per stem rather
than per output channel since channels like SL/BL share a shaped signal.
`CHANNEL_SIGNAL` maps each positional channel to which shaped signal feeds
it. `STEREO_DOWNMIX_GAINS` mirrors `upmixer/utils.py::itu_downmix_stereo`
(ITU-R BS.775-4 Annex 4 Table 2): back channels fold into the matching side
channel attenuated by the centre coefficient; height channels and LFE are
excluded per the standard (LFE gets its own discrete native send instead).

**Offline pre-playback analysis.** Loudness/true-peak correction is measured
once, offline, before playback starts (`precomputeCorrection`), not sampled
during playback; `CORRECTION_STEP_MS` only paces the live bus-compressor's
gain-reduction poll. That offline render is capped at `ANALYSIS_MAX_SECONDS`
total: the full mastering + per-stem routing + ambisonic encode/decode graph
it mirrors is hundreds of nodes (order-3 ambisonic encoding alone is ~18
nodes per positional speaker), and rendering a full multi-minute program at
full resolution measured upwards of a minute on a real track — indistinguishable
from a hang to a waiting user. Programs over the cap are sampled as
`ANALYSIS_EXCERPT_COUNT` equal-length windows spread evenly across the
timeline (`buildAnalysisExcerpts`) instead of rendered whole, so both quiet
intros and loud choruses are represented; the resulting splice-point
discontinuities can only ever nudge the measured true peak up, making the
safety-net gain more conservative, never less.

**Clip detection.** `CLIP_TOLERANCE` is not 0dBFS: a sample clearing unity by
only a hairline still needs to count as clipped, but the live
`WaveShaperNode`'s `oversample: "4x"` reconstruction filter measurably rings
past `buildSoftLimitCurve`'s own designed asymptote at its knee — observed
peaks of ~1.005–1.006 (~+0.04–0.05dB) on ordinary loud passages, a known
WaveShaperNode oversampling artifact, not audible content. The tolerance
gives ~8x headroom over that ripple while still catching a genuine over.

**Offline correction measurement (`precomputeCorrection`).** Whole-program
loudness/true-peak measurement replaced an earlier realtime approach that
sampled a live post-mastering tap frame-by-frame during playback (a one-shot
loudness snapshot, then an ever-tightening true-peak ratchet re-sampled every
~100ms). That ratchet caused a real artifact: an early snapshot commonly
landed on a quiet intro, so the first loud chorus/drop later would set a new
true-peak record and yank gain down mid-playback — a moving target
masquerading as a limiter. A DAW bounce doesn't do this, since it knows the
whole file's level before a single sample reaches the output, so
`precomputeCorrection` renders the program once through a throwaway
`OfflineAudioContext` mirror of the same mastering + collapse graph
`initialize()`/`buildMasteringTopology()` build live (reusing the same
framework-free builders the golden-diff harness calls, and the same decoded
stem buffers/cached FIR assets — no new DSP, just a static measurement of the
same signal `apply()` drives live via `computeMixGains()`), and measures both
quantities in one pass. It does not render the entire file — see
`buildAnalysisExcerpts` above and `ANALYSIS_MAX_SECONDS` for why. Native
output needs no correction of its own (the look-ahead limiter worklet is its
own safety net, so `apply()` keeps `nativeOutputGain` at unity); this only
runs for binaural/stereo/transaural, tracked against
`precomputedForMode`/`precomputedForProfile` so a mode/profile switch
mid-session re-measures instead of reusing a stale value.

This offline pass deliberately does not reproduce the compressor's dynamic
gain reduction (unlike `render-preview-golden.mjs`'s suspend()/resume()
polling trick, which only ever renders a fixed 5-second synthetic signal
where that's cheap — scaled to a multi-minute track it becomes tens of
thousands of serialized round-trips that never finish in practice).
`compGains` stay at the static makeup gain for this measurement pass; live
playback still applies the real per-tick reduction via
`applyCompressorReduction`/`correctionInterval`, unaffected. This measurement
was already a coarse approximation (mono-downmix mean-square, no BS.1770
gating) whose job is steering one global static gain, not reproducing
bit-exact dynamics.

**Engine gain domains and buses (`PreviewAudioEngine` fields).** `master`
carries only `tpSafeGain` (the true-peak-safe loudness correction, PROGRAM
domain — what a bounce of the graph would contain), never the user's monitor
volume. `monitorGain` is the MONITOR domain: Transport volume/mute, applied
strictly after `softLimit` so raising the slider can never drive the limiter
harder or change its engagement, matching a DAW program-fader/monitor-knob
split; the channel/headphone meters tap `softLimit`'s output, upstream of
`monitorGain`, so they never move with the volume slider.

`hoaBus` is the ambisonic rendering core: every positional speaker's encoder
feeds it (a plain summing gain, explicit/discrete at 16 channels), which
renders the whole channel bed to stereo via the loaded HRIR set — the
"virtual loudspeaker" model matching what `StemUpmixPipeline` delivers.
`preMasterBus`/`lfeBus`/`mergePoint` sum the binaural render with the LFE
bypass ahead of the soft-limiter; `preMasterBus` is a plain passthrough since
mastering (EQ/comp/bass) runs earlier on the discrete bed
(`SpeakerBus.masterIn`/`masterOut`), matching `upmixer/pipeline.py`'s order
(`MasteringChain` before `render_binaural_delivery`). `decodeConvolvers` is
one `ConvolverNode` pair per ACN channel per `spatial_audio_engine.md` §4.
`voicingMerger` is `buildBinauralGraph`'s post-voicing stereo output; LFE
sums directly into its two inputs (a `ChannelMergerNode` sums same-index
sources). `binauralGraphNodes` collects every other node `buildBinauralGraph`
creates for one-array teardown in `reset()`.

`crosstalkHoaBus`/`crosstalkDecodeConvolvers`/`xtcConvolvers`/
`crosstalkVoicingChain`/`crosstalkGraphNodes`/`crosstalkGate` are a parallel
bus built by `buildCrosstalkGraph`, gated the same way as binaural; its
internal anechoic "flat" sub-decode owns its own HOA bus/convolvers,
independent of the primary `hoaBus` (which decodes whatever `spatialProfile`
the headphone preview selected).

`sidechainSum`/`sidechainSink`/`sidechainCompressor`/`compGains`/
`compMakeupGain` emulate the backend's linked-sidechain bus compressor
(`upmixer/mastering/compressor.py`) without an AudioWorklet: every channel's
post-EQ signal sums into `sidechainSum`, feeding one shared
`DynamicsCompressorNode` used purely as a sidechain (its native channelCount
can't exceed 2, so it can't process the discrete bed directly); its live
`.reduction`, polled in `tick()`, is applied as a shared gain to every
channel's own `compGain` node. `sidechainSink` is a permanent zero-gain tap
into `mergePoint` keeping the detector node part of the actively rendered
graph, since a compressor with no path to the destination may not reliably
keep updating `.reduction`.

## Extension boundaries

Dolby Encoding Engine integration belongs after `StorageAudioSink`. A future encoder sink can consume WAV or ADM-BWF artifacts, emit stream-ready artifacts, and attach them to the same job. Webhooks should subscribe to committed job transitions rather than pipeline callbacks. Object storage should implement `ObjectStorage`; external library or upload sources should implement `AudioSource`.

## Reverse proxy

Uvicorn trusts only `UPMIXER_FORWARDED_ALLOW_IPS`. Set it to the proxy address or network, not `*`, in exposed deployments. Set `UPMIXER_ROOT_PATH` when the proxy publishes the application beneath a path prefix. The frontend uses same-origin relative API URLs.

## Local development

Use Python 3.11, 3.12, or 3.13 for web stem jobs. Install Python web dependencies with `python3 -m pip install -e ".[dev,web,web-dev,separation-cpu]"`, then run `python3 -m upmixer_web`. The CPU extra also enables MPS acceleration on supported Apple Silicon Macs; reserve `separation-gpu` for NVIDIA CUDA hosts. In `web/`, install packages and run `npm run dev`. Vite proxies `/api` to the backend.

For a GPU container, run `docker compose up --build`. The Compose configuration requests all available NVIDIA GPUs and persists database, imports, cache, and outputs in the `upmixer-data` volume.
