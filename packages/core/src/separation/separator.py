"""In-core stem separation engine (Torch models plus an MLX SCNet adapter)."""

from __future__ import annotations

import gc
import logging
import os
import platform
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf

from .inference.scnet_worker import SCNetWorker

if TYPE_CHECKING:
    from .inference.device import DeviceManager
    from .inference.engine import SeparationEngine

_log = logging.getLogger(__name__)

_SUCCESSFUL_BATCHES: dict[tuple[str, str], int] = {}

_MIN_CPU_SEGMENT_SIZE = 64
_MIN_CPU_CHUNK_DURATION_S = 60.0


def _detect_backend() -> str:
    """Return accelerator used by torch models without requiring torch."""
    try:
        import torch

        if torch.cuda.is_available():
            if getattr(torch.version, "hip", None):
                _log.debug(
                    "ROCm build detected (torch.version.hip=%s); using cuda backend path",
                    torch.version.hip,
                )
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
    """Choose safe full-precision inference batching for each backend."""
    if backend == "cuda":
        try:
            import torch

            free_bytes, _ = torch.cuda.mem_get_info()
            free_gib = free_bytes / (1024**3)
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


def _mlx_scnet_available() -> bool:
    """Return whether the MLX SCNet backend is usable on this host."""
    if platform.system().lower() != "darwin" or platform.machine().lower() != "arm64":
        return False
    try:
        import mlx.core  # noqa: F401
        import mlx_spectro  # noqa: F401
    except (ImportError, RuntimeError):
        return False
    return True


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
    return min(limits) / (1024**3)


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


def _check_import() -> None:
    try:
        import torch  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "PyTorch not installed. "
            "Run: pip install 'upmixer[separation-cpu]'  (or [separation-gpu] for CUDA)"
        ) from e


def _is_oom_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        isinstance(exc, MemoryError)
        or "out of memory" in message
        or "cuda oom" in message
        or "mps backend out of memory" in message
    )


def _remove_empty_output_dirs(paths: list[str], root: str) -> None:
    root_path = Path(root)
    parents = {
        Path(path if os.path.isabs(path) else os.path.join(root, path)).parent
        for path in paths
    }
    for parent in parents:
        if parent.parent == root_path:
            try:
                parent.rmdir()
            except OSError:
                pass


DEFAULT_MODEL = "BS-Roformer-SW.ckpt"
_SCNET_MPS_CPU_MODEL = "model_scnet_ep_36_sdr_10.0891.ckpt"

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
    "Kick": "Kick",
    "Snare": "Snare",
    "Toms": "Toms",
    "HH": "Hi-Hat",  # some models abbreviate hi-hat as "HH"
    "Hi-Hat": "Hi-Hat",
    "Ride": "Ride",
    "Crash": "Crash",
    "Crowd": "Crowd",
    "No Crowd": "_crowd_other",  # kept as fallback in case model config changes
}


MODEL_STEM_OVERRIDES: dict[str, dict[str, str]] = {
    "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt": {
        "other": "_crowd_other",
    },
    "becruily_deux.ckpt": {
        "instrumental": "_deux_inst",
    },
    # Fed the isolated Vocals stem, this model emits the lead as "Vocals" and
    # the backing vocal residual as "Instrumental".
    "mel_band_roformer_karaoke_gabox_v2.ckpt": {
        "vocals": "Lead Vocals",
        "instrumental": "Backing Vocals",
    },
}


