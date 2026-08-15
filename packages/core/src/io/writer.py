from pathlib import Path

import numpy as np
import soundfile as sf

from upmixer.codecs import get_codec
from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, OutputFormat
from upmixer.io.atomic import atomic_output_path

_OGG_WRITE_CHUNK_SECONDS = 10


def write_audio(
    path: str | Path,
    audio: np.ndarray,
    sample_rate: int,
    codec_name: str,
    output_subtype: str,
) -> None:
    """Write interleaved audio through a codec, publishing atomically."""
    codec = get_codec(codec_name)
    subtype = codec.fixed_subtype or output_subtype
    with atomic_output_path(Path(path)) as temporary:
        if codec.container == "OGG":
            # libsndfile's OGG encoders need stack proportional to the whole
            # buffer when handed it in one sf.write() call, which overflows the
            # thread stack (SIGBUS/SIGSEGV) on long tracks. Fixed-size chunks
            # keep their per-call stack use bounded regardless of length.
            chunk_frames = sample_rate * _OGG_WRITE_CHUNK_SECONDS
            with sf.SoundFile(
                str(temporary), "w",
                samplerate=sample_rate, channels=audio.shape[1],
                format=codec.container, subtype=subtype,
            ) as handle:
                for start in range(0, len(audio), chunk_frames):
                    handle.write(audio[start:start + chunk_frames])
        else:
            sf.write(
                str(temporary), audio, sample_rate,
                format=codec.container, subtype=subtype,
            )
        info = sf.info(str(temporary))
        if info.samplerate != sample_rate or info.channels != audio.shape[1]:
            raise RuntimeError("Written audio metadata does not match requested output")


class AudioWriter:
    """Writes multichannel audio through the configured delivery codec."""

    def __init__(
        self,
        file_path: str | Path,
        sample_rate: int,
        config: UpmixConfig,
        output_format: OutputFormat | None = None,
    ):
        self._path = Path(file_path)
        self._sample_rate = sample_rate
        self._config = config
        self._format = output_format if output_format is not None else FORMAT_MAP[config.output_format]

    def write(self, channels: dict[str, np.ndarray]) -> None:
        """Accepts dict mapping channel name -> 1D array.

        Stacks in correct channel order and writes as multichannel audio.
        """
        ordered = []
        for label in self._format.channels:
            key = label.value
            if key not in channels:
                raise ValueError(
                    f"Missing channel '{key}' for {self._format.name} output"
                )
            ordered.append(channels[key])

        write_audio(
            self._path,
            np.column_stack(ordered),
            self._sample_rate,
            self._config.output_codec,
            self._config.output_subtype,
        )
