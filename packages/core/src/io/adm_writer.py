"""ADM BWF (Audio Definition Model / Broadcast Wave Format) writer.

Targets Logic Pro / DaVinci Resolve / Pro Tools ADM BWF import. See
``adm_chunks.py`` for the low-level RIFF/BWF/ADM-XML chunk builders and the
key ADM/Dolby profile design choices they implement.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np
import soundfile as sf
import upmixer_dsp

from upmixer.config import UpmixConfig
from upmixer.formats import (
    DOLBY_ADM_BED_FORMATS,
    FORMAT_MAP,
    ChannelLabel,
    OutputFormat,
)
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

_DOLBY_ENGINE_ALLOWED_FORMATS = frozenset(DOLBY_ADM_BED_FORMATS)
_DOLBY_BASIC_ZONES = frozenset({
    "ZM1", "ZM2L", "ZM2R", "ZM3L", "ZM3Lss", "ZM3R", "ZM3Rss", "ZM4", "ZM5",
})
_DOLBY_HEIGHT_ZONES = frozenset({"ZB", "ZT"})
_UINT32_MAX = 0xFFFFFFFF
_DS64_DATA_SIZE = 28


def _valid_zone_exclusion(zones: tuple[str, ...]) -> bool:
    return (
        len(zones) == len(set(zones))
        and sum(zone in _DOLBY_BASIC_ZONES for zone in zones) <= 1
        and sum(zone in _DOLBY_HEIGHT_ZONES for zone in zones) <= 1
        and all(zone in _DOLBY_BASIC_ZONES | _DOLBY_HEIGHT_ZONES for zone in zones)
    )


@dataclass(frozen=True)
class AdmObject:
    """One mono object track and its static Dolby-profile ADM parameters."""

    name: str
    audio: np.ndarray
    position: tuple[float, float, float]
    object_size: float = 0.0
    diffuse: bool = False
    gain: float = 1.0
    importance: int = 10
    channel_lock: bool = False
    zone_exclusion: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.diffuse, bool):
            raise ValueError("Dolby ADM object diffuse must be a boolean")
        if self.diffuse:
            raise ValueError("Dolby ADM diffuse objects are not supported by the audio engine")


def _wave_header(
    riff_size: int, data_size: int, sample_count: int,
) -> tuple[bytes, int]:
    if riff_size <= _UINT32_MAX:
        return b"RIFF" + struct.pack("<I", riff_size) + b"WAVE", data_size

    riff_size += _chunk_size(_DS64_DATA_SIZE)
    ds64 = struct.pack("<QQQI", riff_size, data_size, sample_count, 0)
    header = b"BW64" + struct.pack("<I", _UINT32_MAX) + b"WAVE"
    return header + b"ds64" + struct.pack("<I", len(ds64)) + ds64, _UINT32_MAX


def _validated_audio(name: str, audio: np.ndarray) -> np.ndarray:
    array = np.asarray(audio)
    if array.ndim != 1:
        raise ValueError(f"ADM-BWF channel '{name}' must be a 1D array")
    if (
        not np.issubdtype(array.dtype, np.number)
        or np.issubdtype(array.dtype, np.complexfloating)
        or not np.all(np.isfinite(array))
    ):
        raise ValueError(f"ADM-BWF channel '{name}' must contain finite real samples")
    return array


def render_adm_programme(
    channels: dict[str, np.ndarray],
    fmt: OutputFormat,
    objects: list[AdmObject],
) -> dict[str, np.ndarray]:
    """Render one ADM bed and its static objects to the bed layout."""
    rendered = {label.value: channels[label.value].copy() for label in fmt.channels}
    speakers = [label.value for label in fmt.channels if label != ChannelLabel.LFE]
    for obj in objects:
        x, y, z = obj.position
        radius = math.sqrt(x * x + y * y + z * z)
        elevation = math.degrees(math.asin(z / radius)) if radius else 0.0
        azimuth = math.degrees(math.atan2(-x, y)) if radius else 0.0
        gains, _ = upmixer_dsp.adm_object_routes(
            azimuth, elevation, 0.0, obj.object_size, obj.channel_lock,
            list(obj.zone_exclusion), speakers,
        )
        for speaker, gain in zip(speakers, gains):
            rendered[speaker] += obj.gain * gain * obj.audio
    return rendered


class AdmBwfWriter:
    """Writes multichannel audio as a Dolby Atmos Master ADM Profile v1.1 BWF file.

    Supports bed configurations: 5.1, 7.1, and 7.1.2.
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
        objects: list[AdmObject] | None = None,
    ) -> None:
        """Write multichannel audio to ADM BWF file.

        Args:
            channels:        Dict channel_name → 1D float64 array.
            measured_lkfs:   BS.1770-4 integrated loudness (LKFS), written to
                             bext LOUDNESS_VALUE field.  None = "not indicated".
            measured_tp_dbtp: Maximum True Peak (dBTP), written to bext
                             MAX_TRUE_PEAK_LEVEL field.  None = "not indicated".
            objects: Mono object tracks written after the one DirectSpeakers bed.
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

        objects = objects or []
        if len(objects) > 118 or fmt.n_channels + len(objects) > 128:
            raise ValueError("Dolby ADM-BWF allows at most 128 tracks and 118 objects")

        ordered = []
        for label in fmt.channels:
            key = label.value
            if key not in channels:
                raise ValueError(f"Missing channel '{key}' for {fmt.name} output")
            ordered.append(_validated_audio(key, channels[key]))
        for obj in objects:
            if not obj.name or len(obj.name) > 64:
                raise ValueError("ADM object names must be 1 to 64 characters")
            if any(not -1.0 <= value <= 1.0 for value in obj.position):
                raise ValueError(f"ADM object '{obj.name}' has an invalid position")
            if not 0.0 <= obj.object_size <= 1.0:
                raise ValueError(f"ADM object '{obj.name}' has an invalid object size")
            if (
                isinstance(obj.gain, bool)
                or not isinstance(obj.gain, (int, float))
                or not math.isfinite(obj.gain)
                or obj.gain < 0.0
            ):
                raise ValueError(f"ADM object '{obj.name}' gain must be finite and non-negative")
            if (
                isinstance(obj.importance, bool)
                or not isinstance(obj.importance, int)
                or not 0 <= obj.importance <= 10
            ):
                raise ValueError(f"ADM object '{obj.name}' importance must be in 0..10")
            if obj.importance == 0 and obj.gain != 0.0:
                raise ValueError(f"ADM object '{obj.name}' gain must be 0 when importance is 0")
            if not isinstance(obj.channel_lock, bool):
                raise ValueError(f"ADM object '{obj.name}' channel lock must be a boolean")
            if not _valid_zone_exclusion(obj.zone_exclusion):
                raise ValueError(f"ADM object '{obj.name}' has an invalid zone exclusion")
            ordered.append(_validated_audio(obj.name, obj.audio))

        n_samples = len(ordered[0])
        if any(len(channel) != n_samples for channel in ordered):
            raise ValueError("All ADM-BWF channels must have identical sample counts")
        ordered = dither_channels(
            ordered,
            self._config.output_subtype,
            self._config.output_dither,
            self._config.output_dither_seed,
        )
        metadata_objects = tuple(
            (
                obj.name, obj.position, obj.object_size, obj.diffuse, obj.gain,
                obj.importance, obj.channel_lock, obj.zone_exclusion,
            )
            for obj in objects
        )
        fmt_bytes  = _fmt_chunk(fmt, sr, bit_depth, len(ordered))
        bext_bytes = _bext_chunk(loudness_lkfs=measured_lkfs, tp_dbtp=measured_tp_dbtp)
        chna_bytes = _chna_chunk(fmt, len(objects))
        axml_bytes = _axml_chunk(
            fmt, n_samples, sr, bit_depth, objects=metadata_objects,
        )
        dbmd_bytes = _dbmd_chunk()
        data_size = n_samples * len(ordered) * (bit_depth // 8)
        riff_size = 4 + sum((
            _chunk_size(len(fmt_bytes)), _chunk_size(len(bext_bytes)),
            _chunk_size(data_size), _chunk_size(len(axml_bytes)),
            _chunk_size(len(chna_bytes)), _chunk_size(len(dbmd_bytes)),
        ))
        header, data_size_field = _wave_header(riff_size, data_size, n_samples)

        with atomic_output_path(self._path) as temporary:
            with temporary.open("wb") as handle:
                handle.write(header)
                _write_chunk(handle, b"fmt ", fmt_bytes)
                _write_chunk(handle, b"bext", bext_bytes)
                handle.write(b"data" + struct.pack("<I", data_size_field))
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
            if info.samplerate != sr or info.channels != len(ordered) or info.frames != n_samples:
                raise RuntimeError("Written ADM-BWF metadata does not match requested output")
