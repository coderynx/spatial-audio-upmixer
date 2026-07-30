# Spatial Audio Upmixer

Spatial Audio Upmixer is a Python library and command-line tool for converting mono, stereo, and existing surround
audio into higher-channel-count spatial beds. It combines content-aware spatial processing, optional neural source
separation, and a shared mastering chain to produce standard multichannel WAV or ADM-BWF files.

The installable library package and Python namespace remain `upmixer`. The `upmixer` command now ships from a
separate `upmixer-cli` package (which depends on `upmixer`), so you can install just the library without a CLI
footprint. This repository is a uv workspace monorepo — `packages/core` (library), `apps/cli`, `apps/api`, and
`apps/web` — see `AGENTS.md` for the full layout.

An optional web application adds interactive track and album workflows without changing the CLI. It uses the same manifests and processing pipelines, so browser-configured jobs remain portable to automation.

## Web application

Install and start the API:

```bash
uv sync --all-packages --extra dev --extra web-dev --extra separation-cpu
uv run upmixer-web
```

Stem separation requires Python 3.11, 3.12, or 3.13. The `separation-cpu`
extra is also the correct choice for Apple Silicon Macs — the in-core
inference engine selects MPS acceleration when available. Use
`separation-gpu` only on NVIDIA CUDA hosts.

Start the React client in another terminal:

```bash
cd apps/web
npm install
npm run dev
```

Open `http://localhost:5173`. API documentation is available at `http://localhost:8000/api/docs`. See [Web architecture](docs/web_architecture.md) for persistence, storage interfaces, job states, endpoints, reverse-proxy setup, GPU containers, and extension boundaries.

## Features

- Upmix mono, stereo, 5.0, 5.1, 7.1, 5.1.2, 5.1.4, and 7.1.2 sources.
- Produce 5.1, 7.1, 5.1.2, 5.1.4, 7.1.2, or 7.1.4 channel beds.
- Choose a fast coherence-based STFT pipeline or an instrument-aware stem pipeline.
- Adapt width, ambience, transients, and height routing with automatic or explicit spatial profiles.
- Preserve the intent of multichannel sources through zone-aware routing and optional source anchoring.
- Apply reference matching, spectral EQ, bus compression, bass control, BS.1770 loudness normalization, and true-peak
  control through one mastering chain.
- Write standard WAV, ADM-BWF metadata, and an optional ITU-R BS.775 stereo downmix.
- Run reproducible YAML/JSON manifests and safe batch jobs with preflight checks, atomic writes, resume state, and JSON
  reports.

## Requirements

