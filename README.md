# upmixer

A command-line tool and Python library for upmixing audio from stereo or surround formats to Dolby Atmos and other multichannel layouts.

Two processing modes are available: a real-time STFT-based pipeline that works on any input, and a stem separation pipeline that splits the audio into instruments first and then places each stem spatially. The stem mode produces noticeably better spatial separation at the cost of significantly longer processing time.

Supported output formats: 5.1, 7.1, 5.1.2, 5.1.4, 7.1.2, 7.1.4.

---

## Installation

```bash
pip install upmixer
```

Stem separation mode requires an additional dependency. Install the CPU variant (works everywhere) or the GPU variant if you have CUDA:

```bash
# CPU (slower, works everywhere)
pip install "upmixer[separation-cpu]"

# GPU (CUDA)
pip install "upmixer[separation-gpu]"
```

YAML manifest support requires PyYAML:

```bash
pip install "upmixer[manifest]"
# or: pip install pyyaml
```

Python 3.11 or later is required.

---

## CLI usage

### Classic mode

```bash
# Stereo → 5.1 (realtime mode, fast)
upmixer input.wav output.wav --format 5.1

# Stereo → 7.1.4 Atmos using stem separation
upmixer input.wav output.wav --format 7.1.4 --mode stem

# Write ADM-BWF for import into Logic Pro / DaVinci Resolve / Pro Tools
upmixer input.wav output.wav --format 7.1.4 --output-type adm-bwf

# Dolby Atmos Music streaming delivery (explicit loudness + container)
upmixer input.flac output.wav --format 7.1.4 --output-type adm-bwf \
        --output-sample-rate 48000 --loudness-target -18.0

# Override auto-detected input format (8ch is ambiguous: 7.1 or 5.1.2)
upmixer surround.wav output.wav --input-format 5.1.2 --format 7.1.4

# Get a JSON summary when done (useful in scripts)
upmixer input.wav output.wav --mode stem --format 7.1.4 --json

# Suppress all output
upmixer input.wav output.wav -q

# Show full debug output including audio-separator internals
upmixer input.wav output.wav --mode stem -v
```

### Manifest-driven mode

All parameters can be defined in a YAML or JSON manifest file. This is the recommended approach for complex or reproducible jobs:

```bash
# Run a job from a manifest
upmixer --manifest examples/atmos_music.yaml

# Manifest provides defaults; CLI flags override them
upmixer --manifest examples/atmos_music.yaml --preview

# List all valid manifest keys and their types
upmixer --manifest-keys
```

See the [`examples/`](examples/) directory for ready-to-use manifests.

#### Manifest format (YAML)

Version format: `MAJOR.MINOR[.PATCH]` — e.g., `"1.0"` or `"1.0.0"`. ADM-BWF always uses `.wav` extension (WAV container).

```yaml
version: "1.0"

metadata:             # optional — informational only, not inherited by assets
  name: "My Project"
  author: "Jane Doe"

engine:
  mode: stem          # or realtime
  stem_cache_dir: /tmp/upmixer_stems

mixing:
  channel_layout: 7.1.4

format:               # output file format (separate from output path)
  type: adm-bwf       # ITU-R BS.2076-2 ADM-BWF container (WAV)
  subtype: PCM_24
  sample_rate: 48000

mastering:
  loudness:
    normalize: true
    target: -18.0
    max_tp: -1.0

assets:
  - input:  stereo.flac
    output: atmos_music.wav
```

Global blocks (`engine`, `mixing`, `mastering`, `routing`, `format`, `processing`) apply to all assets. Per-asset blocks deep-merge with globals — only specified keys are overridden.

```yaml
# Per-asset override example
assets:
  - input:  tracks/01_intro.flac
    output: dist/01_intro.wav

  - input:  tracks/02_main.flac
    output: dist/02_main.wav
    mixing:
      stem_rebalance:
        Vocals: +0.0   # override only vocals for this track
```

#### Manifest format (JSON)

```json
{
  "version": "1.0",
  "engine": {"mode": "stem"},
  "mixing": {"channel_layout": "7.1.4"},
  "format": {"type": "adm-bwf", "subtype": "PCM_24", "sample_rate": 48000},
  "mastering": {"loudness": {"normalize": true, "target": -18.0, "max_tp": -1.0}},
  "assets": [{"input": "stereo.flac", "output": "atmos_music.wav"}]
}
```

