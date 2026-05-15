"""ADM BWF (Audio Definition Model / Broadcast Wave Format) writer.

Targets Logic Pro / DaVinci Resolve / Pro Tools ADM BWF import.

Key design choices:
  - WAVE_FORMAT_PCM (0x0001), 16-byte fmt, no channel mask — matches
    Logic Pro's own ADM BWF export format exactly.
  - Bare <audioFormatExtended> root in axml chunk (ITU-R BS.2076-2 §6).
    EBU ebuCoreMain wrapper causes Logic Pro's XPath parser to miss the
    element at the expected location.
  - Custom IDs from 0x1001 (Dolby Atmos Master ADM Profile v1.1 §3.1)
  - Dolby RC_ speaker labels + RoomCentric channel names (§2.4, §2.5)
  - Cartesian positions in audioBlockFormat (§2.5)
  - sampleRate + bitDepth on audioTrackUID (§2.10)
  - <dialogue mixedContentKind="0">2</dialogue> in audioContent (§2.8)
  - audioPackFormatIDRef inside audioStreamFormat (§2.3)
"""

from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

import numpy as np

from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, FORMAT_MAP, OutputFormat

# Supported bed configurations for ADM BWF export.
# Dolby Atmos Master ADM Profile v1.1 §2.6 defines 5.1/7.1/7.1.2 as the
# canonical bed formats; 5.1.2, 5.1.4, and 7.1.4 extend the same spec and
# are accepted by Logic Pro, DaVinci Resolve, and Pro Tools.
_DOLBY_ALLOWED_FORMATS = frozenset({"5.1", "7.1", "5.1.2", "7.1.2", "5.1.4", "7.1.4"})

# Dolby channel names (audioChannelFormatName) per spec §2.4 Table 2-11
_DOLBY_CH_NAME: dict[ChannelLabel, str] = {
    ChannelLabel.FL:  "RoomCentricLeft",
    ChannelLabel.FR:  "RoomCentricRight",
    ChannelLabel.C:   "RoomCentricCenter",
    ChannelLabel.LFE: "RoomCentricLFE",
    ChannelLabel.SL:  "RoomCentricLeftSideSurround",
    ChannelLabel.SR:  "RoomCentricRightSideSurround",
    ChannelLabel.BL:  "RoomCentricLeftRearSurround",
    ChannelLabel.BR:  "RoomCentricRightRearSurround",
    ChannelLabel.TFL: "RoomCentricLeftTopSurround",
    ChannelLabel.TFR: "RoomCentricRightTopSurround",
    ChannelLabel.TBL: "RoomCentricLeftTopRearSurround",
    ChannelLabel.TBR: "RoomCentricRightTopRearSurround",
}

# Dolby speaker labels (RC_ prefix) per spec §2.5
_DOLBY_SPEAKER_LABEL: dict[ChannelLabel, str] = {
    ChannelLabel.FL:  "RC_L",
    ChannelLabel.FR:  "RC_R",
    ChannelLabel.C:   "RC_C",
    ChannelLabel.LFE: "RC_LFE",
    ChannelLabel.SL:  "RC_Lss",
    ChannelLabel.SR:  "RC_Rss",
    ChannelLabel.BL:  "RC_Lrs",
    ChannelLabel.BR:  "RC_Rrs",
    ChannelLabel.TFL: "RC_Lts",
    ChannelLabel.TFR: "RC_Rts",
    ChannelLabel.TBL: "RC_Ltrs",
    ChannelLabel.TBR: "RC_Rtrs",
}

# Cartesian (X, Y, Z) positions per spec §2.5 Table 2-13
_DOLBY_POSITION: dict[ChannelLabel, tuple[float, float, float]] = {
    ChannelLabel.FL:  (-1.0,  1.0,  0.0),
    ChannelLabel.FR:  ( 1.0,  1.0,  0.0),
    ChannelLabel.C:   ( 0.0,  1.0,  0.0),
    ChannelLabel.LFE: (-1.0,  1.0, -1.0),
    ChannelLabel.SL:  (-1.0,  0.0,  0.0),
    ChannelLabel.SR:  ( 1.0,  0.0,  0.0),
    ChannelLabel.BL:  (-1.0, -1.0,  0.0),
    ChannelLabel.BR:  ( 1.0, -1.0,  0.0),
    ChannelLabel.TFL: (-1.0,  0.0,  1.0),
    ChannelLabel.TFR: ( 1.0,  0.0,  1.0),
    ChannelLabel.TBL: (-1.0, -1.0,  1.0),
    ChannelLabel.TBR: ( 1.0, -1.0,  1.0),
}


