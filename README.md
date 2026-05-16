# upmixer

A command-line tool and Python library for upmixing audio from stereo or surround formats to Dolby Atmos and other multichannel layouts.

Two processing modes are available: a real-time STFT-based pipeline that works on any input, and a stem separation pipeline that splits the audio into instruments first and then places each stem spatially. The stem mode produces noticeably better spatial separation at the cost of significantly longer processing time.

Supported output formats: 5.1, 7.1, 5.1.2, 5.1.4, 7.1.2, 7.1.4.

---

## Installation

The stem separation mode requires an additional dependency. Install the CPU variant (works everywhere) or the GPU variant if you have CUDA:

```bash
# CPU (slower, works everywhere)
pip install "upmixer[separation-cpu]"

# GPU (CUDA)
pip install "upmixer[separation-gpu]"
```

Python 3.11 or later is required.

---

## CLI usage

```bash
# Stereo → 5.1 (realtime mode, fast)
upmixer input.wav output.wav --format 5.1

# Stereo → 7.1.4 Atmos using stem separation
upmixer input.wav output.wav --format 7.1.4 --mode stem

# Write ADM-BWF for import into Logic Pro / DaVinci Resolve / Pro Tools
upmixer input.wav output.wav --format 7.1.4 --output-type adm-bwf

# Override auto-detected input format (8ch is ambiguous: 7.1 or 5.1.2)
upmixer surround.wav output.wav --input-format 5.1.2 --format 7.1.4

# Get a JSON summary when done (useful in scripts)
upmixer input.wav output.wav --mode stem --format 7.1.4 --json

# Suppress all output
upmixer input.wav output.wav -q

# Show full debug output including audio-separator internals
upmixer input.wav output.wav --mode stem -v
```

### Key options

| Flag | Default | Description |
|------|---------|-------------|
| `--format` | `5.1` | Output channel layout |
| `--mode` | `realtime` | `realtime` or `stem` |
| `--input-format` | auto | Force input layout (required for ambiguous channel counts) |
| `--output-type` | `wav` | `wav` or `adm-bwf` |
| `--output-sample-rate` | same as input | Resample output (e.g. `48000`) |
| `--output-subtype` | `PCM_24` | Bit depth: `PCM_16`, `PCM_24`, `PCM_32` |
| `--no-loudness-normalize` | — | Disable BS.1770-4 loudness normalization |
| `--loudness-target` | `-24.0` | Target integrated loudness in LKFS |
| `--stem-model` | `htdemucs_ft.yaml` | Separation model (4-stem Demucs by default) |
| `--stem-model-dir` | `~/.cache/upmixer-models` | Model cache directory |
| `--center-gain` | `0.85` | Center channel gain |
| `--surround-gain` | `0.60` | Side surround gain |
| `--height-gain` | `0.55` | Height channel gain |
| `--lfe-gain` | `0.50` | LFE gain |
| `-q` / `--quiet` | — | Suppress all output except errors |
| `-v` / `--verbose` | — | Debug logging |
| `--json` | — | Print `UpmixResult` as JSON to stdout |

---

## Python API

```python
from upmixer import UpmixConfig, UpmixPipeline

config = UpmixConfig(output_format="7.1.4", loudness_target_lkfs=-24.0)
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

`UpmixResult` fields:

```python
result.input_format          # e.g. "Stereo"
result.output_format         # e.g. "7.1.4 Atmos"
result.duration_seconds      # audio duration
result.stems                 # list of stem names used (stem mode only)
result.measured_lkfs         # integrated loudness before normalization
result.applied_gain_db       # gain applied for loudness compliance
result.processing_time_seconds
result.to_json()             # serialize to JSON string
```

Logging follows the standard Python `logging` module under the `upmixer` logger name. Nothing is printed to stdout or stderr unless you configure a handler. The `--json` and `--quiet` CLI flags only affect the CLI entry point.

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

Loudness is normalized to ITU-R BS.1770-4 with True Peak limiting before writing the output.

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

ADM-BWF output (`--output-type adm-bwf`) embeds ITU-R BS.2076-2 metadata for DAW import. Tested with Logic Pro.

---

## References and credits

- [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) by nomadkaraoke — stem separation via Demucs and RoFormer models
- [SoundFile](https://github.com/bastibe/python-soundfile) — audio I/O
- [SciPy](https://scipy.org/) — filtering, STFT, resampling
- [NumPy](https://numpy.org/) — array processing
- ITU-R BS.1770-4 — integrated loudness measurement and gating algorithm
- ITU-R BS.2076-2 — Audio Definition Model (ADM) for broadcast WAV
- Dolby Atmos Music mixing practice — spatial routing philosophy for the stem engine

---

## License

MIT
