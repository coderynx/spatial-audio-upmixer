"""Dolby Encoding Engine ADM-BWF compatibility tests."""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.io.adm_writer import AdmBwfWriter, _DOLBY_ENGINE_ALLOWED_FORMATS


def _chunks(data: bytes) -> dict[bytes, bytes]:
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    result: dict[bytes, bytes] = {}
    pos = 12
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        result[tag] = data[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    return result


@pytest.fixture()
def adm_712(tmp_path):
    output = tmp_path / "out.adm.wav"
    config = UpmixConfig(output_format="7.1.2")
    channels = {
        label.value: np.zeros(4800, dtype=np.float64)
        for label in FORMAT_MAP["7.1.2"].channels
    }
    AdmBwfWriter(str(output), 48_000, config).write(channels, -18.0, -1.0)
    chunks = _chunks(output.read_bytes())
    return ET.fromstring(chunks[b"axml"]), chunks


def test_allows_dolby_engine_layouts():
    assert _DOLBY_ENGINE_ALLOWED_FORMATS == {"5.1", "7.1", "5.1.2", "5.1.4", "7.1.2", "7.1.4"}


def test_allows_7_1_4(tmp_path):
    config = UpmixConfig(output_format="7.1.4")
    channels = {label.value: np.zeros(32) for label in FORMAT_MAP["7.1.4"].channels}
    AdmBwfWriter(str(tmp_path / "out.wav"), 48_000, config).write(channels)


def test_uses_default_dbmd_payload_when_not_supplied(tmp_path):
    config = UpmixConfig(output_format="5.1")
    channels = {label.value: np.zeros(32) for label in FORMAT_MAP["5.1"].channels}
    output = tmp_path / "out.wav"
    AdmBwfWriter(str(output), 48_000, config).write(channels)
    assert _chunks(output.read_bytes())[b"dbmd"] == struct.pack("<IHH", 1, 1, 0)


@pytest.mark.parametrize("sample_rate, subtype", [(44_100, "PCM_24"), (48_000, "PCM_16")])
def test_requires_profile_pcm_format(tmp_path, sample_rate, subtype):
    config = UpmixConfig(output_format="5.1", output_subtype=subtype)
    channels = {label.value: np.zeros(32) for label in FORMAT_MAP["5.1"].channels}
    with pytest.raises(ValueError, match="requires"):
        AdmBwfWriter(str(tmp_path / "out.wav"), sample_rate, config).write(channels)


def test_required_chunks_and_default_dbmd(adm_712):
    _, chunks = adm_712
    assert {b"fmt ", b"bext", b"data", b"axml", b"chna", b"dbmd"} <= chunks.keys()
    assert chunks[b"dbmd"] == struct.pack("<IHH", 1, 1, 0)


def test_xml_uses_engine_adm_version(adm_712):
    root, _ = adm_712
    assert root.attrib["version"] == "ITU-R_BS.2076-2"
    programme = root.find("audioProgramme")
    assert programme is not None
    assert [child.tag for child in programme] == ["audioContentIDRef"]


def test_direct_speakers_blocks_and_track_order(adm_712):
    root, _ = adm_712
    expected = ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR"]
    assert [ref.text for ref in root.findall("audioObject/audioTrackUIDRef")] == [
        f"ATU_{i:08d}" for i in range(1, 11)
    ]
    assert len(root.findall("audioChannelFormat")) == len(expected)
    for channel in root.findall("audioChannelFormat"):
        block = channel.find("audioBlockFormat")
        assert block is not None
        assert block.findtext("cartesian") == "1"
        assert len(block.findall("position")) == 3
        assert len(block.findall("speakerLabel")) == 1
        assert block.find("jumpPosition") is None


def test_bext_uses_final_measurements(adm_712):
    _, chunks = adm_712
    assert struct.unpack_from("<h", chunks[b"bext"], 412)[0] == -1800
    assert struct.unpack_from("<h", chunks[b"bext"], 416)[0] == -100
