from pathlib import Path

import numpy as np
import soundfile as sf

from upmixer.formats import InputFormat, detect_input_format


class AudioReader:
    """Reads audio files of any supported channel count."""

    def __init__(self, file_path: str | Path):
        self._path = Path(file_path)
        info = sf.info(str(self._path))
        self._sample_rate = info.samplerate
        self._n_samples = info.frames
        self._channels = info.channels

    def read(self, dtype: str = "float64") -> tuple[np.ndarray, int]:
        """Returns audio data and sample rate using requested NumPy dtype."""
        audio, sr = sf.read(str(self._path), dtype=dtype)
        if audio.ndim == 1:
            audio = audio[:, np.newaxis]
        return audio, sr

    def detect_format(self) -> InputFormat:
        """Auto-detect input format from file channel count."""
        return detect_input_format(self._channels)

    @property
    def duration_seconds(self) -> float:
        return self._n_samples / self._sample_rate

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def n_samples(self) -> int:
        return self._n_samples

    @property
    def n_channels(self) -> int:
        return self._channels