# ── Low-level helpers ─────────────────────────────────────────────────────────


def _make_chunk(tag: bytes, data: bytes) -> bytes:
    """Wrap data in a RIFF chunk; word-align with a silent padding byte if needed."""
    chunk = tag + struct.pack("<I", len(data)) + data
    if len(data) % 2:
        chunk += b"\x00"
    return chunk


def _pad_field(s: str, n: int) -> bytes:
    """ASCII-encode s and null-pad to exactly n bytes."""
    b = s.encode("ascii")
    if len(b) > n:
        raise ValueError(f"String '{s}' ({len(b)} bytes) exceeds field width {n}")
    return b + b"\x00" * (n - len(b))


def _fmt_time(seconds: float) -> str:
    """Format as HH:MM:SS.SSSSS (ADM timestamp per BS.2076-2)."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:08.5f}"


def _pos_str(v: float) -> str:
    """Render a position coordinate as integer string where possible."""
    return str(int(v)) if v == int(v) else f"{v:.6g}"


# ── Chunk builders ────────────────────────────────────────────────────────────


def _fmt_chunk(fmt: OutputFormat, sample_rate: int, bit_depth: int) -> bytes:
    """Build a 16-byte WAVE_FORMAT_PCM fmt chunk.

    Logic Pro's own ADM BWF export uses WAVE_FORMAT_PCM (0x0001) with no
    channel mask — 16 bytes, no cbSize extension.
    """
    n_ch = fmt.n_channels
    block_align = n_ch * (bit_depth // 8)
    return struct.pack(
        "<HHIIHH",
        0x0001,                    # wFormatTag = WAVE_FORMAT_PCM
        n_ch,
        sample_rate,
        sample_rate * block_align,
        block_align,
        bit_depth,
    )  # 16 bytes


def _bext_chunk() -> bytes:
    """Build a minimal BWF v2 bext chunk (EBU Tech 3285 r3)."""
    now = datetime.utcnow()
    buf = bytearray(602)

    desc = b"Generated by upmixer"
    buf[:len(desc)] = desc

    orig = b"upmixer"
    buf[256:256 + len(orig)] = orig

    buf[320:330] = now.strftime("%Y-%m-%d").encode("ascii")
    buf[330:338] = now.strftime("%H:%M:%S").encode("ascii")

    struct.pack_into("<H", buf, 346, 2)  # BWF version = 2

    for offset in (412, 414, 416, 418, 420):
        struct.pack_into("<H", buf, offset, 0x7FFF)  # loudness = not indicated

    return bytes(buf) + b"\r\n"


def _chna_chunk(fmt: OutputFormat) -> bytes:
    """Build CHNA chunk using custom Dolby-profile IDs starting from 0x1001 (§3.2)."""
    n = fmt.n_channels
    pack_id = "AP_00011001"
    data = struct.pack("<HH", n, n)  # numTracks, numUIDs

    for i, label in enumerate(fmt.channels):
        track_fmt_id = f"AT_0001{0x1001 + i:04X}_01"
        uid_str = f"ATU_{i + 1:08d}"
        data += struct.pack("<H", i + 1)    # 1-based track index
        data += _pad_field(uid_str, 12)
        data += _pad_field(track_fmt_id, 14)
        data += _pad_field(pack_id, 11)
        data += b"\x00"                     # padding byte

    return data


def _axml_chunk(
    fmt: OutputFormat, duration_s: float, sample_rate: int, bit_depth: int
) -> bytes:
    """Generate Dolby Atmos Master ADM Profile v1.1 compliant XML."""
    dur = _fmt_time(duration_s)
    zero = "00:00:00.00000"
    n = len(fmt.channels)
    pack_id = "AP_00011001"

    def ch_id(i: int) -> str:
        return f"AC_0001{0x1001 + i:04X}"

    def stream_id(i: int) -> str:
        return f"AS_0001{0x1001 + i:04X}"

    def track_id(i: int) -> str:
        return f"AT_0001{0x1001 + i:04X}_01"

    def blk_id(i: int) -> str:
        return f"AB_0001{0x1001 + i:04X}_00000001"

    lines: list[str] = []
    a = lines.append

    # ITU-R BS.2076-2 §6: axml chunk shall contain a bare audioFormatExtended
    # element at the root — NOT wrapped in ebuCoreMain. Logic Pro parses
    # audioFormatExtended at the document root; the EBU wrapper breaks routing.
    a('<?xml version="1.0" encoding="UTF-8"?>')
    a('<audioFormatExtended version="ITU-R_BS.2076-2">')

    # audioProgramme — include start/end for Logic Pro timeline binding
    a('        <audioProgramme audioProgrammeID="APR_1001"')
    a('                        audioProgrammeName="Main Programme"')
    a(f'                        start="{zero}" end="{dur}">')
    a('          <audioContentIDRef>ACO_1001</audioContentIDRef>')
    a('        </audioProgramme>')

    # audioContent — dialogue element required, no typeLabel/typeDefinition (§2.8)
    a('        <audioContent audioContentID="ACO_1001"')
    a(f'                      audioContentName="{fmt.name} Bed">')
    a('          <audioObjectIDRef>AO_1001</audioObjectIDRef>')
    a('          <dialogue mixedContentKind="0">2</dialogue>')
    a('        </audioContent>')

    # audioObject — no interact attribute (§2.7)
    a('        <audioObject audioObjectID="AO_1001"')
    a(f'                     audioObjectName="{fmt.name} Bed"')
    a(f'                     start="{zero}" duration="{dur}">')
    a(f'          <audioPackFormatIDRef>{pack_id}</audioPackFormatIDRef>')
    for i in range(n):
        a(f'          <audioTrackUIDRef>ATU_{i + 1:08d}</audioTrackUIDRef>')
    a('        </audioObject>')

    # audioPackFormat — custom ID (§3.1)
    a(f'        <audioPackFormat audioPackFormatID="{pack_id}"')
    a(f'                         audioPackFormatName="{fmt.name} Bed"')
    a('                         typeLabel="0001" typeDefinition="DirectSpeakers">')
    for i in range(n):
        a(f'          <audioChannelFormatIDRef>{ch_id(i)}</audioChannelFormatIDRef>')
    a('        </audioPackFormat>')

    # audioChannelFormats — Dolby names, cartesian positions, RC_ labels (§2.4, §2.5)
    # DirectSpeakers audioBlockFormat must NOT have rtime/duration
    for i, label in enumerate(fmt.channels):
        cid = ch_id(i)
        bid = blk_id(i)
        name = _DOLBY_CH_NAME[label]
        speaker = _DOLBY_SPEAKER_LABEL[label]
        x, y, z = _DOLBY_POSITION[label]

        a(f'        <audioChannelFormat audioChannelFormatID="{cid}"')
        a(f'                            audioChannelFormatName="{name}"')
        a('                            typeLabel="0001" typeDefinition="DirectSpeakers">')
        a(f'          <audioBlockFormat audioBlockFormatID="{bid}">')
        a('            <cartesian>1</cartesian>')
        a(f'            <position coordinate="X">{_pos_str(x)}</position>')
        a(f'            <position coordinate="Y">{_pos_str(y)}</position>')
        a(f'            <position coordinate="Z">{_pos_str(z)}</position>')
        a(f'            <speakerLabel>{speaker}</speakerLabel>')
        a('          </audioBlockFormat>')
        a('        </audioChannelFormat>')

    # audioStreamFormats — includes audioPackFormatIDRef (§2.3)
    for i, label in enumerate(fmt.channels):
        sid = stream_id(i)
        cid = ch_id(i)
        tid = track_id(i)
        a(f'        <audioStreamFormat audioStreamFormatID="{sid}"')
        a(f'                           audioStreamFormatName="PCM_{label.value}"')
        a('                           formatLabel="0001" formatDefinition="PCM">')
        a(f'          <audioChannelFormatIDRef>{cid}</audioChannelFormatIDRef>')
        a(f'          <audioPackFormatIDRef>{pack_id}</audioPackFormatIDRef>')
        a(f'          <audioTrackFormatIDRef>{tid}</audioTrackFormatIDRef>')
        a('        </audioStreamFormat>')

    # audioTrackFormats
    for i, label in enumerate(fmt.channels):
        tid = track_id(i)
        sid = stream_id(i)
        a(f'        <audioTrackFormat audioTrackFormatID="{tid}"')
        a(f'                          audioTrackFormatName="PCM_{label.value}"')
        a('                          formatLabel="0001" formatDefinition="PCM">')
        a(f'          <audioStreamFormatIDRef>{sid}</audioStreamFormatIDRef>')
        a('        </audioTrackFormat>')

    # audioTrackUIDs — sampleRate and bitDepth required (§2.10)
    for i, label in enumerate(fmt.channels):
        uid = f"ATU_{i + 1:08d}"
        tid = track_id(i)
        a(f'        <audioTrackUID UID="{uid}"')
        a(f'                       sampleRate="{sample_rate}"')
        a(f'                       bitDepth="{bit_depth}">')
        a(f'          <audioTrackFormatIDRef>{tid}</audioTrackFormatIDRef>')
        a(f'          <audioPackFormatIDRef>{pack_id}</audioPackFormatIDRef>')
        a('        </audioTrackUID>')

    a('</audioFormatExtended>')

    return "\n".join(lines).encode("utf-8")




def _audio_to_pcm(audio: np.ndarray, bit_depth: int) -> bytes:
    """Convert float64 [-1, 1] to interleaved little-endian PCM bytes."""
    scale = 2 ** (bit_depth - 1) - 1
    clipped = np.clip(audio, -1.0, 1.0)
    if bit_depth == 16:
        return np.round(clipped * scale).astype("<i2").tobytes()
    if bit_depth == 24:
        flat = np.ascontiguousarray(np.round(clipped * scale).astype("<i4"))
        return flat.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    if bit_depth == 32:
        return np.round(clipped * scale).astype("<i4").tobytes()
    raise ValueError(f"Unsupported bit depth for ADM BWF: {bit_depth}")


# ── Public writer class ───────────────────────────────────────────────────────


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

    def write(self, channels: dict[str, np.ndarray]) -> None:
        fmt = self._format
        if fmt.name not in _DOLBY_ALLOWED_FORMATS:
            raise ValueError(
                f"Output format '{fmt.name}' is not a supported ADM BWF bed configuration. "
                f"Supported: {sorted(_DOLBY_ALLOWED_FORMATS)}. "
                f"Use --output-type wav for other formats."
            )

        sr = self._sr
        bit_depth = {"PCM_16": 16, "PCM_24": 24, "PCM_32": 32}.get(
            self._config.output_subtype, 24
        )

        ordered = []
        for label in fmt.channels:
            key = label.value
            if key not in channels:
                raise ValueError(f"Missing channel '{key}' for {fmt.name} output")
            ordered.append(channels[key])

        audio = np.column_stack(ordered)   # (n_samples, n_channels), C-order
        duration_s = audio.shape[0] / sr

        fmt_bytes  = _fmt_chunk(fmt, sr, bit_depth)
        chna_bytes = _chna_chunk(fmt)
        pcm_bytes  = _audio_to_pcm(audio, bit_depth)
        axml_bytes = _axml_chunk(fmt, duration_s, sr, bit_depth)

        # Chunk order matches Logic Pro export: fmt data axml chna
        # bext omitted — Logic Pro doesn't write it in ADM BWF exports.
        wave_body = (
            _make_chunk(b"fmt ", fmt_bytes)
            + _make_chunk(b"data", pcm_bytes)
            + _make_chunk(b"axml", axml_bytes)
            + _make_chunk(b"chna", chna_bytes)
        )
        riff = b"RIFF" + struct.pack("<I", 4 + len(wave_body)) + b"WAVE" + wave_body

        Path(self._path).write_bytes(riff)
