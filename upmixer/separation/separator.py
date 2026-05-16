"""Thin wrapper around python-audio-separator for stem extraction."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

_log = logging.getLogger("upmixer")


# Default model — htdemucs_ft: 4-stem (drums/bass/vocals/other), good quality/speed
DEFAULT_MODEL = "htdemucs_ft.yaml"

# All stem names audio-separator may produce, mapped to canonical routing names.
# Keys = substring that appears in the (StemName) tag in output filenames.
# Values = canonical name used in DEFAULT_ROUTING in stem_router.py.
STEM_NAME_MAP: dict[str, str] = {
    # 4-stem Demucs
    "Vocals": "Vocals",
    "Drums": "Drums",
    "Bass": "Bass",
    "Other": "Other",
    # 6-stem Demucs (htdemucs_6s)
    "Guitar": "Guitar",
    "Piano": "Piano",
    # RoFormer 2-stem
    "Instrumental": "Instrumental",
    # Karaoke / vocal splitter variants
    "Lead Vocals": "Lead Vocals",
    "Backing Vocals": "Backing Vocals",
    "No Vocals": "Instrumental",
    # De-verb / denoise outputs
    "Reverb": "Other",
    "No Reverb": "Vocals",
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
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        model_dir: str | None = None,
        sample_rate: int = 44100,
        log_level: int = logging.WARNING,
    ) -> None:
        self._model = model
        self._model_dir = model_dir or str(
            Path.home() / ".cache" / "upmixer-models"
        )
        self._sample_rate = sample_rate
        self._log_level = log_level
        self._loaded_sep = None   # loaded lazily, reused across all separate() calls
        self._tmp_dir: str | None = None  # persistent output dir for this instance

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
        from audio_separator.separator import Separator

        if self._loaded_sep is None:
            _check_import()

            # Route audio-separator's internal logger through ours (idempotent).
            _as_log = logging.getLogger("audio_separator.separator.separator")
            if not any(isinstance(h, _ForwardHandler) for h in _as_log.handlers):
                _as_log.addHandler(_ForwardHandler(_log))
            _as_log.propagate = False
            _as_log.setLevel(self._log_level)

            self._loaded_sep = Separator(
                model_file_dir=self._model_dir,
                output_dir=self._ensure_tmp_dir(),
                output_format="WAV",
                sample_rate=self._sample_rate,
                normalization_threshold=0.9,
                log_level=self._log_level,
            )
            self._loaded_sep.load_model(model_filename=self._model)

        return self._loaded_sep

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
        sep = self._get_separator()
        output_paths = sep.separate(audio_path)

        stems: dict[str, np.ndarray] = {}
        for path in output_paths:
            # audio-separator may return basenames or absolute paths
            full_path = path if os.path.isabs(path) else os.path.join(tmp_dir, path)
            stem_name = _parse_stem_name(full_path)
            if stem_name is None:
                try:
                    os.unlink(full_path)
                except OSError:
                    pass
                continue
            audio, _ = sf.read(full_path, dtype="float32", always_2d=True)
            # Ensure stereo — mono models may write single-channel
            if audio.shape[1] == 1:
                audio = np.concatenate([audio, audio], axis=1)
            stems[stem_name] = audio  # (n_samples, 2)
            # Remove stem file immediately after reading to keep disk usage bounded
            try:
                os.unlink(full_path)
            except OSError:
                pass

        return stems

    def close(self) -> None:
        """Remove the persistent temp directory and release the Separator."""
        import shutil
        if self._tmp_dir and os.path.exists(self._tmp_dir):
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        self._loaded_sep = None

    def __del__(self) -> None:
        self.close()


def _parse_stem_name(path: str) -> str | None:
    """Extract canonical stem label from audio-separator output filename.

    audio-separator names files like:
        song_(Vocals)_model_name.wav
        song_(Lead Vocals)_model_name.wav
        song_(Instrumental)_model_name.wav

    Matches against STEM_NAME_MAP keys (longest match first to avoid
    'Vocals' matching before 'Lead Vocals').
    """
    name = os.path.basename(path).lower()
    # Sort by length descending so "Lead Vocals" matches before "Vocals"
    for tag in sorted(STEM_NAME_MAP.keys(), key=len, reverse=True):
        if f"({tag.lower()})" in name:
            return STEM_NAME_MAP[tag]
    return None
