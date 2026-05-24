from pathlib import Path

import numpy as np
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP


class AudioWriter:
    """Writes multichannel audio in WAV format."""

    def __init__(self, file_path: str | Path, sample_rate: int, config: UpmixConfig):
        self._path = Path(file_path)
        self._sample_rate = sample_rate
        self._config = config
        self._format = FORMAT_MAP[config.output_format]

    def write(self, channels: dict[str, np.ndarray]) -> None:
        """Accepts dict mapping channel name -> 1D array.

        Stacks in correct channel order and writes as multichannel WAV.
        """
        ordered = []
        for label in self._format.channels:
            key = label.value
            if key not in channels:
                raise ValueError(
                    f"Missing channel '{key}' for {self._format.name} output"
                )
            ordered.append(channels[key])

        output = np.column_stack(ordered)

        sf.write(
            str(self._path),
            output,
            self._sample_rate,
            subtype=self._config.output_subtype,
        )
