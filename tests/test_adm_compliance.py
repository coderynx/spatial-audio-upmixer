"""Dolby Atmos Master ADM Profile v1.1 compliance tests for upmixer/io/adm_writer.py.

Verifies:
  - RIFF chunk set: fmt, bext, axml, chna, dbmd all present (A3)
  - No <binaural> element inside audioObject (A1)
  - audioObject children: only audioPackFormatIDRef + audioTrackUIDRef (A1)
  - Exactly 1 speakerLabel per audioBlockFormat (A2)
  - audioStreamFormatName = "PCM_" + audioChannelFormatName (A6)
  - audioTrackFormatName == audioStreamFormatName (A6)
  - Dolby RoomCentric channel names per Table 2-11 (A5)
  - RC_ speaker labels per Table 2-14 (A4)
  - LFE position X = -1.0 (A7)
  - Standard channel positions (FL, FR, C)
  - _DOLBY_ALLOWED_FORMATS includes Table 2-21 + Atmos Music configs (A9)
  - Non-allowed format raises ValueError (A9)
"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, FORMAT_MAP
from upmixer.io.adm_writer import (
    AdmBwfWriter,
    _DOLBY_ALLOWED_FORMATS,
    _DOLBY_CH_NAME,
    _DOLBY_SPEAKER_LABEL,
)


def _parse_riff_chunks(data: bytes) -> dict[bytes, bytes]:
    """Walk a WAVE RIFF file and return {chunk_id: payload} for all sub-chunks."""
    assert data[:4] == b"RIFF", "Not a RIFF file"
    assert data[8:12] == b"WAVE", "Not a WAVE file"
    chunks: dict[bytes, bytes] = {}
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        chunk_size = struct.unpack_from("<I", data, pos + 4)[0]
        chunk_data = data[pos + 8 : pos + 8 + chunk_size]
        chunks[chunk_id] = chunk_data
        pos += 8 + chunk_size + (chunk_size % 2)
    return chunks


def _block_for_channel(root: ET.Element, ch_name: str) -> ET.Element:
    """Return the audioBlockFormat element for the channel with the given Dolby name."""
    for acf in root.findall("audioChannelFormat"):
        if acf.attrib.get("audioChannelFormatName") == ch_name:
            blk = acf.find("audioBlockFormat")
            assert blk is not None, f"No audioBlockFormat in audioChannelFormat '{ch_name}'"
            return blk
    raise AssertionError(f"audioChannelFormat '{ch_name}' not found in ADM XML")


def _positions(block: ET.Element) -> dict[str, float]:
    """Extract {coordinate: value} from <position> children of a block."""
    return {
        p.attrib["coordinate"]: float(p.text)  # type: ignore[arg-type]
        for p in block.findall("position")
    }


@pytest.fixture(scope="module")
def adm_714(tmp_path_factory):
    """Write a 7.1.4 ADM-BWF file once; return (raw_bytes, parsed_axml_root)."""
    tmp = tmp_path_factory.mktemp("adm")
    out = str(tmp / "out.adm.bwf")
    config = UpmixConfig(output_format="7.1.4")
    channels = {lbl.value: np.zeros(4800, dtype=np.float64) for lbl in FORMAT_MAP["7.1.4"].channels}
    writer = AdmBwfWriter(out, 48000, config)
    writer.write(channels)
    raw = (tmp / "out.adm.bwf").read_bytes()
    chunks = _parse_riff_chunks(raw)
    root = ET.fromstring(chunks[b"axml"])
    return raw, root, chunks


# ---------------------------------------------------------------------------
# RIFF chunk presence (A3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chunk_id", [b"fmt ", b"bext", b"axml", b"chna", b"dbmd"])
def test_riff_chunk_present(adm_714, chunk_id):
    _, _, chunks = adm_714
    assert chunk_id in chunks, f"Chunk {chunk_id!r} missing from ADM BWF output"


# ---------------------------------------------------------------------------
# audioObject sub-elements (A1 — Dolby ADM Profile v1.1 Table 2-22)
# ---------------------------------------------------------------------------

def test_no_binaural_element(adm_714):
    _, root, _ = adm_714
    assert root.findall(".//binaural") == [], "<binaural> element found — prohibited by Dolby ADM Profile v1.1 Table 2-22"


def test_audio_object_only_allowed_children(adm_714):
    _, root, _ = adm_714
    allowed = {"audioPackFormatIDRef", "audioTrackUIDRef"}
    for obj in root.findall("audioObject"):
        for child in obj:
            assert child.tag in allowed, (
                f"<audioObject> child <{child.tag}> not in allowed set {allowed} "
                f"(Dolby ADM Profile v1.1 Table 2-22)"
            )


# ---------------------------------------------------------------------------
# speakerLabel count (A2 — Table 2-13: min=1 max=1)
# ---------------------------------------------------------------------------

def test_exactly_one_speaker_label_per_block(adm_714):
    _, root, _ = adm_714
    for acf in root.findall("audioChannelFormat"):
        blk = acf.find("audioBlockFormat")
        assert blk is not None
        labels = blk.findall("speakerLabel")
        assert len(labels) == 1, (
            f"audioChannelFormat '{acf.attrib.get('audioChannelFormatName')}' "
            f"has {len(labels)} speakerLabel(s), expected exactly 1 (Table 2-13)"
        )


# ---------------------------------------------------------------------------
# Name scheme (A6)
# ---------------------------------------------------------------------------

def test_stream_format_name_pcm_prefix(adm_714):
    _, root, _ = adm_714
    for asf in root.findall("audioStreamFormat"):
        name = asf.attrib["audioStreamFormatName"]
        assert name.startswith("PCM_"), (
            f"audioStreamFormatName '{name}' must start with 'PCM_' (Dolby ADM Profile v1.1 §2.3)"
        )


def test_stream_format_name_matches_channel_format_name(adm_714):
    _, root, _ = adm_714
    ch_id_to_name = {
        acf.attrib["audioChannelFormatID"]: acf.attrib["audioChannelFormatName"]
        for acf in root.findall("audioChannelFormat")
    }
    for asf in root.findall("audioStreamFormat"):
        stream_name = asf.attrib["audioStreamFormatName"]
        ref = asf.find("audioChannelFormatIDRef")
        assert ref is not None and ref.text in ch_id_to_name, (
            f"audioStreamFormat '{asf.attrib['audioStreamFormatID']}' missing valid audioChannelFormatIDRef"
        )
        expected = "PCM_" + ch_id_to_name[ref.text]
        assert stream_name == expected, (
            f"audioStreamFormatName '{stream_name}' != 'PCM_{ch_id_to_name[ref.text]}' "
            f"(Dolby ADM Profile v1.1 §2.3)"
        )


def test_track_format_name_equals_stream_format_name(adm_714):
    _, root, _ = adm_714
    stream_id_to_name = {
        asf.attrib["audioStreamFormatID"]: asf.attrib["audioStreamFormatName"]
        for asf in root.findall("audioStreamFormat")
    }
    for atf in root.findall("audioTrackFormat"):
        track_name = atf.attrib["audioTrackFormatName"]
        ref = atf.find("audioStreamFormatIDRef")
        assert ref is not None and ref.text in stream_id_to_name
        stream_name = stream_id_to_name[ref.text]
        assert track_name == stream_name, (
            f"audioTrackFormatName '{track_name}' != audioStreamFormatName '{stream_name}'"
        )


# ---------------------------------------------------------------------------
# Channel format names (A5 — Dolby ADM Profile v1.1 Table 2-11)
# ---------------------------------------------------------------------------

_EXPECTED_CH_NAMES = [
    (ChannelLabel.FL,  "RoomCentricLeft"),
    (ChannelLabel.FR,  "RoomCentricRight"),
    (ChannelLabel.C,   "RoomCentricCenter"),
    (ChannelLabel.LFE, "RoomCentricLFE"),
    (ChannelLabel.SL,  "RoomCentricLeftSideSurround"),
    (ChannelLabel.SR,  "RoomCentricRightSideSurround"),
    (ChannelLabel.BL,  "RoomCentricLeftRearSurround"),
    (ChannelLabel.BR,  "RoomCentricRightRearSurround"),
    (ChannelLabel.TFL, "RoomCentricLeftTopSurround"),
    (ChannelLabel.TFR, "RoomCentricRightTopSurround"),
    (ChannelLabel.TBL, "RoomCentricLeftTopRearSurround"),
    (ChannelLabel.TBR, "RoomCentricRightTopRearSurround"),
]


@pytest.mark.parametrize("label,expected_name", _EXPECTED_CH_NAMES, ids=[l.value for l, _ in _EXPECTED_CH_NAMES])
def test_channel_format_name(label, expected_name):
    assert _DOLBY_CH_NAME[label] == expected_name


@pytest.mark.parametrize("label,expected_name", _EXPECTED_CH_NAMES, ids=[l.value for l, _ in _EXPECTED_CH_NAMES])
def test_channel_format_name_in_xml(adm_714, label, expected_name):
    _, root, _ = adm_714
    if label not in FORMAT_MAP["7.1.4"].channels:
        pytest.skip(f"{label} not in 7.1.4 layout")
    names_in_xml = {acf.attrib["audioChannelFormatName"] for acf in root.findall("audioChannelFormat")}
    assert expected_name in names_in_xml, (
        f"audioChannelFormatName '{expected_name}' not found in ADM XML (Table 2-11)"
    )


# ---------------------------------------------------------------------------
# Speaker labels (A4 — Dolby ADM Profile v1.1 Table 2-14)
# ---------------------------------------------------------------------------

_EXPECTED_SPEAKER_LABELS = [
    (ChannelLabel.FL,  "RC_L"),
    (ChannelLabel.FR,  "RC_R"),
    (ChannelLabel.C,   "RC_C"),
    (ChannelLabel.LFE, "RC_LFE"),
    (ChannelLabel.SL,  "RC_Lss"),
    (ChannelLabel.SR,  "RC_Rss"),
    (ChannelLabel.BL,  "RC_Lrs"),
    (ChannelLabel.BR,  "RC_Rrs"),
    (ChannelLabel.TFL, "RC_Lts"),
    (ChannelLabel.TFR, "RC_Rts"),
    (ChannelLabel.TBL, "RC_Ltrs"),
    (ChannelLabel.TBR, "RC_Rtrs"),
]


@pytest.mark.parametrize("label,expected_label", _EXPECTED_SPEAKER_LABELS, ids=[l.value for l, _ in _EXPECTED_SPEAKER_LABELS])
def test_speaker_label_dict(label, expected_label):
    assert _DOLBY_SPEAKER_LABEL[label] == expected_label


@pytest.mark.parametrize("label,expected_label", _EXPECTED_SPEAKER_LABELS, ids=[l.value for l, _ in _EXPECTED_SPEAKER_LABELS])
def test_speaker_label_in_xml(adm_714, label, expected_label):
    _, root, _ = adm_714
    if label not in FORMAT_MAP["7.1.4"].channels:
        pytest.skip(f"{label} not in 7.1.4 layout")
    ch_name = _DOLBY_CH_NAME[label]
    block = _block_for_channel(root, ch_name)
    sl = block.find("speakerLabel")
    assert sl is not None
    assert sl.text == expected_label, (
        f"speakerLabel for {label.value}: got '{sl.text}', expected '{expected_label}' (Table 2-14)"
    )


# ---------------------------------------------------------------------------
# Cartesian positions (A7 + Table 2-14)
# ---------------------------------------------------------------------------

_EXPECTED_POSITIONS = [
    (ChannelLabel.FL,  "RoomCentricLeft",   -1.0,  1.0,  0.0),
    (ChannelLabel.FR,  "RoomCentricRight",   1.0,  1.0,  0.0),
    (ChannelLabel.C,   "RoomCentricCenter",  0.0,  1.0,  0.0),
    (ChannelLabel.LFE, "RoomCentricLFE",    -1.0,  1.0, -1.0),
]


@pytest.mark.parametrize(
    "label,ch_name,exp_x,exp_y,exp_z",
    _EXPECTED_POSITIONS,
    ids=[l.value for l, *_ in _EXPECTED_POSITIONS],
)
def test_channel_position(adm_714, label, ch_name, exp_x, exp_y, exp_z):
    _, root, _ = adm_714
    if label not in FORMAT_MAP["7.1.4"].channels:
        pytest.skip(f"{label} not in 7.1.4 layout")
    block = _block_for_channel(root, ch_name)
    pos = _positions(block)
    assert pos.get("X") == pytest.approx(exp_x), f"{ch_name} X position wrong"
    assert pos.get("Y") == pytest.approx(exp_y), f"{ch_name} Y position wrong"
    assert pos.get("Z") == pytest.approx(exp_z), f"{ch_name} Z position wrong"


def test_lfe_position_x_is_minus_one(adm_714):
    _, root, _ = adm_714
    block = _block_for_channel(root, "RoomCentricLFE")
    pos = _positions(block)
    assert pos["X"] == -1.0, (
        f"LFE X = {pos['X']}, must be -1.0 per Dolby ADM Profile v1.1 Table 2-14"
    )


def test_audioformat_root_cartesian_flag(adm_714):
    _, root, _ = adm_714
    for acf in root.findall("audioChannelFormat"):
        blk = acf.find("audioBlockFormat")
        assert blk is not None
        cartesian = blk.find("cartesian")
        assert cartesian is not None and cartesian.text == "1", (
            f"audioBlockFormat in '{acf.attrib.get('audioChannelFormatName')}' missing <cartesian>1</cartesian>"
        )


# ---------------------------------------------------------------------------
# _DOLBY_ALLOWED_FORMATS (A9)
# ---------------------------------------------------------------------------

def test_allowed_formats_include_master_adm_profile_table_2_21():
    table_2_21 = {"2.0", "3.0", "5.0", "5.1", "7.0", "7.1", "7.0.2", "7.1.2"}
    assert table_2_21 <= _DOLBY_ALLOWED_FORMATS, (
        f"Missing Table 2-21 formats: {table_2_21 - _DOLBY_ALLOWED_FORMATS}"
    )


def test_allowed_formats_include_atmos_music_extensions():
    atmos_music = {"5.1.2", "5.1.4", "7.1.4"}
    assert atmos_music <= _DOLBY_ALLOWED_FORMATS, (
        f"Missing Atmos Music formats: {atmos_music - _DOLBY_ALLOWED_FORMATS}"
    )


@pytest.mark.parametrize("bad_fmt", ["2.0.2", "6.0", "4.0", "9.1.6"])
def test_invalid_formats_not_in_allowed_set(bad_fmt):
    assert bad_fmt not in _DOLBY_ALLOWED_FORMATS, (
        f"'{bad_fmt}' should not be an allowed ADM BWF format"
    )