- Python 3.11, 3.12, or 3.13
- WAV or FLAC input readable by [libsndfile](https://libsndfile.github.io/)
- Additional CPU or GPU dependencies for stem separation

## Installation

Install the CLI (pulls in the `upmixer` library automatically):

```bash
python3 -m pip install upmixer-cli
```

Or install just the library, for realtime processing without a CLI:

```bash
python3 -m pip install upmixer
```

Install stem separation support for CPU or GPU inference:

```bash
python3 -m pip install "upmixer-cli[separation-cpu]"
python3 -m pip install "upmixer-cli[separation-gpu]"
```

YAML manifests require PyYAML. JSON manifests work with the core installation.

```bash
python3 -m pip install "upmixer-cli[manifest]"
```

For local development:

```bash
git clone https://github.com/coderynx/audio-upmixer.git
cd audio-upmixer
uv sync --all-packages --extra dev --extra web-dev --extra manifest
```

## Quick Start

Create a 5.1 WAV from a stereo source:

```bash
upmixer input.wav output.wav --format 5.1
```

Create a 7.1.4 bed with stem separation:

```bash
upmixer input.flac output.wav --mode stem --format 7.1.4
```

Preview a short section before processing the complete file:

```bash
upmixer input.wav preview.wav --format 7.1.4 --preview --preview-duration 30
```

Run a reproducible manifest:

```bash
upmixer --manifest examples/atmos_music.yaml
```

Existing outputs are protected by default. Use `--overwrite` only when replacement is intentional, or `--resume` with
saved run state to skip outputs whose input and settings still match.

## Processing Modes

| Mode | Best for | How it works | Additional dependency |
|---|---|---|---|
| `realtime` | Fast previews, general files, and parallel batches | Coherence analysis separates correlated direct sound from diffuse ambience, then derives center, surround, back, height, and LFE content | None |
| `stem` | Music, complex mixes, and deliberate instrument placement | Separates requested sources, analyzes each stem, routes it spatially, blends native source zones when requested, and masters the result | `separation-cpu`/`separation-gpu` extra |

Realtime mode treats mono as a centered stereo pair. For multichannel input it preserves existing channels and derives
only the channels needed by the target layout.

Stem mode separates every available stereo zone—front, surround, back, and height—rather than collapsing a
multichannel source to stereo. Center and LFE material are retained as passthrough channels where applicable.

### Spatial profiles

Use `--spatial-profile auto` to select a profile from the source, or choose one explicitly:

`balanced`, `intimate`, `rhythmic`, `spacious`, `live`, or `detailed`.

`--spatial-intensity` controls how strongly the selected profile changes the base routing. Set
`--no-spatial-preanalysis` when offline content analysis is not wanted.

### Stem planning

Stem mode selects and orders separation models automatically from the requested outputs. The default set is `vocals`,
`bass`, `drums`, `guitar`, `piano`, and `other`.

Additional requested stems activate specialized stages:

- `crowd` isolates audience content before primary instrument separation.
- `kick`, `snare`, `toms`, `hi-hat`, `ride`, and `crash` subdivide the isolated drums stem.
- `lead-vocals` and `backing-vocals` subdivide the primary vocals stem.

Requesting a drum or vocal child replaces its parent in the final mix. For example,
request `lead-vocals,backing-vocals` to mix both refined vocal stems; do not also
request `vocals`. `crowd` remains a final stem while its crowd-free residual feeds
the primary instrument separation stage.

For example:

```bash
upmixer live.wav live_714.wav --mode stem --format 7.1.4 \
  --stems lead-vocals,backing-vocals,bass,kick,snare,toms,crowd
```

Model files are cached under `~/.cache/upmixer-models` by default. `--stem-cache-dir` separately caches generated stems
for repeat runs. Stem silence skipping is enabled by default, and inference batch size is selected from the available
CPU, CUDA, MPS, or CoreML resources unless `--stem-batch-size` is supplied.

CPU inference adapts to VM/container memory. Systems with at most 12 GiB use smaller MDXC segments and split long
inputs into bounded-memory chunks; CPU runs keep one model resident to prevent hierarchical plans from stacking model
weights. Override these limits with `--stem-segment-size`, `--stem-chunk-duration-s`, and
`--stem-model-cache-size`. For a 4-core, low-memory VM, keep `--cpu-priority auto`, batch size 1, and place
`--stem-cache-dir` on the SSD.

Stem separation runs on an in-core PyTorch inference engine
(`upmixer/separation/inference/`) driving BS-RoFormer, Mel-Band RoFormer, and
TFC-TDF (MDX23C) architectures directly, with model integration not tied to
any third-party wrapper's supported-model list.

## Supported Layouts

Spatial Audio Upmixer only adds channels: the selected output must be a strict superset of the detected input layout.

### Inputs

| Layout | Channels |
|---|---:|
| Mono | 1 |
| Stereo | 2 |
| 5.0 | 5 |
| 5.1 | 6 |
| 7.1 | 8 |
| 5.1.2 | 8 |
| 5.1.4 | 10 |
| 7.1.2 | 10 |

Eight- and ten-channel files are ambiguous from channel count alone. Use `--input-format` to distinguish `7.1` from
`5.1.2`, or `5.1.4` from `7.1.2`.

```bash
upmixer surround.wav output.wav --input-format 5.1.2 --format 7.1.4
```

### Outputs

| Layout | Channels | Height channels |
|---|---:|---:|
| 5.1 | 6 | 0 |
| 7.1 | 8 | 0 |
| 5.1.2 | 8 | 2 |
| 5.1.4 | 10 | 4 |
| 7.1.2 | 10 | 2 |
| 7.1.4 | 12 | 4 |

### Binaural (Spatial Audio Engine)

`--output-type binaural` is a delivery format alongside `wav`/`adm-bwf`, not a channel layout — pick it to render a
headphone-ready stereo WAV instead of the discrete speaker bed you chose with `--format` (which must be one of
`5.1.4` / `7.1.2` / `7.1.4`). The pipeline upmixes/masters to that bed as normal, then collapses it to stereo
through one of three profiles (`--binaural-profile`):

| Profile | For | Room | Consumer voicing |
|---|---|---|---|
| `studio` (default) | Professional monitoring, as if in a treated spatial-audio mixing room | Neutral measured-style room | None |
| `listening` | Flattering "hi-fi system" consumer preview on headphones | Reference cinema room (studio room amount, warmer tail) | Hi-fi enhance (loudness-matched): +2 dB bass, +3 dB air, +1.5 dB presence, +15% cinema width |
| `flat` | Anechoic reference — verify the mix with zero added coloration | None | None |

```bash
upmixer input.wav monitor.wav --format 7.1.4 --output-type binaural --binaural-profile studio
upmixer input.wav consumer.wav --format 7.1.4 --output-type binaural --binaural-profile listening
```

Mutually exclusive with `--output-type adm-bwf` (ADM-BWF requires a discrete channel-based bed) since both share the
same `--output-type` flag. See [`docs/standards/spatial_audio_engine.md`](docs/standards/spatial_audio_engine.md)
for the full signal-graph contract — the same one the web UI's in-preview Spatial Audio Engine profile selector
implements.

## CLI Workflows

Write a standard multichannel WAV and a stereo compatibility downmix:

```bash
upmixer input.wav output_714.wav --format 7.1.4 \
  --downmix-output output_stereo.wav
```

Write a 24-bit, 48 kHz ADM-BWF bed for downstream authoring:

```bash
upmixer input.flac authoring_bed.wav --mode stem --format 7.1.4 \
  --output-type adm-bwf --output-subtype PCM_24 --output-sample-rate 48000
```

Inspect a job without writing audio:

```bash
upmixer input.wav output.wav --format 7.1.4 --dry-run
```

Emit a machine-readable result or run report:

```bash
upmixer input.wav output.wav --format 7.1.4 --json
upmixer input.wav output.wav --format 7.1.4 --report run-report.json
```

Use `upmixer --help` for every CLI option and `upmixer --manifest-keys` for manifest-configurable fields.

## Manifests

YAML and JSON manifests describe one or more assets with shared settings and optional per-asset overrides. A manifest
version must use `MAJOR.MINOR` or `MAJOR.MINOR.PATCH` syntax.

```yaml
version: "1.0"

metadata:
  name: "Album spatial masters"
  author: "Example Engineer"

engine:
  mode: stem
  stems: [vocals, bass, drums, guitar, piano, other]
  stem_cache_dir: /tmp/upmixer-stems
  stem_silence_skip: true

mixing:
  channel_layout: 7.1.4
  stem_source_anchor_strength: 0.5
  spatial:
    profile: auto
    intensity: 1.0
    preanalyze: true

format:
  type: adm-bwf
  subtype: PCM_24
  sample_rate: 48000

mastering:
  eq:
    profile: spatial-present
    strength: 0.7
  compressor:
    profile: transparent
  loudness:
    normalize: true
    target: -18.0
    max_tp: -1.0

assets:
  - input: tracks/01_intro.flac
    output: masters/01_intro.wav

  - input: tracks/02_single.flac
    output: masters/02_single.wav
    mixing:
      stem_rebalance:
        Vocals: 1.0
```

Global `engine`, `mixing`, `routing`, `format`, `mastering`, and `processing` blocks apply to every asset. Per-asset
configuration blocks are deep-merged, so an override changes only the specified keys. Keep `engine.mode` consistent
across a manifest batch because one pipeline type is reused for the complete run.

Optional stereo companion output uses the ITU-R BS.775 matrix. Omit `output` to
derive a sibling filename from each multichannel asset output:

```yaml
format:
  downmix:
    enabled: true
    surround_coeff: 0.7071
```

The web server owns uploaded input/output/cache paths and downmix filenames.
Projects expose the same delivery setting and publish the companion as an artifact.
See [`docs/project_manifest_parity.md`](docs/project_manifest_parity.md) for the
project-to-manifest mapping and intentionally unsupported fields.

Configuration precedence is:

```text
CLI flags > per-asset manifest values > global manifest values > UpmixConfig defaults
```

### Included examples

| Manifest | Demonstrates |
|---|---|
| [`stereo_to_51.yaml`](examples/stereo_to_51.yaml) | Fast stereo-to-5.1 realtime processing |
| [`stem_714.yaml`](examples/stem_714.yaml) | Full 7.1.4 stem workflow, source anchoring, and optional stem shaping |
| [`stem_hierarchical.yaml`](examples/stem_hierarchical.yaml) | Automatic crowd, primary, drum, and backing-vocal stages |
| [`atmos_music.yaml`](examples/atmos_music.yaml) | YAML ADM-BWF music-authoring bed |
| [`atmos_music.json`](examples/atmos_music.json) | Equivalent JSON ADM-BWF example |
| [`binaural_studio.yaml`](examples/binaural_studio.yaml) | Headphone export, neutral Studio monitoring profile |
| [`binaural_listening.yaml`](examples/binaural_listening.yaml) | Headphone export, Listening (reference cinema room) profile |
| [`batch_album_stem.yaml`](examples/batch_album_stem.yaml) | Explicit album jobs with shared stem settings |
| [`batch_dir_stem.yaml`](examples/batch_dir_stem.yaml) | Directory expansion and per-directory overrides |
| [`batch_explicit_jobs.yaml`](examples/batch_explicit_jobs.yaml) | Per-track deep-merged overrides |
| [`batch_files_realtime.yaml`](examples/batch_files_realtime.yaml) | Realtime batch from unrelated source paths |

## Batch Processing

Process a directory recursively after reviewing its resolved jobs:

```bash
upmixer --batch-dir /albums/project --output-dir /masters/project \
  --recursive --output-template '{relative_stem}.wav' --format 7.1.4 --dry-run
```

Run it and write resumable state plus a portable report:

```bash
upmixer --batch-dir /albums/project --output-dir /masters/project \
  --recursive --output-template '{relative_stem}.wav' --format 7.1.4 \
  --resume --report project-report.json
```

Use `--inputs` for files from unrelated directories. Realtime batches may use `--batch-workers`; stem batches remain
sequential so loaded separator models can be reused. CLI stem batches created with `--inputs` or `--batch-dir`
automatically use a shared cache unless a cache directory is configured explicitly; manifest examples set their cache
directory in `engine`.

## Output and Mastering

Both pipelines feed the same mastering chain:

1. Optional spectral and RMS reference matching.
2. Optional preset spectral EQ.
3. Optional multichannel bus compression.
4. Optional bass control and LFE trim.
5. Soft peak limiting.
6. ITU-R BS.1770 integrated loudness normalization and true-peak ceiling enforcement.

Loudness results, applied gain, selected spatial profile, stem names, and processing time are returned in
`UpmixResult` and are available from `--json`.

### Standard WAV

Standard WAV output supports `PCM_16`, `PCM_24`, and `PCM_32`. The source sample rate is retained unless
`--output-sample-rate` is set.

### ADM-BWF

ADM-BWF output contains the PCM bed plus BWF, ADM XML, CHNA, and DBMD chunks for downstream DAW or encoding workflows.
It requires:

- A `.wav` output path.
- `PCM_24` audio.
- A 48 kHz or 96 kHz output sample rate; 48 kHz is selected when no rate is specified.
- One of the supported 5.1 through 7.1.4 output layouts.

The generated file is a channel-based authoring bed, not a Dolby codec bitstream. Delivery requirements vary by
platform and workflow; validate metadata, loudness, channel order, and downstream encoding in the target toolchain.

## Python API

Realtime/file pipeline:

```python
from upmixer import UpmixConfig, UpmixPipeline

config = UpmixConfig(
    output_format="7.1.4",
    spatial_profile="auto",
    loudness_target_lkfs=-18.0,
)
result = UpmixPipeline(config).process_file("stereo.wav", "spatial.wav")
print(result.to_json())
```

Stem pipeline with automatic model planning and deterministic cleanup:

```python
from upmixer import UpmixConfig
from upmixer.separation.stem_pipeline import StemUpmixPipeline

config = UpmixConfig(
    output_format="7.1.4",
    stems=["vocals", "bass", "drums", "guitar", "piano", "other"],
    stem_cache_dir="/tmp/upmixer-stems",
)

with StemUpmixPipeline(config) as pipeline:
    result = pipeline.process_file("stereo.wav", "spatial.wav")

print(result.stems)
```

Both `process_file` methods accept `input_format_override` and a `progress_callback(message, fraction)` callable.

The primary public imports are:

```python
from upmixer import (
    FORMAT_MAP,
    INPUT_FORMAT_MAP,
    StreamingProcessor,
    UpmixConfig,
    UpmixPipeline,
    UpmixResult,
)
```

## Development

Install development dependencies and run each package's suite:

```bash
uv sync --all-packages --extra dev --extra web-dev --extra manifest
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
```

Performance and real-model checks are opt-in:

```bash
uv run pytest packages/core/tests -m perf -s
```

Build a distribution for a given package:

```bash
uv build packages/core
```

## References

- [ITU-R BS.1770](https://www.itu.int/rec/R-REC-BS.1770/) — audio programme loudness and true-peak measurement.
- [ITU-R BS.2076](https://www.itu.int/rec/R-REC-BS.2076/) — Audio Definition Model.
- [ITU-R BS.775](https://www.itu.int/rec/R-REC-BS.775/) and
  [ITU-R BS.2051](https://www.itu.int/rec/R-REC-BS.2051/) — multichannel and advanced sound-system layouts.

## Legal Disclaimer

Dolby and Dolby Atmos are registered trademarks of Dolby Laboratories Licensing Corporation.
This software is an independent open-source project. It is not affiliated with, sponsored by, authorized by, certified
by, or otherwise endorsed by Dolby Laboratories. All references to Dolby Atmos and related specifications are used
solely in a descriptive, nominative sense to indicate technical compatibility with publicly available industry
standards.

## License

[AGPL-3.0-or-later](LICENSE)
