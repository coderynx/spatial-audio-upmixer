"""Low-level RIFF/BWF/ADM-XML chunk builders backing ``adm_writer.py``.

Key design choices:
  - WAVE_FORMAT_PCM (0x0001), 16-byte fmt, no channel mask.
  - EBUCore 2016 wrapper around the BS.2076-0 audioFormatExtended element.
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
from xml.sax.saxutils import escape

import numpy as np

from upmixer.direct_speakers import direct_speakers
from upmixer.formats import ChannelLabel, OutputFormat

_DOLBY_CHANNEL: dict[str, tuple[str, str]] = {
    "M+030": ("RoomCentricLeft", "RC_L"),
    "M-030": ("RoomCentricRight", "RC_R"),
    "M+000": ("RoomCentricCenter", "RC_C"),
    "LFE1": ("RoomCentricLFE", "RC_LFE"),
    "M+110": ("RoomCentricLeftSurround", "RC_Ls"),
    "M-110": ("RoomCentricRightSurround", "RC_Rs"),
    "M+090": ("RoomCentricLeftSideSurround", "RC_Lss"),
    "M-090": ("RoomCentricRightSideSurround", "RC_Rss"),
    "M+135": ("RoomCentricLeftRearSurround", "RC_Lrs"),
    "M-135": ("RoomCentricRightRearSurround", "RC_Rrs"),
    "U+090": ("RoomCentricLeftTopSurround", "RC_Lts"),
    "U-090": ("RoomCentricRightTopSurround", "RC_Rts"),
}

_DOLBY_ZONE: dict[str, dict[str, float]] = {
    "ZM1": {
        "minX": -1.0, "maxX": 1.0, "minY": -1.0, "maxY": -0.41934,
        "minZ": -0.499, "maxZ": 0.499,
    },
    "ZM2L": {
        "minX": -1.0, "maxX": -0.75806, "minY": -0.41934,
        "maxY": 0.83871, "minZ": -0.499, "maxZ": 0.499,
    },
    "ZM2R": {
        "minX": 0.75806, "maxX": 1.0, "minY": -0.41934,
        "maxY": 0.83871, "minZ": -0.499, "maxZ": 0.499,
    },
    "ZM3L": {
        "minX": -1.0, "maxX": -0.16129, "minY": 0.5, "maxY": 1.0,
        "minZ": -0.499, "maxZ": 0.499,
    },
    "ZM3Lss": {
        "minX": -1.0, "maxX": -0.51611, "minY": -0.707,
        "maxY": 0.49999, "minZ": -0.499, "maxZ": 0.499,
    },
    "ZM3R": {
        "minX": 0.16129, "maxX": 1.0, "minY": 0.5, "maxY": 1.0,
        "minZ": -0.499, "maxZ": 0.499,
    },
    "ZM3Rss": {
        "minX": 0.51611, "maxX": 1.0, "minY": -0.707,
        "maxY": 0.49999, "minZ": -0.499, "maxZ": 0.499,
    },
    "ZM4": {
        "minX": -1.0, "maxX": 1.0, "minY": -1.0, "maxY": 0.83871,
        "minZ": -0.499, "maxZ": 0.499,
    },
    "ZM5": {
        "minX": -1.0, "maxX": 1.0, "minY": 0.5, "maxY": 1.0,
        "minZ": -0.499, "maxZ": 0.499,
    },
    "ZB": {
        "minX": -1.0, "maxX": 1.0, "minY": -1.0, "maxY": 1.0,
        "minZ": -1.0, "maxZ": -0.4995,
    },
    "ZT": {
        "minX": -1.0, "maxX": 1.0, "minY": -1.0, "maxY": 1.0,
        "minZ": 0.4995, "maxZ": 1.0,
    },
}


def _dolby_channel(
    fmt: OutputFormat, label: ChannelLabel,
) -> tuple[str, str, tuple[float, float, float]]:
    speaker = next(item for item in direct_speakers(fmt) if item.channel == label)
    name, dolby_label = _DOLBY_CHANNEL[speaker.speaker_label]
    return name, dolby_label, speaker.cartesian_position


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


def _object_size_str(v: float) -> str:
    """Render an ADM object-size scalar without losing precision."""
    return repr(v)


def _fmt_chunk(
    fmt: OutputFormat, sample_rate: int, bit_depth: int, n_channels: int | None = None,
) -> bytes:
    """Build a 16-byte WAVE_FORMAT_PCM fmt chunk.

    Logic Pro's own ADM BWF export uses WAVE_FORMAT_PCM (0x0001) with no
    channel mask — 16 bytes, no cbSize extension.
    """
    n_ch = n_channels or fmt.n_channels
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


def _chna_chunk(fmt: OutputFormat, object_count: int = 0) -> bytes:
    """Build CHNA chunk using custom Dolby-profile IDs starting from 0x1001 (§3.2)."""
    n = fmt.n_channels
    pack_id = "AP_00011001"
    data = struct.pack("<HH", n + object_count, n + object_count)

    for i, label in enumerate(fmt.channels):
        track_fmt_id = f"AT_0001{0x1001 + i:04X}_01"
        uid_str = f"ATU_{i + 1:08X}"
        data += struct.pack("<H", i + 1)
        data += _pad_field(uid_str, 12)
        data += _pad_field(track_fmt_id, 14)
        data += _pad_field(pack_id, 11)
        data += b"\x00"

    for i in range(object_count):
        number = 0x1001 + i
        uid = f"ATU_{n + i + 1:08X}"
        data += struct.pack("<H", n + i + 1)
        data += _pad_field(uid, 12)
        data += _pad_field(f"AT_0003{number:04X}_01", 14)
        data += _pad_field(f"AP_0003{number:04X}", 11)
        data += b"\x00"

    return data


def _axml_chunk(
    fmt: OutputFormat,
    duration_s: float,
    sample_rate: int,
    bit_depth: int,
    objects: tuple[
        tuple[str, tuple[float, float, float], float, bool, float, int, bool, tuple[str, ...]],
        ...,
    ] = (),
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

    def object_number(i: int) -> int:
        return 0x1001 + i

    def object_id(i: int) -> int:
        return 0x100B + i

    def xml_attr(value: str) -> str:
        return escape(value, {'"': "&quot;"})

    lines: list[str] = []
    a = lines.append

    a('<?xml version="1.0" encoding="UTF-8"?>')
    a('<ebuCoreMain xmlns="urn:ebu:metadata-schema:ebuCore_2016">')
    a('<coreMetadata>')
    a('<format>')
    a('<audioFormatExtended>')

    a('        <audioProgramme audioProgrammeID="APR_1001"')
    a('                        audioProgrammeName="Main Programme"')
    a(f'                        start="{zero}" end="{dur}">')
    a('          <audioContentIDRef>ACO_1001</audioContentIDRef>')
    a('        </audioProgramme>')

    a('        <audioContent audioContentID="ACO_1001"')
    a('                      audioContentName="Main Mix">')
    a('          <audioObjectIDRef>AO_1001</audioObjectIDRef>')
    for i in range(len(objects)):
        a(f'          <audioObjectIDRef>AO_{object_id(i):04X}</audioObjectIDRef>')
    a('          <dialogue mixedContentKind="0">2</dialogue>')
    a('        </audioContent>')

    a('        <audioObject audioObjectID="AO_1001"')
    a(f'                     audioObjectName="{fmt.name} Bed"')
    a(f'                     start="{zero}" duration="{dur}">')
    a(f'          <audioPackFormatIDRef>{pack_id}</audioPackFormatIDRef>')
    for i in range(n):
        a(f'          <audioTrackUIDRef>ATU_{i + 1:08X}</audioTrackUIDRef>')
    a('        </audioObject>')

    for i, (name, _, _, _, _, _, _, _) in enumerate(objects):
        number = object_number(i)
        a(f'        <audioObject audioObjectID="AO_{object_id(i):04X}"')
        a(f'                     audioObjectName="{xml_attr(name)}"')
        a(f'                     start="{zero}" duration="{dur}">')
        a(f'          <audioPackFormatIDRef>AP_0003{number:04X}</audioPackFormatIDRef>')
        a(f'          <audioTrackUIDRef>ATU_{n + i + 1:08X}</audioTrackUIDRef>')
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
        name, speaker, (x, y, z) = _dolby_channel(fmt, label)

        a(f'        <audioChannelFormat audioChannelFormatID="{cid}"')
        a(f'                            audioChannelFormatName="{name}"')
        a('                            typeLabel="0001" typeDefinition="DirectSpeakers">')
        if label == ChannelLabel.LFE:
            a('          <frequency typeDefinition="lowPass">120</frequency>')
        a(f'          <audioBlockFormat audioBlockFormatID="{bid}">')
        a('            <cartesian>1</cartesian>')
        a(f'            <position coordinate="X">{_pos_str(x)}</position>')
        a(f'            <position coordinate="Y">{_pos_str(y)}</position>')
        a(f'            <position coordinate="Z">{_pos_str(z)}</position>')
        a(f'            <speakerLabel>{speaker}</speakerLabel>')
        a('          </audioBlockFormat>')
        a('        </audioChannelFormat>')

    for i, (name, position, extent, diffuse, gain, importance, channel_lock, zones) in enumerate(objects):
        number = object_number(i)
        x, y, z = position
        a(f'        <audioPackFormat audioPackFormatID="AP_0003{number:04X}"')
        a(f'                         audioPackFormatName="{xml_attr(name)}"')
        a('                         typeLabel="0003" typeDefinition="Objects">')
        a(f'          <audioChannelFormatIDRef>AC_0003{number:04X}</audioChannelFormatIDRef>')
        a('        </audioPackFormat>')
        a(f'        <audioChannelFormat audioChannelFormatID="AC_0003{number:04X}"')
        a(f'                            audioChannelFormatName="{xml_attr(name)}"')
        a('                            typeLabel="0003" typeDefinition="Objects">')
        a(f'          <audioBlockFormat audioBlockFormatID="AB_0003{number:04X}_00000001">')
        a('            <cartesian>1</cartesian>')
        a(f'            <position coordinate="X">{_pos_str(x)}</position>')
        a(f'            <position coordinate="Y">{_pos_str(y)}</position>')
        a(f'            <position coordinate="Z">{_pos_str(z)}</position>')
        if extent > 0.0:
            a(f'            <width>{_object_size_str(extent)}</width>')
            a(f'            <height>{_object_size_str(extent)}</height>')
            a(f'            <depth>{_object_size_str(extent)}</depth>')
        if diffuse:
            a('            <diffuse>1</diffuse>')
        if gain != 1.0:
            a(f'            <gain>{_object_size_str(gain)}</gain>')
        if importance != 10:
            a(f'            <importance>{importance}</importance>')
        if channel_lock:
            a('            <channelLock>1</channelLock>')
        if zones:
            a('            <zoneExclusion>')
            for zone in zones:
                values = _DOLBY_ZONE[zone]
                attrs = " ".join(f'{key}="{_pos_str(value)}"' for key, value in values.items())
                a(f'              <zone {attrs} />')
            a('            </zoneExclusion>')
        a('            <jumpPosition interpolationLength="0.000000">1</jumpPosition>')
        a('          </audioBlockFormat>')
        a('        </audioChannelFormat>')

    for i, label in enumerate(fmt.channels):
        sid = stream_id(i)
        cid = ch_id(i)
        tid = track_id(i)
        channel_name, _, _ = _dolby_channel(fmt, label)
        a(f'        <audioStreamFormat audioStreamFormatID="{sid}"')
        a(f'                           audioStreamFormatName="PCM_{channel_name}"')
        a('                           formatLabel="0001" formatDefinition="PCM">')
        a(f'          <audioChannelFormatIDRef>{cid}</audioChannelFormatIDRef>')
        a(f'          <audioPackFormatIDRef>{pack_id}</audioPackFormatIDRef>')
        a(f'          <audioTrackFormatIDRef>{tid}</audioTrackFormatIDRef>')
        a('        </audioStreamFormat>')

    for i, (name, _, _, _, _, _, _, _) in enumerate(objects):
        number = object_number(i)
        a(f'        <audioStreamFormat audioStreamFormatID="AS_0003{number:04X}"')
        a(f'                           audioStreamFormatName="PCM_{xml_attr(name)}"')
        a('                           formatLabel="0001" formatDefinition="PCM">')
        a(f'          <audioChannelFormatIDRef>AC_0003{number:04X}</audioChannelFormatIDRef>')
        a(f'          <audioPackFormatIDRef>AP_0003{number:04X}</audioPackFormatIDRef>')
        a(f'          <audioTrackFormatIDRef>AT_0003{number:04X}_01</audioTrackFormatIDRef>')
        a('        </audioStreamFormat>')

    for i, label in enumerate(fmt.channels):
        tid = track_id(i)
        sid = stream_id(i)
        channel_name, _, _ = _dolby_channel(fmt, label)
        a(f'        <audioTrackFormat audioTrackFormatID="{tid}"')
        a(f'                          audioTrackFormatName="PCM_{channel_name}"')
        a('                          formatLabel="0001" formatDefinition="PCM">')
        a(f'          <audioStreamFormatIDRef>{sid}</audioStreamFormatIDRef>')
        a('        </audioTrackFormat>')

    for i, (name, _, _, _, _, _, _, _) in enumerate(objects):
        number = object_number(i)
        a(f'        <audioTrackFormat audioTrackFormatID="AT_0003{number:04X}_01"')
        a(f'                          audioTrackFormatName="PCM_{xml_attr(name)}"')
        a('                          formatLabel="0001" formatDefinition="PCM">')
        a(f'          <audioStreamFormatIDRef>AS_0003{number:04X}</audioStreamFormatIDRef>')
        a('        </audioTrackFormat>')

    for i, label in enumerate(fmt.channels):
        uid = f"ATU_{i + 1:08X}"
        tid = track_id(i)
        a(f'        <audioTrackUID UID="{uid}"')
        a(f'                       sampleRate="{sample_rate}"')
        a(f'                       bitDepth="{bit_depth}">')
        a(f'          <audioTrackFormatIDRef>{tid}</audioTrackFormatIDRef>')
        a(f'          <audioPackFormatIDRef>{pack_id}</audioPackFormatIDRef>')
        a('        </audioTrackUID>')

    for i in range(len(objects)):
        number = object_number(i)
        a(f'        <audioTrackUID UID="ATU_{n + i + 1:08X}"')
        a(f'                       sampleRate="{sample_rate}"')
        a(f'                       bitDepth="{bit_depth}">')
        a(f'          <audioTrackFormatIDRef>AT_0003{number:04X}_01</audioTrackFormatIDRef>')
        a(f'          <audioPackFormatIDRef>AP_0003{number:04X}</audioPackFormatIDRef>')
        a('        </audioTrackUID>')

    a('</audioFormatExtended>')
    a('</format>')
    a('</coreMetadata>')
    a('</ebuCoreMain>')

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