#### Parameter priority order

```
CLI flags  >  manifest values  >  UpmixConfig defaults
```

CLI flags always win. Manifest values override the built-in UpmixConfig defaults.

### Batch processing

Process entire albums or arbitrary file lists in a single invocation. In stem mode the neural network model is **loaded once** and reused across all files — significantly faster than running upmixer per track.

```bash
# All WAV/FLAC files in a directory → output dir (stem mode)
upmixer --batch-dir /albums/ok-computer/ --output-dir /out/ --mode stem --format 7.1.4

# Cherry-picked files from different directories (realtime, 4 parallel workers)
upmixer --inputs /dir1/track1.wav /dir2/track2.flac /dir3/bonus.wav \
        --output-dir /out/ --mode realtime --batch-workers 4

# Manifest-driven batch (see examples/batch_album_stem.yaml)
upmixer --manifest examples/batch_album_stem.yaml

# Get a JSON summary of all results
upmixer --batch-dir /albums/ --output-dir /out/ --json
```

**Resource usage**: By default upmixer runs at reduced OS priority (`--cpu-priority low`) and caps numpy/torch thread counts to half the logical CPU count. Pass `--cpu-priority normal` to disable this.

#### Batch manifest format

Manifest batch uses the unified `assets` array — a single-file job is a one-item array:

```yaml
version: "1.0"

engine:
  mode: stem
  stem_cache_dir: /tmp/upmixer_stems

mixing:
  channel_layout: 7.1.4

mastering:
  loudness:
    normalize: true
    target: -18.0

assets:
  - input:  /albums/01_intro.flac
    output: /output/01_intro.wav

  - input:  /albums/02_main.flac
    output: /output/02_main.wav
    mixing:
      stem_rebalance:
        Vocals: +0.0   # per-asset override
```

For directory scanning use the CLI: `upmixer --batch-dir /albums/ --output-dir /out/ --mode stem`.

See [`examples/batch_album_stem.yaml`](examples/batch_album_stem.yaml), [`examples/batch_files_realtime.yaml`](examples/batch_files_realtime.yaml), and [`examples/batch_explicit_jobs.yaml`](examples/batch_explicit_jobs.yaml) for complete examples.

#### Batch Python API

```python
from upmixer.batch import BatchProcessor, resolve_batch_jobs
from upmixer.config import UpmixConfig

jobs = resolve_batch_jobs(
    input_paths=["/dir1/a.wav", "/dir2/b.flac"],
    output_dir="/out/",
)
result = BatchProcessor(UpmixConfig(), mode="stem").process(jobs)
print(f"{len(result.jobs)}/{len(jobs)} succeeded in {result.wall_time_s:.1f}s")
for fail in result.failed:
    print(f"FAILED: {fail['input']} — {fail['error']}")
```

`StemUpmixPipeline` now supports context manager syntax to ensure the model is released when done:

```python
from upmixer.separation.stem_pipeline import StemUpmixPipeline
from upmixer.config import UpmixConfig

with StemUpmixPipeline(UpmixConfig(), model="BS-Roformer-SW.ckpt") as pipeline:
    for track, out in pairs:
        result = pipeline.process_file(track, out)   # model reused each call
# model released here
```

---

