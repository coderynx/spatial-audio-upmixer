"""Low-level RIFF/BWF/ADM-XML chunk builders backing ``adm_writer.py``.

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
from datetime import datetime, timezone

import numpy as np

from upmixer.formats import ChannelLabel, OutputFormat

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


def _chunk_size(data_size: int) -> int:
    return 8 + data_size + (data_size & 1)


def _write_chunk(handle, tag: bytes, data: bytes) -> None:
    handle.write(tag)
    handle.write(struct.pack("<I", len(data)))
    handle.write(data)
    if len(data) & 1:
        handle.write(b"\x00")


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


def _fmt_chunk(fmt: OutputFormat, sample_rate: int, bit_depth: int) -> bytes:
    """Build a 16-byte WAVE_FORMAT_PCM fmt chunk.

    Logic Pro's own ADM BWF export uses WAVE_FORMAT_PCM (0x0001) with no
    channel mask — 16 bytes, no cbSize extension.
    """
    n_ch = fmt.n_channels
    block_align = n_ch * (bit_depth // 8)
    return struct.pack(
        "<HHIIHH",
        0x0001,
        n_ch,
        sample_rate,
        sample_rate * block_align,
        block_align,
        bit_depth,
    )


def _loudness_i16(value: float | None) -> int:
    """Encode a loudness / True Peak value to BWF bext int16 representation.

    Unit: 0.01 dB / LKFS (EBU Tech 3285 r3, §2.8 LOUDNESS_VALUE etc.).
    0x7FFF (32767) = sentinel "not indicated".
    Range of encodable values: −327.68 … +327.66 (well beyond any real level).
    """
    if value is None:
        return 0x7FFF
    return int(round(max(-327.68, min(327.66, value)) * 100))


def _bext_chunk(
    loudness_lkfs: float | None = None,
    tp_dbtp: float | None = None,
) -> bytes:
    """Build a BWF v2 bext chunk (EBU Tech 3285 r3) with optional loudness metadata.

    Args:
        loudness_lkfs: Integrated programme loudness (BS.1770-4) in LKFS.
                       Written to LOUDNESS_VALUE field (offset 412).
        tp_dbtp:       Maximum True Peak level in dBTP.
                       Written to MAX_TRUE_PEAK_LEVEL field (offset 416).
        All other loudness fields (LRA, momentary, short-term) are set to
        the "not indicated" sentinel (0x7FFF) as they are not measured.
    """
    now = datetime.now(timezone.utc)
    buf = bytearray(602)

    desc = b"Generated by upmixer"
    buf[:len(desc)] = desc

    orig = b"upmixer"
    buf[256:256 + len(orig)] = orig

    buf[320:330] = now.strftime("%Y-%m-%d").encode("ascii")
    buf[330:338] = now.strftime("%H:%M:%S").encode("ascii")

    struct.pack_into("<H", buf, 346, 2)

    struct.pack_into("<h", buf, 412, _loudness_i16(loudness_lkfs))
    struct.pack_into("<H", buf, 414, 0x7FFF)
    struct.pack_into("<h", buf, 416, _loudness_i16(tp_dbtp))
    struct.pack_into("<H", buf, 418, 0x7FFF)
    struct.pack_into("<H", buf, 420, 0x7FFF)

    return bytes(buf) + b"\r\n"


def _chna_chunk(fmt: OutputFormat) -> bytes:
    """Build CHNA chunk using custom Dolby-profile IDs starting from 0x1001 (§3.2)."""
    n = fmt.n_channels
    pack_id = "AP_00011001"
    data = struct.pack("<HH", n, n)

    for i, label in enumerate(fmt.channels):
        track_fmt_id = f"AT_0001{0x1001 + i:04X}_01"
        uid_str = f"ATU_{i + 1:08d}"
        data += struct.pack("<H", i + 1)
        data += _pad_field(uid_str, 12)
        data += _pad_field(track_fmt_id, 14)
        data += _pad_field(pack_id, 11)
        data += b"\x00"

    return data


def _axml_chunk(
    fmt: OutputFormat,
    duration_s: float,
    sample_rate: int,
    bit_depth: int,
    strict_profile: bool,
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

    a('<?xml version="1.0" encoding="UTF-8"?>')
    version = "ITU-R_BS.2076-0" if strict_profile else "ITU-R_BS.2076-2"
    a(f'<audioFormatExtended version="{version}">')

    a('        <audioProgramme audioProgrammeID="APR_1001"')
    a('                        audioProgrammeName="Main Programme"')
    a(f'                        start="{zero}" end="{dur}">')
    a('          <audioContentIDRef>ACO_1001</audioContentIDRef>')
    if not strict_profile and fmt.bs2051_system:
        a(f'          <audioProgrammeLabel language="en">ITU-R BS.2051-3 System {fmt.bs2051_system}</audioProgrammeLabel>')
    a('        </audioProgramme>')

    a('        <audioContent audioContentID="ACO_1001"')
    a(f'                      audioContentName="{fmt.name} Bed">')
    a('          <audioObjectIDRef>AO_1001</audioObjectIDRef>')
    a('          <dialogue mixedContentKind="0">2</dialogue>')
    a('        </audioContent>')

    a('        <audioObject audioObjectID="AO_1001"')
    a(f'                     audioObjectName="{fmt.name} Bed"')
    a(f'                     start="{zero}" duration="{dur}">')
    a(f'          <audioPackFormatIDRef>{pack_id}</audioPackFormatIDRef>')
    for i in range(n):
        a(f'          <audioTrackUIDRef>ATU_{i + 1:08d}</audioTrackUIDRef>')
    a('        </audioObject>')

    a(f'        <audioPackFormat audioPackFormatID="{pack_id}"')
    a(f'                         audioPackFormatName="{fmt.name} Bed"')
    a('                         typeLabel="0001" typeDefinition="DirectSpeakers">')
    for i in range(n):
        a(f'          <audioChannelFormatIDRef>{ch_id(i)}</audioChannelFormatIDRef>')
    a('        </audioPackFormat>')

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

    for i, label in enumerate(fmt.channels):
        sid = stream_id(i)
        cid = ch_id(i)
        tid = track_id(i)
        a(f'        <audioStreamFormat audioStreamFormatID="{sid}"')
        a(f'                           audioStreamFormatName="PCM_{_DOLBY_CH_NAME[label]}"')
        a('                           formatLabel="0001" formatDefinition="PCM">')
        a(f'          <audioChannelFormatIDRef>{cid}</audioChannelFormatIDRef>')
        a(f'          <audioPackFormatIDRef>{pack_id}</audioPackFormatIDRef>')
        a(f'          <audioTrackFormatIDRef>{tid}</audioTrackFormatIDRef>')
        a('        </audioStreamFormat>')

    for i, label in enumerate(fmt.channels):
        tid = track_id(i)
        sid = stream_id(i)
        a(f'        <audioTrackFormat audioTrackFormatID="{tid}"')
        a(f'                          audioTrackFormatName="PCM_{_DOLBY_CH_NAME[label]}"')
        a('                          formatLabel="0001" formatDefinition="PCM">')
        a(f'          <audioStreamFormatIDRef>{sid}</audioStreamFormatIDRef>')
        a('        </audioTrackFormat>')

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


def _dbmd_chunk() -> bytes:
    """Return the compact dbmd chunk accepted by Dolby Encoding Engine."""
    return struct.pack("<IHH", 1, 0x0001, 0)


def _audio_to_pcm(audio: np.ndarray, bit_depth: int) -> bytes:
    """Convert float64 [-1, 1] to interleaved little-endian PCM bytes.

    Scaling is by ``2 ** (bit_depth - 1)`` with the codes clipped to the
    asymmetric two's-complement range, which is libsndfile's mapping — so a
    bed already quantized by ``io.writer.dither_channels`` reproduces its own
    codes exactly here.
    """
    scale = 2 ** (bit_depth - 1)
    codes = np.clip(np.round(audio * scale), -scale, scale - 1)
    if bit_depth == 16:
        return codes.astype("<i2").tobytes()
    if bit_depth == 24:
        flat = np.ascontiguousarray(codes.astype("<i4"))
        return flat.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    if bit_depth == 32:
        return codes.astype("<i4").tobytes()
    raise ValueError(f"Unsupported bit depth for ADM BWF: {bit_depth}")