class StemSeparator:
    """Separates an audio file into instrument stems using the in-core engine.

    Separation is file-based (stems are written to a temp directory as they
    are produced, then loaded back as numpy arrays) to match the disk-backed
    chaining the multi-stage pipeline relies on.

    A single persistent temporary directory is used for all separate() calls
    on this instance. The underlying model weights are kept alive across
    calls so the model is loaded only once — a major runtime saving when
    processing multiple zones.

    Individual stem files are deleted immediately after reading to keep disk
    usage bounded. The persistent temp dir is removed when close() is called
    or the instance is garbage-collected.

    Args:
        model: Model filename. See ``inference/registry.py`` for the
            registered checkpoints and their architectures.
        model_dir: Where model weights are cached. Defaults to
            ~/.cache/upmixer-models.
        sample_rate: Output sample rate for stems. The mix is resampled to
            exactly this rate before separation, so stems are returned at
            exactly this rate.
        batch_size: TFC-TDF chunk batch size (ignored by Roformer models,
            which do not batch). ``None`` selects a backend-aware value.
        segment_size: Chunk frame count. ``None`` selects a VM-memory-aware
            CPU value and keeps the model's own default on accelerators.
        chunk_duration_s: Long-file chunk duration. ``None`` enables bounded
            chunks on low-memory CPU systems and disables them elsewhere.
        overlap: Overlapping windows per demix chunk. ``None`` selects the
            community-default (2). These are quality knobs, not memory
            knobs, so the OOM back-off ladder never adjusts them.
        tta: Test-time augmentation (average predictions over polarity and
            channel-swap variants). Off by default; ~3x slower when on.
        pitch_shift: Optional pitch-register rescue trick — resamples the
            mix by this ratio before separation and back afterward. ``None``
            disables it.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        model_dir: str | None = None,
        sample_rate: int = 44100,
        batch_size: int | None = None,
        segment_size: int | None = None,
        chunk_duration_s: float | None = None,
        overlap: int | None = None,
        tta: bool = False,
        pitch_shift: float | None = None,
    ) -> None:
        if batch_size is not None and batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if segment_size is not None and segment_size < 1:
            raise ValueError("segment_size must be at least 1")
        if chunk_duration_s is not None and chunk_duration_s <= 0:
            raise ValueError("chunk_duration_s must be greater than 0")
        if overlap is not None and overlap < 1:
            raise ValueError("overlap must be at least 1")
        if pitch_shift is not None and pitch_shift <= 0:
            raise ValueError("pitch_shift must be greater than 0")
        self._model = model
        self._model_dir = model_dir or str(Path.home() / ".cache" / "upmixer-models")
        self._sample_rate = sample_rate
        detected_backend = _detect_backend()
        if model == _SCNET_MPS_CPU_MODEL:
            if _mlx_scnet_available():
                _log.info("MLX backend available for SCNet model %s", model)
                detected_backend = "mlx"
            elif detected_backend == "mps":
                _log.warning(
                    "SCNet XL IHF is not reliable on MPS; using CPU backend for %s",
                    model,
                )
                detected_backend = "cpu"
        self._backend = detected_backend
        remembered = _SUCCESSFUL_BATCHES.get((model, self._backend))
        self._batch_size = (
            batch_size or remembered or _automatic_batch_size(self._backend)
        )
        self._batch_size_is_auto = batch_size is None
        auto_segment, auto_chunk = _automatic_cpu_tuning(
            self._backend, _system_memory_gib()
        )
        self._segment_size = segment_size if segment_size is not None else auto_segment
        self._chunk_duration_s = (
            chunk_duration_s if chunk_duration_s is not None else auto_chunk
        )
        self._overlap = overlap
        self._tta = tta
        self._pitch_shift = pitch_shift
        self._device_manager: DeviceManager | None = None
        self._engine: SeparationEngine | None = None
        self._scnet_worker: SCNetWorker | None = None
        self._tmp_dir: str | None = None

    @property
    def backend(self) -> str:
        """Inference backend selected for this model (cuda/mps/mlx/cpu)."""
        return self._backend

    def _ensure_tmp_dir(self) -> str:
        """Return (creating if needed) the persistent temp directory."""
        if self._tmp_dir is None or not os.path.exists(self._tmp_dir):
            self._tmp_dir = tempfile.mkdtemp(prefix="upmixer_stems_")
        return self._tmp_dir

    def _get_separator(self) -> "SeparationEngine":
        """Return a ready SeparationEngine, loading the model only on first call.

        Always uses the persistent _tmp_dir so the output_dir never changes
        between calls — avoids stale path issues after temp-dir cleanup.

        Torch, MLX, and the rest of the inference stack are imported lazily here
        (not at module scope) so that importing this module — and building
        ``StemUpmixPipeline`` or probing ``.backend`` — does not require the
        optional ``separation`` extra to be installed.
        """
        if self._engine is None:
            _check_import()
            from .inference.device import DeviceManager
            from .inference.engine import SeparationEngine
            from .inference.registry import get_model_spec

            if self._device_manager is None:
                self._device_manager = DeviceManager(self._backend)

            spec = get_model_spec(self._model)
            if self._backend == "mlx":
                from .inference.scnet_mlx import load_model

                model, config = load_model(self._model, self._model_dir)
            else:
                from .inference.loader import load_model

                model, config = load_model(
                    self._model, self._device_manager.torch_device, self._model_dir
                )

            _log.info(
                "  Separator backend=%s batch=%d segment=%s chunk=%s precision=float32",
                self._backend,
                self._batch_size,
                self._segment_size or "model",
                f"{self._chunk_duration_s:g}s" if self._chunk_duration_s else "off",
            )
            self._engine = SeparationEngine(
                model=model,
                config=config,
                arch=spec.arch,
                model_filename=self._model,
                device=self._device_manager,
                output_dir=self._ensure_tmp_dir(),
                sample_rate=self._sample_rate,
                batch_size=self._batch_size,
                segment_size=self._segment_size,
                chunk_duration_s=self._chunk_duration_s,
                overlap=self._overlap,
                default_chunk_samples=spec.default_chunk_samples,
                tta=self._tta,
                pitch_shift=self._pitch_shift,
            )

        return self._engine

    def _get_scnet_worker(self) -> SCNetWorker:
        """Return the persistent MLX SCNet worker for this separator."""
        if self._scnet_worker is None:
            self._scnet_worker = SCNetWorker(self._model, self._model_dir)
        return self._scnet_worker

    def _separate_paths(
        self,
        audio_path: str,
        retain_parent: bool = False,
        wanted: frozenset[str] | None = None,
        progress_callback: Callable[[float], None] | None = None,
    ) -> list[str]:
        """Separate with progressively lower-memory retries after OOM."""
        while True:
            engine = None
            try:
                started = time.monotonic()
                if (
                    self._backend == "mlx"
                    and self._model == _SCNET_MPS_CPU_MODEL
                    and self._engine is None
                ):
                    worker = self._get_scnet_worker()
                    paths = worker.separate(
                        audio_path,
                        self._ensure_tmp_dir(),
                        sample_rate=self._sample_rate,
                        batch_size=self._batch_size,
                        segment_size=self._segment_size,
                        chunk_duration_s=self._chunk_duration_s,
                        overlap=self._overlap,
                        tta=self._tta,
                        pitch_shift=self._pitch_shift,
                        retain_parent=retain_parent,
                        wanted=wanted,
                        progress_callback=progress_callback,
                    )
                else:
                    engine = self._get_separator()
                    kwargs: dict[str, object] = {}
                    if retain_parent:
                        kwargs["retain_parent"] = True
                    if progress_callback is not None:
                        kwargs["progress_callback"] = progress_callback
                    if wanted is not None:
                        kwargs["wanted"] = wanted
                    paths = engine.separate(audio_path, **kwargs)
                if self._batch_size_is_auto:
                    _SUCCESSFUL_BATCHES[(self._model, self._backend)] = self._batch_size
                _log.info(
                    "  Separator model=%s inference+output=%.2fs",
                    self._model,
                    time.monotonic() - started,
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
                    self._engine = None
                    engine = None
                    worker = self._scnet_worker
                    self._scnet_worker = None
                    if worker is not None:
                        worker.terminate()
                    traceback.clear_frames(exc.__traceback__)
                    del exc
                    if self._device_manager is not None:
                        self._device_manager.empty_cache()
                    else:
                        gc.collect()
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
                self._engine = None
                engine = None
                traceback.clear_frames(exc.__traceback__)
                del exc
                if self._device_manager is not None:
                    self._device_manager.empty_cache()
                else:
                    gc.collect()

    def separate(
        self,
        audio_path: str,
    ) -> dict[str, np.ndarray]:
        """Separate audio into stems.

        Args:
            audio_path: Path to input audio file (any format/channel count).

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
                    stem_name,
                    os.path.basename(full_path),
                    exc,
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

        _remove_empty_output_dirs(output_paths, tmp_dir)
        return stems

    def separate_to_file(
        self,
        audio_path: str,
        keep_on_disk: frozenset[str],
        stem_overrides: dict[str, str] | None = None,
        wanted: frozenset[str] | None = None,
        retain_parent: bool = False,
        progress_callback: Callable[[float], None] | None = None,
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
            stem_overrides: Per-call tag→canonical mapping replacing this
                          model's ``MODEL_STEM_OVERRIDES`` entry, for a model
                          whose output names depend on what it was fed.
            wanted:       Canonical names this stage is allowed to contribute.
                          A model emits every stem it was trained on, which is
                          not always what the stage is for — the primary model
                          run on a vocals-free residual still writes a Vocals
                          file, and letting it through overwrites the real one.
                          ``None`` accepts everything the model emits.
            retain_parent: Keep the exact resampled stage input until
                          :meth:`take_last_parent` consumes it.

        Returns:
            ``(loaded, on_disk)`` where:
              ``loaded``  — canonical_name → ndarray for stems NOT in keep_on_disk.
              ``on_disk`` — canonical_name → absolute WAV path for kept stems.
        """
        tmp_dir = self._ensure_tmp_dir()
        output_paths = self._separate_paths(
            audio_path,
            retain_parent=retain_parent,
            wanted=wanted,
            progress_callback=progress_callback,
        )

        _log.debug(
            "[separator] model=%s produced %d output file(s): %s",
            self._model,
            len(output_paths),
            [os.path.basename(p) for p in output_paths],
        )

        _overrides = stem_overrides or MODEL_STEM_OVERRIDES.get(self._model)
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

            if wanted is not None and stem_name not in wanted:
                _log.debug(
                    "[separator] %s → %r not declared by this stage — discarded",
                    os.path.basename(full),
                    stem_name,
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
                    stem_name,
                    os.path.basename(full),
                    exc,
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

        _remove_empty_output_dirs(output_paths, tmp_dir)
        _log.debug(
            "[separator] stage done — loaded=%s  on_disk=%s",
            sorted(loaded.keys()),
            sorted(on_disk.keys()),
        )
        return loaded, on_disk

    def take_last_parent(self) -> np.ndarray:
        """Return the last separation input at the engine's working rate."""
        if self._scnet_worker is not None and self._engine is None:
            return self._scnet_worker.take_last_parent(self._ensure_tmp_dir())
        return self._get_separator().take_last_parent()

    def close(self) -> None:
        """Remove the persistent temp directory and release the loaded model."""
        worker = self._scnet_worker
        self._scnet_worker = None
        if worker is not None:
            worker.close()
        if self._tmp_dir and os.path.exists(self._tmp_dir):
            import shutil

            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        had_loaded_model = self._engine is not None
        device_manager = self._device_manager
        self._engine = None
        self._device_manager = None
        if device_manager is not None:
            device_manager.empty_cache()
        elif had_loaded_model:
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
    """Extract canonical stem label from a separated-stem output filename.

    Stems are named ``song_(Vocals)_model_name.wav`` /
    ``song_(Lead Vocals)_model_name.wav`` (see ``inference/audio_io.py``'s
    ``stem_output_path``, matching python-audio-separator's convention).

    In multi-stage pipelines the intermediate filename is embedded in the
    next stage's output filename, e.g.:
        song_(other)_crowd_model_(Piano)_primary_model.wav
                      ^^^^ intermediate tag ^^^^  ^^^^ current stage tag

    To correctly identify the current-stage stem, this function finds the
    **rightmost** matching tag in the filename.  Tags from intermediate
    stages always appear earlier (leftward) than the current stage's tag.

    Args:
        path:             Output file path from the separation engine.
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
