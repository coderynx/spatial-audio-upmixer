from pathlib import Path

import numpy as np
import soundfile as sf


class AudioReader:
    """Reads audio files and validates they are stereo."""

    def __init__(self, file_path: str | Path):
        self._path = Path(file_path)
        info = sf.info(str(self._path))
        self._sample_rate = info.samplerate
        self._n_samples = info.frames
        self._channels = info.channels

    def read(self) -> tuple[np.ndarray, int]:
        """Returns (audio_data [n_samples, 2], sample_rate).

        Raises ValueError if input is not stereo.
        """
        if self._channels != 2:
            raise ValueError(
                f"Expected stereo input (2 channels), got {self._channels} channels"
            )

        audio, sr = sf.read(str(self._path), dtype="float64")
        return audio, sr

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