### Key CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--manifest` / `-m` | — | YAML or JSON manifest file |
| `--manifest-keys` | — | Print all valid manifest keys and exit |
| `--format` | `5.1` | Output channel layout |
| `--mode` | `realtime` | `realtime` or `stem` |
| `--input-format` | auto | Force input layout (required for ambiguous channel counts) |
| `--output-type` | `wav` | `wav` or `adm-bwf` |
| `--output-sample-rate` | same as input | Resample output (e.g. `48000`) |
| `--output-subtype` | `PCM_24` | Bit depth: `PCM_16`, `PCM_24`, `PCM_32` |
| `--no-loudness-normalize` | — | Disable BS.1770-4 loudness normalization |
| `--loudness-target` | `-18.0` | Target integrated loudness in LKFS |
| `--stem-model` | `htdemucs_ft.yaml` | Separation model |
| `--stem-model-dir` | `~/.cache/upmixer-models` | Model cache directory |
| `--inputs` | — | One or more input files for batch (any directories) |
| `--batch-dir` | — | Directory to scan for WAV/FLAC (batch mode) |
| `--output-dir` | — | Output directory for batch mode |
| `--batch-workers` | `1` | Parallel workers (realtime batch only) |
| `--cpu-priority` | `low` | `low` = nice(10) + cap threads; `normal` = no limits |
| `--center-gain` | `0.85` | Center channel gain |
| `--surround-gain` | `0.60` | Side surround gain |
| `--height-gain` | `0.55` | Height channel gain |
| `--lfe-gain` | `0.50` | LFE gain |
| `--lfe-cutoff` | `120` | LFE low-pass cutoff in Hz |
| `--preview` | — | Process a 30 s excerpt instead of the full file |
| `--preview-duration` | `30.0` | Preview window length in seconds |
| `--preview-start` | auto-center | Preview start time in seconds |
| `-q` / `--quiet` | — | Suppress all output except errors |
| `-v` / `--verbose` | — | Debug logging |
| `--json` | — | Print `UpmixResult` / `BatchResult` as JSON to stdout |

### Delivery targets

Set loudness, container, and sample rate directly in your manifest or via CLI flags. Common target configurations:

| Target | `output.type` | `output.sample_rate` | `loudness.target` | `loudness.max_tp` | Use case |
|--------|--------------|---------------------|-------------------|-------------------|----------|
| Dolby Atmos Music (streaming) | `adm-bwf` | `48000` | `-18.0` | `-1.0` | Apple Music, Amazon Music, Tidal |
| Dolby Atmos Blu-ray (TrueHD) | `wav` | `48000` | `-27.0` | `-2.0` | Blu-ray via TrueHD/MLP encoder |

See [`examples/atmos_music.yaml`](examples/atmos_music.yaml) and [`examples/batch_files_realtime.yaml`](examples/batch_files_realtime.yaml) for complete manifest examples.

---

## Python API

```python
from upmixer import UpmixConfig, UpmixPipeline

config = UpmixConfig(output_format="7.1.4", loudness_target_lkfs=-18.0)
pipeline = UpmixPipeline(config)
result = pipeline.process_file("stereo.wav", "atmos.wav")

print(f"Processed {result.duration_seconds:.1f}s in {result.processing_time_seconds:.1f}s")
```

Stem mode:

```python
from upmixer import UpmixConfig
from upmixer.separation.stem_pipeline import StemUpmixPipeline

config = UpmixConfig(output_format="7.1.4")
pipeline = StemUpmixPipeline(config, model="htdemucs_ft.yaml")
result = pipeline.process_file("stereo.wav", "atmos.wav")

print(result.to_json())
```

Progress callback (useful for GUIs or integrations):

```python
def on_progress(message: str, fraction: float) -> None:
    print(f"[{fraction:.0%}] {message}")

result = pipeline.process_file("input.wav", "output.wav", progress_callback=on_progress)
```

Manifest-driven API:

```python
from upmixer.config import UpmixConfig
from upmixer.manifest import load_manifest, validate_manifest, parse_manifest, apply_asset_job
from upmixer.pipeline import UpmixPipeline

raw = load_manifest("job.yaml")
validate_manifest(raw)                           # raises ManifestError on bad version/assets
meta, asset_jobs = parse_manifest(raw)           # meta: ManifestMeta | None

for job in asset_jobs:
    config = UpmixConfig()
    apply_asset_job(config, job)                 # applies job.config + format params
    pipeline = UpmixPipeline(config)
    result = pipeline.process_file(job.input, job.output)
```

`UpmixResult` fields:

```python
result.input_format             # e.g. "Stereo"
result.output_format            # e.g. "7.1.4 Atmos"
result.duration_seconds         # audio duration
result.stems                    # list of stem names used (stem mode only)
result.measured_lkfs            # integrated loudness before normalization
result.measured_tp_dbtp         # True Peak after loudness gain
result.applied_gain_db          # total gain applied for loudness compliance
result.tp_limited               # True if TP ceiling reduced the gain
result.processing_time_seconds
result.to_json()                # serialize to JSON string
```

`MasteringResult` (returned by `MasteringChain.process()`):

