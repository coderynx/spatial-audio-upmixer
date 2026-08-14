"""ADM BWF (Audio Definition Model / Broadcast Wave Format) writer.

Targets Logic Pro / DaVinci Resolve / Pro Tools ADM BWF import. See
``adm_chunks.py`` for the low-level RIFF/BWF/ADM-XML chunk builders and the
key ADM/Dolby profile design choices they implement.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, FORMAT_MAP, OutputFormat
from upmixer.io.adm_chunks import (
    _LEFT_CH_LABELS,
    _RIGHT_CH_LABELS,
    _audio_to_pcm,
    _axml_chunk,
    _axml_stem_beds_chunk,
    _bext_chunk,
    _chna_chunk,
    _chna_stem_beds_chunk,
    _chunk_size,
    _dbmd_chunk,
    _fmt_chunk,
    _fmt_chunk_n,
    _make_chunk,
    _write_chunk,
)
from upmixer.io.atomic import atomic_output_path

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


class AdmBwfStemWriter:
    """Writes separated stems as per-stem DirectSpeakers beds in a BWF file.

    Each stem becomes one audioObject backed by a DirectSpeakers audioPackFormat.
    The routing gain is pre-applied to each channel's PCM data so the DAW
    renders the correct default mix immediately. Unused channels are omitted
    from the bed so the DAW is not cluttered with silent tracks.

    Passthrough channels (C, LFE from multichannel input) are written as a
    separate bed containing only those channels.

    Args:
        output_fmt: the target output format — used to filter routing channels
            so only speakers that exist in the layout produce tracks.
    """

    def __init__(
        self,
        file_path: str,
        sample_rate: int,
        config: UpmixConfig,
        output_fmt: OutputFormat,
    ) -> None:
        self._path = file_path
        self._sr = sample_rate
        self._config = config
        self._output_fmt = output_fmt
        self._output_ch_values = {label.value for label in output_fmt.channels}
        self._lfe_sos = butter(
            config.lfe_filter_order,
            config.lfe_cutoff_hz / (sample_rate / 2.0),
            btype="low",
            output="sos",
        )

    def write(
        self,
        stems: dict[str, np.ndarray],
        passthrough: dict[str, np.ndarray],
        routing_map: dict[str, dict[str, float]],
    ) -> None:
        """Write stems as DirectSpeakers beds and passthrough as a separate bed.

        Args:
            stems: "StemName[@zone]" → (n_samples, 2) float64 stereo.
            passthrough: channel_name → (n_samples,) float64 mono (C, LFE).
            routing_map: "StemName[@zone]" → {channel: gain} routing dict.
        """
        if self._output_fmt.name not in _DOLBY_ENGINE_ALLOWED_FORMATS:
            raise ValueError(
                f"Output format '{self._output_fmt.name}' is not supported by Dolby ADM Profile v1.1"
            )
        if self._sr not in (48_000, 96_000) or self._config.output_subtype != "PCM_24":
            raise ValueError("Dolby ADM-BWF requires 48/96 kHz PCM_24")
        bit_depth = {"PCM_16": 16, "PCM_24": 24, "PCM_32": 32}.get(
            self._config.output_subtype, 24
        )

        stem_beds: list[tuple[str, int, list[tuple[ChannelLabel, int]]]] = []
        all_tracks: list[np.ndarray] = []

        for stem_idx, stem_key in enumerate(sorted(stems.keys())):
            audio = stems[stem_key]
            routing = routing_map.get(stem_key) or {}
            stem_name = stem_key.replace("@", " (") + (")" if "@" in stem_key else "")

            n = audio.shape[0]
            L = audio[:, 0].astype(np.float64)
            R = audio[:, 1].astype(np.float64) if audio.shape[1] > 1 else L.copy()
            mono = (L + R) * 0.5

            bed_channels: list[tuple[ChannelLabel, int]] = []

            for ch_str, gain in routing.items():
                if ch_str == "LFE":
                    continue
                if ch_str not in self._output_ch_values:
                    continue
                try:
                    label = ChannelLabel(ch_str)
                except ValueError:
                    continue

                if label in _LEFT_CH_LABELS:
                    ch_audio = L * gain
                elif label in _RIGHT_CH_LABELS:
                    ch_audio = R * gain
                elif label == ChannelLabel.C:
                    ch_audio = mono * gain
                else:
                    continue

                track_idx = len(all_tracks)
                all_tracks.append(ch_audio)
                bed_channels.append((label, track_idx))

            lfe_routing_gain = routing.get("LFE", 0.0)
            if lfe_routing_gain > 0 and "LFE" in self._output_ch_values:
                lfe_audio = (
                    self._config.lfe_gain
                    * lfe_routing_gain
                    * sosfilt(self._lfe_sos, mono)
                )
                track_idx = len(all_tracks)
                all_tracks.append(lfe_audio)
                bed_channels.append((ChannelLabel.LFE, track_idx))

            if bed_channels:
                stem_beds.append((stem_name, stem_idx, bed_channels))

        if passthrough:
            pass_channels: list[tuple[ChannelLabel, int]] = []
            for ch_name in sorted(passthrough.keys()):
                if ch_name not in self._output_ch_values:
                    continue
                try:
                    label = ChannelLabel(ch_name)
                except ValueError:
                    continue
                track_idx = len(all_tracks)
                all_tracks.append(passthrough[ch_name].astype(np.float64))
                pass_channels.append((label, track_idx))
            if pass_channels:
                passthrough_stem_idx = len(stem_beds)
                stem_beds.append(("Passthrough", passthrough_stem_idx, pass_channels))

        if not all_tracks:
            raise ValueError("No audio tracks to write (check routing and output format).")
        if len(stem_beds) > 64 or len(all_tracks) > 128:
            raise ValueError("Dolby ADM Profile limits output to 64 beds and 128 tracks")

        n_tracks = len(all_tracks)
        n_samples = max(len(t) for t in all_tracks)

        padded = []
        for t in all_tracks:
            if len(t) < n_samples:
                t = np.concatenate([t, np.zeros(n_samples - len(t))])
            padded.append(np.clip(t, -1.0, 1.0))

        interleaved = np.column_stack(padded)
        duration_s = n_samples / self._sr

        fmt_bytes  = _fmt_chunk_n(n_tracks, self._sr, bit_depth)
        chna_bytes = _chna_stem_beds_chunk(stem_beds)
        pcm_bytes  = _audio_to_pcm(interleaved, bit_depth)
        axml_bytes = _axml_stem_beds_chunk(stem_beds, duration_s, self._sr, bit_depth, self._output_fmt.bs2051_system)

        wave_body = (
            _make_chunk(b"fmt ", fmt_bytes)
            + _make_chunk(b"data", pcm_bytes)
            + _make_chunk(b"axml", axml_bytes)
            + _make_chunk(b"chna", chna_bytes)
            + _make_chunk(b"dbmd", _dbmd_chunk())
        )
        riff = b"RIFF" + struct.pack("<I", 4 + len(wave_body)) + b"WAVE" + wave_body
        Path(self._path).write_bytes(riff)
