"""Thin wrapper around python-audio-separator for stem extraction."""
from __future__ import annotations

import gc
import inspect
import logging
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

_log = logging.getLogger("upmixer")

_SUCCESSFUL_BATCHES: dict[tuple[str, str], int] = {}

_MIN_CPU_SEGMENT_SIZE = 64
_MIN_CPU_CHUNK_DURATION_S = 60.0


def _detect_backend() -> str:
    """Return accelerator used by torch models without requiring torch."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except (ImportError, RuntimeError):
        pass
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
        if "CUDAExecutionProvider" in providers:
            return "cuda"
        if "CoreMLExecutionProvider" in providers:
            return "coreml"
    except (ImportError, RuntimeError):
        pass
    return "cpu"


def _automatic_batch_size(backend: str) -> int:
    """Choose safe full-precision MDXC inference batching."""
    if backend == "cuda":
        try:
            import torch
            free_bytes, _ = torch.cuda.mem_get_info()
            free_gib = free_bytes / (1024 ** 3)
            if free_gib >= 12.0:
                return 4
            if free_gib >= 8.0:
                return 2
        except (ImportError, RuntimeError):
            pass
        return 1
    if backend in {"mps", "coreml"}:
        return 2
    return 1


def _system_memory_gib() -> float | None:
    """Return VM/container-visible memory, preferring cgroup limits."""
    limits: list[int] = []
    for path in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            value = path.read_text(encoding="ascii").strip()
            if value != "max":
                limit = int(value)
                if limit > 0:
                    limits.append(limit)
        except (OSError, ValueError):
            pass

    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        if pages > 0 and page_size > 0:
            limits.append(pages * page_size)
    except (AttributeError, OSError, TypeError, ValueError):
        pass

    if not limits:
        return None
    return min(limits) / (1024 ** 3)


def _automatic_cpu_tuning(
    backend: str,
    memory_gib: float | None,
) -> tuple[int | None, float | None]:
    """Choose bounded-memory MDXC and file chunk sizes for CPU inference."""
    if backend != "cpu":
        return None, None
    if memory_gib is not None and memory_gib <= 4.0:
        return 64, 120.0
    if memory_gib is None or memory_gib <= 8.0:
        return 128, 300.0
    if memory_gib <= 12.0:
        return 128, 600.0
    return None, None


def _is_oom_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        isinstance(exc, MemoryError)
        or "out of memory" in message
        or "cuda oom" in message
        or "mps backend out of memory" in message
    )


DEFAULT_MODEL = "BS-Roformer-SW.ckpt"

STEM_NAME_MAP: dict[str, str] = {
    "Vocals": "Vocals",
    "Drums": "Drums",
    "Bass": "Bass",
    "Other": "Other",
    "Guitar": "Guitar",
    "Piano": "Piano",
    "Instrumental": "Instrumental",
    "Lead Vocals": "Lead Vocals",
    "Backing Vocals": "Backing Vocals",
    "No Vocals": "Instrumental",
    "Reverb": "Other",
    "No Reverb": "Vocals",
    # NOTE: verify exact tag strings if model output filenames differ
    "Kick":   "Kick",
    "Snare":  "Snare",
    "Toms":   "Toms",
    "HH":     "Hi-Hat",   # some models abbreviate hi-hat as "HH"
    "Hi-Hat": "Hi-Hat",
    "Ride":   "Ride",
    "Crash":  "Crash",
    # NOTE: this model tags its residual as "(other)" — same tag as the primary
    "Crowd":    "Crowd",
    "No Crowd": "_crowd_other",   # kept as fallback in case model config changes
}


MODEL_STEM_OVERRIDES: dict[str, dict[str, str]] = {
    "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt": {
        "other": "_crowd_other",
    },
    # Fed the isolated Vocals stem, this model emits the lead as "Vocals" and
    # the backing vocal residual as "Instrumental".
    "mel_band_roformer_karaoke_gabox_v2.ckpt": {
        "vocals": "Lead Vocals",
        "instrumental": "Backing Vocals",
    },
}


class _ForwardHandler(logging.Handler):
    """Re-emit audio-separator log records through the upmixer logger.

    Installed on the ``audio_separator.separator.separator`` logger so that
    audio-separator's internal messages are visible when the ``upmixer``
    logger is set to DEBUG, and suppressed otherwise.
    """

    def __init__(self, target: logging.Logger) -> None:
        super().__init__()
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        self._target.log(record.levelno, "[separator] %s", record.getMessage())


def _check_import() -> None:
    try:
        import audio_separator.separator  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "audio-separator not installed. "
            "Run: pip install 'audio-separator[cpu]'  (or [gpu] for CUDA)"
        ) from e


class StemSeparator:
    """Wraps audio-separator to extract instrument stems from an audio file.

    Separation is file-based (audio-separator writes to disk); stems are
    loaded back as numpy arrays after processing.

    A single persistent temporary directory is used for all separate() calls
    on this instance.  The underlying Separator object (and loaded model
    weights) are kept alive across calls so the model is loaded only once —
    a major runtime saving when processing multiple zones.

    Individual stem files are deleted immediately after reading to keep disk
    usage bounded.  The persistent temp dir is removed when close() is called
    or the instance is garbage-collected.

    Args:
        model: Model filename. Demucs 4-stem and RoFormer 2-stem are both supported.
        model_dir: Where models are cached. Defaults to ~/.cache/upmixer-models.
        sample_rate: Output sample rate for stems. audio-separator resamples
            internally so stems are returned at exactly this rate.
        log_level: Python logging level forwarded to audio-separator's internal
            logger.  Defaults to ``logging.WARNING`` (suppress verbose output).
            Pass ``logging.DEBUG`` to surface all internal separation messages
            through the ``upmixer`` logger.
        batch_size: Full-precision inference batch size. ``None`` selects a
            backend-aware value.
        segment_size: MDXC segment size. ``None`` selects a VM-memory-aware CPU
            value and keeps the model value on accelerators.
        chunk_duration_s: Long-file chunk duration. ``None`` enables bounded
            chunks on low-memory CPU systems and disables them elsewhere.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        model_dir: str | None = None,
        sample_rate: int = 44100,
        log_level: int = logging.WARNING,
        batch_size: int | None = None,
        segment_size: int | None = None,
        chunk_duration_s: float | None = None,
    ) -> None:
        if batch_size is not None and batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if segment_size is not None and segment_size < 1:
            raise ValueError("segment_size must be at least 1")
        if chunk_duration_s is not None and chunk_duration_s <= 0:
            raise ValueError("chunk_duration_s must be greater than 0")
        self._model = model
        self._model_dir = model_dir or str(
            Path.home() / ".cache" / "upmixer-models"
        )
        self._sample_rate = sample_rate
        self._log_level = log_level
        self._backend = _detect_backend()
        remembered = _SUCCESSFUL_BATCHES.get((model, self._backend))
        self._batch_size = (
            batch_size
            or remembered
            or _automatic_batch_size(self._backend)
        )
        self._batch_size_is_auto = batch_size is None
        auto_segment, auto_chunk = _automatic_cpu_tuning(
            self._backend, _system_memory_gib()
        )
        self._segment_size = (
            segment_size if segment_size is not None else auto_segment
        )
        self._chunk_duration_s = (
            chunk_duration_s if chunk_duration_s is not None else auto_chunk
        )
        self._loaded_sep = None
        self._tmp_dir: str | None = None

    @property
    def backend(self) -> str:
        """Inference backend selected by audio-separator dependencies."""
        return self._backend

    def _ensure_tmp_dir(self) -> str:
        """Return (creating if needed) the persistent temp directory."""
        if self._tmp_dir is None or not os.path.exists(self._tmp_dir):
            self._tmp_dir = tempfile.mkdtemp(prefix="upmixer_stems_")
        return self._tmp_dir

    def _get_separator(self) -> object:
        """Return a ready Separator, loading the model only on first call.

        Always uses the persistent _tmp_dir so the output_dir never changes
        between calls — avoids stale path issues after temp-dir cleanup.

        audio-separator log records are intercepted and re-emitted through
        the ``upmixer`` logger so callers control verbosity uniformly.
        """
        _check_import()
        from audio_separator.separator import Separator

        if self._loaded_sep is None:
            _as_log = logging.getLogger("audio_separator.separator.separator")
            if not any(isinstance(h, _ForwardHandler) for h in _as_log.handlers):
                _as_log.addHandler(_ForwardHandler(_log))
            _as_log.propagate = False
            _as_log.setLevel(self._log_level)

            kwargs = {
                "model_file_dir": self._model_dir,
                "output_dir": self._ensure_tmp_dir(),
                "output_format": "WAV",
                "sample_rate": self._sample_rate,
                "normalization_threshold": 0.9,
                "log_level": self._log_level,
            }
            parameters = inspect.signature(Separator).parameters
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in parameters.values()
            )
            if "use_soundfile" in parameters or accepts_kwargs:
                kwargs["use_soundfile"] = True
            if "use_autocast" in parameters or accepts_kwargs:
                kwargs["use_autocast"] = False
            if (
                self._chunk_duration_s is not None
                and ("chunk_duration" in parameters or accepts_kwargs)
            ):
                kwargs["chunk_duration"] = self._chunk_duration_s
            if "mdxc_params" in parameters or accepts_kwargs:
                mdxc_params = {"batch_size": self._batch_size}
                if self._segment_size is not None:
                    mdxc_params.update(
                        segment_size=self._segment_size,
                        override_model_segment_size=True,
                    )
                kwargs["mdxc_params"] = mdxc_params

            _log.info(
                "  Separator backend=%s batch=%d segment=%s chunk=%s precision=float32",
                self._backend,
                self._batch_size,
                self._segment_size or "model",
                f"{self._chunk_duration_s:g}s" if self._chunk_duration_s else "off",
            )
            self._loaded_sep = Separator(**kwargs)
            self._loaded_sep.load_model(model_filename=self._model)

        return self._loaded_sep

    def _separate_paths(self, audio_path: str) -> list[str]:
        """Separate with progressively lower-memory retries after OOM."""
        while True:
            try:
                started = time.monotonic()
                paths = self._get_separator().separate(audio_path)
                if self._batch_size_is_auto:
                    _SUCCESSFUL_BATCHES[(self._model, self._backend)] = self._batch_size
                _log.info(
                    "  Separator model=%s inference+output=%.2fs",
                    self._model, time.monotonic() - started,
                )
                return paths
            except Exception as exc:
                if not _is_oom_error(exc):
                    raise
                old_settings = (
                    self._batch_size,
                    self._segment_size,
                    self._chunk_duration_s,
                )
                if self._batch_size > 1:
                    self._batch_size = max(1, self._batch_size // 2)
                elif self._backend == "cpu" and (
                    self._segment_size is None
                    or self._segment_size > _MIN_CPU_SEGMENT_SIZE
                ):
                    current = self._segment_size or 256
                    self._segment_size = max(_MIN_CPU_SEGMENT_SIZE, current // 2)
                elif self._backend == "cpu" and (
                    self._chunk_duration_s is None
                    or self._chunk_duration_s > _MIN_CPU_CHUNK_DURATION_S
                ):
                    current = self._chunk_duration_s or 600.0
                    self._chunk_duration_s = max(
                        _MIN_CPU_CHUNK_DURATION_S, current / 2.0
                    )
                else:
                    raise
                _log.warning(
                    "  Separator OOM at batch=%d segment=%s chunk=%s; "
                    "retrying batch=%d segment=%s chunk=%s",
                    old_settings[0],
                    old_settings[1] or "model",
                    old_settings[2] or "off",
                    self._batch_size,
                    self._segment_size or "model",
                    self._chunk_duration_s or "off",
                )
                self._loaded_sep = None
                gc.collect()
                try:
                    import torch
                    if self._backend == "cuda":
                        torch.cuda.empty_cache()
                except (ImportError, RuntimeError):
                    pass

    def separate(
        self,
        audio_path: str,
        output_dir: str | None = None,
    ) -> dict[str, np.ndarray]:
        """Separate audio into stems.

        Args:
            audio_path: Path to input audio file (any format/channel count).
            output_dir: Ignored (kept for API compatibility). Stems are always
                written to the instance's persistent temp directory.

        Returns:
            Dict mapping canonical stem name to numpy array (n_samples, 2) float32.
            Unknown/unrecognised stem names are silently skipped.
        """
        tmp_dir = self._ensure_tmp_dir()
        output_paths = self._separate_paths(audio_path)

        _overrides = MODEL_STEM_OVERRIDES.get(self._model)
        stems: dict[str, np.ndarray] = {}
        for path in output_paths:
            full_path = path if os.path.isabs(path) else os.path.join(tmp_dir, path)
            stem_name = _parse_stem_name(full_path, _overrides)
            if stem_name is None:
                try:
                    os.unlink(full_path)
                except OSError:
                    pass
                continue
            try:
                audio, _ = sf.read(full_path, dtype="float32", always_2d=True)
            except Exception as exc:
                _log.warning(
                    "Skipping stem '%s' — could not read '%s': %s",
                    stem_name, os.path.basename(full_path), exc,
                )
                try:
                    os.unlink(full_path)
                except OSError:
                    pass
                continue
            if audio.shape[1] == 1:
                audio = np.concatenate([audio, audio], axis=1)
            stems[stem_name] = audio
            try:
                os.unlink(full_path)
            except OSError:
                pass

        return stems

    def separate_to_file(
        self,
        audio_path: str,
        keep_on_disk: frozenset[str],
    ) -> tuple[dict[str, np.ndarray], dict[str, str]]:
        """Separate audio, keeping specified stems as on-disk WAV files.

        Used by the multi-stage pipeline to pass intermediate stems (e.g. the
        crowd residual or the Drums stem) directly to the next model stage
        without loading them into memory.

        Args:
            audio_path:   Input file path.
            keep_on_disk: Canonical stem names to leave as WAV files on disk.
                          Their paths are returned so the next pipeline stage
                          can use them as input.  The caller is responsible for
                          cleanup once the files are no longer needed.

        Returns:
            ``(loaded, on_disk)`` where:
              ``loaded``  — canonical_name → ndarray for stems NOT in keep_on_disk.
              ``on_disk`` — canonical_name → absolute WAV path for kept stems.
        """
        tmp_dir = self._ensure_tmp_dir()
        output_paths = self._separate_paths(audio_path)

        _log.debug(
            "[separator] model=%s produced %d output file(s): %s",
            self._model,
            len(output_paths),
            [os.path.basename(p) for p in output_paths],
        )

        _overrides = MODEL_STEM_OVERRIDES.get(self._model)
        loaded: dict[str, np.ndarray] = {}
        on_disk: dict[str, str] = {}

        for path in output_paths:
            full = path if os.path.isabs(path) else os.path.join(tmp_dir, path)
            stem_name = _parse_stem_name(full, _overrides)
            _log.debug(
                "[separator] %s → stem_name=%r  keep_on_disk=%s",
                os.path.basename(full),
                stem_name,
                stem_name in keep_on_disk if stem_name else "N/A (unrecognised)",
            )
            if stem_name is None:
                _log.warning(
                    "[separator] Unrecognised stem tag in filename '%s' — "
                    "add an entry to STEM_NAME_MAP to handle this model output. "
                    "File will be discarded.",
                    os.path.basename(full),
                )
                try:
                    os.unlink(full)
                except OSError:
                    pass
                continue

            if stem_name in keep_on_disk:
                on_disk[stem_name] = full
                continue

            try:
                audio, _ = sf.read(full, dtype="float32", always_2d=True)
            except Exception as exc:
                _log.warning(
                    "Skipping stem '%s' — could not read '%s': %s",
                    stem_name, os.path.basename(full), exc,
                )
                try:
                    os.unlink(full)
                except OSError:
                    pass
                continue

            if audio.shape[1] == 1:
                audio = np.concatenate([audio, audio], axis=1)
            loaded[stem_name] = audio
            try:
                os.unlink(full)
            except OSError:
                pass

        _log.debug(
            "[separator] stage done — loaded=%s  on_disk=%s",
            sorted(loaded.keys()),
            sorted(on_disk.keys()),
        )
        return loaded, on_disk

    def close(self) -> None:
        """Remove the persistent temp directory and release the Separator."""
        import shutil
        if self._tmp_dir and os.path.exists(self._tmp_dir):
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        had_loaded_model = self._loaded_sep is not None
        self._loaded_sep = None
        if had_loaded_model:
            gc.collect()

    def __enter__(self) -> "StemSeparator":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


def _parse_stem_name(
    path: str,
    model_overrides: dict[str, str] | None = None,
) -> str | None:
    """Extract canonical stem label from audio-separator output filename.

    audio-separator names output files like:
        song_(Vocals)_model_name.wav
        song_(Lead Vocals)_model_name.wav

    In multi-stage pipelines the intermediate filename is embedded in the
    next stage's output filename, e.g.:
        song_(other)_crowd_model_(Piano)_primary_model.wav
                      ^^^^ intermediate tag ^^^^  ^^^^ current stage tag

    To correctly identify the current-stage stem, this function finds the
    **rightmost** matching tag in the filename.  Tags from intermediate
    stages always appear earlier (leftward) than the current stage's tag.

    Args:
        path:             Output file path from audio-separator.
        model_overrides:  Per-model tag→canonical mapping that takes precedence
                          over the general STEM_NAME_MAP when two tags occur at
                          the same position (i.e. the current model's own tag).
                          Keys must be lowercase.

    Returns:
        Canonical stem name, or ``None`` if no known tag is found.
    """
    name = os.path.basename(path).lower()

    best_pos: int = -1
    best_canonical: str | None = None

    if model_overrides:
        for tag, canonical in model_overrides.items():
            pos = name.rfind(f"({tag})")
            if pos > best_pos:
                best_pos = pos
                best_canonical = canonical

    for tag, canonical in STEM_NAME_MAP.items():
        pos = name.rfind(f"({tag.lower()})")
        if pos > best_pos:
            best_pos = pos
            best_canonical = canonical

    return best_canonical