```python
from upmixer.mastering import MasteringChain, MasteringResult
from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP

chain = MasteringChain(UpmixConfig(loudness_normalize=True))
channels, mastering_result = chain.process(channels_dict, sample_rate=48000, output_fmt=FORMAT_MAP["7.1.4"])

mastering_result.measured_lkfs      # float | None
mastering_result.measured_tp_dbtp   # float | None
mastering_result.applied_gain_db    # float | None
mastering_result.tp_limited         # bool
```

Logging follows the standard Python `logging` module under the `upmixer` logger name. Nothing is printed to stdout or stderr unless you configure a handler.

---

## How it works

### Realtime mode

The input is processed frame-by-frame using a short-time Fourier transform. Per-frequency-bin inter-channel coherence is estimated and used to separate direct (correlated) from ambient (diffuse) content. Direct content stays in the front bed; ambient content is spread to surrounds and heights. A harmonic mask prevents tonal content from leaking to surrounds where diffuse reverb belongs.

This mode works on any input channel count and has predictable latency (one STFT window).

### Stem mode

The input is separated into instrument stems using [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) (Demucs or RoFormer models). Each stem is then analyzed for stereo width, frequency balance, and transient density, and routed to its appropriate spatial position based on routing tables derived from Dolby Atmos Music mixing practice:

- Lead vocals → center-anchored (C dominant, FL/FR phantom support)
- Backing vocals → widened front + height for chorus expansion
- Drums → center anchor for kick/snare, front bed primary, LFE as weight send
- Bass → front L/R primary, center for mono low-end, LFE as effect send
- Guitar → front-dominant, room depth in surrounds
- Other / pads / atmospherics → diffuse in surrounds, strong height presence (reverb tails belong overhead)

For multichannel inputs (5.1, 7.1, etc.), each stereo zone (front, surround, back, height) is separated independently. Stems are tagged with their zone and routed to preserve spatial intent of the original mix.

### Processing pipeline

The pipeline is split into two stages:

**Mixing phase** — spatial routing and energy normalization. Handled by `UpmixPipeline` (realtime) or `StemUpmixPipeline` (stem). Produces a multichannel bed.

**Mastering phase** — loudness compliance and peak control. Handled by `MasteringChain` and shared by both pipelines:

1. **BS.1770-4 integrated loudness normalization** — scalar linear gain to hit the target LKFS. No dynamic processing, no clipping.
2. **True Peak ceiling** — if the post-normalization True Peak exceeds `loudness_max_tp` dBTP, a second linear gain reduction is applied.
3. **Tanh soft-limiter** — always applied last to catch any transient peaks that survived the True Peak check.

---

## Output formats

| Format | Channels | Notes |
|--------|----------|-------|
| `5.1` | FL FR C LFE SL SR | Standard cinema surround |
| `7.1` | FL FR C LFE SL SR BL BR | Extended rear channels |
| `5.1.2` | FL FR C LFE SL SR TFL TFR | Atmos overhead pair |
| `5.1.4` | FL FR C LFE SL SR TFL TFR TBL TBR | Full Atmos overhead |
| `7.1.2` | FL FR C LFE SL SR BL BR TFL TFR | 7.1 + front overhead |
| `7.1.4` | FL FR C LFE SL SR BL BR TFL TFR TBL TBR | Full Atmos bed |

ADM-BWF output (`--output-type adm-bwf`) embeds ITU-R BS.2076-2 metadata for DAW import, including BWF bext chunk loudness fields populated with BS.1770-4 measurement results (compliant with Dolby Atmos Music Master Delivery Specification v2022.07).

---

## References and credits

- [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) by nomadkaraoke — stem separation via Demucs and RoFormer models
- [SoundFile](https://github.com/bastibe/python-soundfile) — audio I/O
- [SciPy](https://scipy.org/) — filtering, STFT, resampling
- [NumPy](https://numpy.org/) — array processing
- ITU-R BS.1770-4 — integrated loudness measurement and gating algorithm
- ITU-R BS.2076-2 — Audio Definition Model (ADM) for broadcast WAV
- Dolby Atmos Music Master Delivery Specification v2022.07 — loudness, container, and LFE requirements for streaming delivery
- Dolby Atmos Music mixing practice — spatial routing philosophy for the stem engine

---

## License

MIT
