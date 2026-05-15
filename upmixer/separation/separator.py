"""Thin wrapper around python-audio-separator for stem extraction."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


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

    Args:
        model: Model filename. Demucs 4-stem and RoFormer 2-stem are both supported.
        model_dir: Where models are cached. Defaults to ~/.cache/upmixer-models.
        sample_rate: Output sample rate for stems (resampled if differs from source).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        model_dir: str | None = None,
        sample_rate: int = 44100,
    ) -> None:
        self._model = model
        self._model_dir = model_dir or str(
            Path.home() / ".cache" / "upmixer-models"
        )
        self._sample_rate = sample_rate

    def separate(
        self,
        audio_path: str,
        output_dir: str | None = None,
    ) -> dict[str, np.ndarray]:
        """Separate audio into stems.

        Args:
            audio_path: Path to input audio file (any format/channel count).
            output_dir: Where to write temporary stem files. Uses a temp dir if None.

        Returns:
            Dict mapping canonical stem name to numpy array (n_samples, 2) float32.
            Unknown/unrecognised stem names are silently skipped.
        """
        from audio_separator.separator import Separator

        _check_import()
        use_tmp = output_dir is None
        tmp_dir = tempfile.mkdtemp(prefix="upmixer_stems_") if use_tmp else output_dir

        try:
            sep = Separator(
                model_file_dir=self._model_dir,
                output_dir=tmp_dir,
                output_format="WAV",
                sample_rate=self._sample_rate,
                normalization_threshold=0.9,
            )
            sep.load_model(model_filename=self._model)
            output_paths = sep.separate(audio_path)

            stems: dict[str, np.ndarray] = {}
            for path in output_paths:
                # audio-separator returns basenames; resolve against output_dir
                full_path = path if os.path.isabs(path) else os.path.join(tmp_dir, path)
                stem_name = _parse_stem_name(full_path)
                if stem_name is None:
                    continue
                audio, _ = sf.read(full_path, dtype="float32", always_2d=True)
                # Ensure stereo — mono models may write single-channel
                if audio.shape[1] == 1:
                    audio = np.concatenate([audio, audio], axis=1)
                stems[stem_name] = audio  # (n_samples, 2)

        finally:
            if use_tmp:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        return stems


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
