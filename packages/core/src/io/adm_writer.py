"""ADM BWF (Audio Definition Model / Broadcast Wave Format) writer.

Targets Logic Pro / DaVinci Resolve / Pro Tools ADM BWF import. See
``adm_chunks.py`` for the low-level RIFF/BWF/ADM-XML chunk builders and the
key ADM/Dolby profile design choices they implement.
"""

from __future__ import annotations

import struct

import numpy as np
import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.io.adm_chunks import (
    _audio_to_pcm,
    _axml_chunk,
    _bext_chunk,
    _chna_chunk,
    _chunk_size,
    _dbmd_chunk,
    _fmt_chunk,
    _write_chunk,
)
from upmixer.io.atomic import atomic_output_path
from upmixer.io.writer import dither_channels

_DOLBY_ENGINE_ALLOWED_FORMATS = frozenset({"5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"})


class AdmBwfWriter:
    """Writes multichannel audio as a Dolby Atmos Master ADM Profile v1.1 BWF file.

    Supports bed configurations: 5.1, 7.1, 5.1.2, 7.1.2, 5.1.4, 7.1.4.
    Use --output-type wav for any other format.
    """

    def __init__(self, file_path: str, sample_rate: int, config: UpmixConfig):
        self._path = file_path
        self._sr = sample_rate
        self._config = config
        self._format = FORMAT_MAP[config.output_format]

    def write(
        self,
        channels: dict[str, np.ndarray],
        measured_lkfs: float | None = None,
        measured_tp_dbtp: float | None = None,
    ) -> None:
        """Write multichannel audio to ADM BWF file.

        Args:
            channels:        Dict channel_name → 1D float64 array.
            measured_lkfs:   BS.1770-4 integrated loudness (LKFS), written to
                             bext LOUDNESS_VALUE field.  None = "not indicated".
            measured_tp_dbtp: Maximum True Peak (dBTP), written to bext
                             MAX_TRUE_PEAK_LEVEL field.  None = "not indicated".
        """
        fmt = self._format
        if fmt.name not in _DOLBY_ENGINE_ALLOWED_FORMATS:
            raise ValueError(
                f"Output format '{fmt.name}' is not a supported ADM BWF bed configuration. "
                f"Supported: {sorted(_DOLBY_ENGINE_ALLOWED_FORMATS)}. "
                f"Use --output-type wav for other formats."
            )

        sr = self._sr
        if sr not in (48_000, 96_000):
            raise ValueError("Dolby ADM-BWF requires 48 kHz or 96 kHz")
        if self._config.output_subtype != "PCM_24":
            raise ValueError("Dolby ADM-BWF requires PCM_24")
        bit_depth = {"PCM_16": 16, "PCM_24": 24, "PCM_32": 32}.get(
            self._config.output_subtype, 24
        )

        ordered = []
        for label in fmt.channels:
            key = label.value
            if key not in channels:
                raise ValueError(f"Missing channel '{key}' for {fmt.name} output")
            ordered.append(channels[key])

        n_samples = len(ordered[0])
        if any(len(channel) != n_samples for channel in ordered):
            raise ValueError("All ADM-BWF channels must have identical sample counts")
        ordered = dither_channels(
            ordered,
            self._config.output_subtype,
            self._config.output_dither,
            self._config.output_dither_seed,
        )
        duration_s = n_samples / sr

        fmt_bytes  = _fmt_chunk(fmt, sr, bit_depth)
        bext_bytes = _bext_chunk(loudness_lkfs=measured_lkfs, tp_dbtp=measured_tp_dbtp)
        chna_bytes = _chna_chunk(fmt)
        axml_bytes = _axml_chunk(
            fmt, duration_s, sr, bit_depth, strict_profile=False
        )
        dbmd_bytes = _dbmd_chunk()
        data_size = n_samples * fmt.n_channels * (bit_depth // 8)
        riff_size = 4 + sum((
            _chunk_size(len(fmt_bytes)), _chunk_size(len(bext_bytes)),
            _chunk_size(data_size), _chunk_size(len(axml_bytes)),
            _chunk_size(len(chna_bytes)), _chunk_size(len(dbmd_bytes)),
        ))
        if riff_size > 0xFFFFFFFF:
            raise ValueError("ADM-BWF exceeds RIFF 4 GiB limit; BW64 output is required")

        with atomic_output_path(self._path) as temporary:
            with temporary.open("wb") as handle:
                handle.write(b"RIFF" + struct.pack("<I", riff_size) + b"WAVE")
                _write_chunk(handle, b"fmt ", fmt_bytes)
                _write_chunk(handle, b"bext", bext_bytes)
                handle.write(b"data" + struct.pack("<I", data_size))
                block_size = 262_144
                for start in range(0, n_samples, block_size):
                    block = np.column_stack([channel[start:start + block_size] for channel in ordered])
                    handle.write(_audio_to_pcm(block, bit_depth))
                if data_size & 1:
                    handle.write(b"\x00")
                _write_chunk(handle, b"axml", axml_bytes)
                _write_chunk(handle, b"chna", chna_bytes)
                _write_chunk(handle, b"dbmd", dbmd_bytes)
            info = sf.info(str(temporary))
            if info.samplerate != sr or info.channels != fmt.n_channels or info.frames != n_samples:
                raise RuntimeError("Written ADM-BWF metadata does not match requested output")
