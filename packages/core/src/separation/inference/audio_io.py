"""Audio loading and stem writing for the inference engine.

Loading mirrors the mix-preparation step of MSST-family inference: librosa
loads and resamples to the target sample rate, mono is duplicated to stereo.
The mix is peak-normalized before demix because the models are trained that
way; :func:`normalize` reports the scale it applied so the caller can undo it
on the stems, keeping the written stems in the input's level domain.
"""

from __future__ import annotations

import os
import re
import tempfile

import librosa
import numpy as np
import soundfile as sf


def load_audio(path: str, sample_rate: int) -> np.ndarray:
    """Load an audio file as ``(2, n_samples)`` float32 at ``sample_rate``."""
    mix, _ = librosa.load(path, mono=False, sr=sample_rate)
    if mix.ndim == 1:
        mix = np.stack([mix, mix])
    return np.asarray(mix, dtype=np.float32)


def normalize(wave: np.ndarray, max_peak: float = 0.9) -> tuple[np.ndarray, float]:
    """Peak down-scale ``wave`` to ``max_peak`` if it exceeds it (no up-scale).

    Returns ``(scaled_wave, scale)``; ``scale`` is 1.0 when no down-scale was
    needed. Divide by ``scale`` to return audio to the input's level domain.
    """
    maxv = np.abs(wave).max()
    if maxv > max_peak:
        scale = max_peak / maxv
        return wave * scale, scale
    return wave, 1.0


def sanitize_filename_part(text: str) -> str:
    """Replace filesystem-unsafe characters, matching upstream's convention."""
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", text)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_. ")


def stem_output_path(
    output_dir: str, audio_base: str, stem_name: str, model_filename: str
) -> str:
    """Build the output path using python-audio-separator's filename convention.

    ``separator.py``'s ``_parse_stem_name`` depends on this exact
    ``{base}_({StemName})_{model}.wav`` pattern (rightmost ``(Tag)`` in the
    filename) to identify a produced stem, so the pipeline's disk-chaining
    keeps working with zero changes.
    """
    model_stem = os.path.splitext(model_filename)[0]
    filename = (
        f"{sanitize_filename_part(audio_base)}_({sanitize_filename_part(stem_name)})_"
        f"{sanitize_filename_part(model_stem)}.wav"
    )
    return os.path.join(output_dir, filename)


def write_stem(path: str, stem: np.ndarray, sample_rate: int) -> None:
    """Write a ``(2, n_samples)`` stem array to ``path`` as a float32 WAV."""
    sf.write(path, stem.T, sample_rate, subtype="FLOAT")


class AtomicWavWriter:
    """Append channel-first float32 frames, publishing the WAV on commit."""

    def __init__(self, path: str, sample_rate: int, channels: int) -> None:
        self.path = path
        self._temporary_path: str | None = None
        self._file: sf.SoundFile | None = None
        if channels < 1:
            raise ValueError("WAV writer requires at least one channel")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=os.path.dirname(path) or ".",
        )
        os.close(descriptor)
        self._temporary_path = temporary_path
        try:
            self._file = sf.SoundFile(
                temporary_path,
                mode="w",
                samplerate=sample_rate,
                channels=channels,
                format="WAV",
                subtype="FLOAT",
            )
        except Exception:
            self.abort()
            raise

    def write(self, stem: np.ndarray) -> None:
        """Append a channel-first block to the temporary WAV."""
        if self._file is None:
            raise RuntimeError("WAV writer is closed")
        block = np.asarray(stem, dtype=np.float32)
        if block.ndim != 2 or block.shape[0] != self._file.channels:
            raise ValueError(
                f"Expected ({self._file.channels}, samples) audio, got {block.shape}"
            )
        self._file.write(block.T)

    def close(self) -> None:
        """Close the temporary WAV without publishing it."""
        if self._file is not None:
            self._file.close()
            self._file = None

    def commit(self) -> None:
        """Publish the completed temporary WAV at its final path."""
        self.close()
        if self._temporary_path is None:
            raise RuntimeError("WAV writer has already been finalized")
        os.replace(self._temporary_path, self.path)
        self._temporary_path = None

    def abort(self) -> None:
        """Close and remove the unpublished temporary WAV."""
        self.close()
        if self._temporary_path is not None:
            try:
                os.unlink(self._temporary_path)
            except OSError:
                pass
            self._temporary_path = None
