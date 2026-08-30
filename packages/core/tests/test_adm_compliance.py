"""Dolby Encoding Engine ADM-BWF compatibility tests."""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, validate_delivery
from upmixer.io.adm_writer import AdmBwfWriter, AdmObject, _DOLBY_ENGINE_ALLOWED_FORMATS
from upmixer.separation.stem_router import StemRouter


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


def _audio_format_extended(xml: bytes) -> ET.Element:
    document = ET.fromstring(xml)
    assert document.tag == "{urn:ebu:metadata-schema:ebuCore_2016}ebuCoreMain"
    root = document.find(".//{*}audioFormatExtended")
    assert root is not None
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


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
    return _audio_format_extended(chunks[b"axml"]), chunks


def test_allows_dolby_engine_layouts():
    assert _DOLBY_ENGINE_ALLOWED_FORMATS == {"5.1", "7.1", "7.1.2"}


@pytest.mark.parametrize("layout", ["stereo", "5.1.2", "5.1.4", "7.1.4"])
def test_rejects_non_profile_adm_deliveries(layout):
    with pytest.raises(ValueError, match="adm-bwf output requires"):
        validate_delivery(layout, "adm-bwf")


def test_rejects_non_profile_7_1_4_bed(tmp_path):
    config = UpmixConfig(output_format="7.1.4")
    channels = {label.value: np.zeros(32) for label in FORMAT_MAP["7.1.4"].channels}
    with pytest.raises(ValueError, match="supported ADM BWF bed"):
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


def test_xml_uses_dolby_bs2076_0_profile(adm_712):
    root, _ = adm_712
    assert "version" not in root.attrib
    programme = root.find("audioProgramme")
    assert programme is not None
    assert [child.tag for child in programme] == ["audioContentIDRef"]


def test_direct_speakers_blocks_and_track_order(adm_712):
    root, _ = adm_712
    expected = ["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR"]
    assert [ref.text for ref in root.findall("audioObject/audioTrackUIDRef")] == [
        f"ATU_{i:08X}" for i in range(1, 11)
    ]
    assert len(root.findall("audioChannelFormat")) == len(expected)
    for channel in root.findall("audioChannelFormat"):
        block = channel.find("audioBlockFormat")
        assert block is not None
        assert block.findtext("cartesian") == "1"
        assert len(block.findall("position")) == 3
        assert len(block.findall("speakerLabel")) == 1
        assert block.find("jumpPosition") is None
    assert root.findall("audioChannelFormat")[3].findtext(
        "frequency[@typeDefinition='lowPass']"
    ) == "120"
    heights = root.findall("audioChannelFormat")[8:10]
    assert [channel.findtext("audioBlockFormat/speakerLabel") for channel in heights] == [
        "RC_Lts", "RC_Rts",
    ]
    assert [
        float(channel.find("audioBlockFormat/position[@coordinate='Y']").text)
        for channel in heights
    ] == [0.0, 0.0]


def test_bext_uses_final_measurements(adm_712):
    _, chunks = adm_712
    assert struct.unpack_from("<h", chunks[b"bext"], 412)[0] == -1800
    assert struct.unpack_from("<h", chunks[b"bext"], 416)[0] == -100


def test_writes_one_bed_and_a_direct_object(tmp_path):
    config = UpmixConfig(output_format="5.1")
    channels = {label.value: np.zeros(32) for label in FORMAT_MAP["5.1"].channels}
    output = tmp_path / "out.wav"
    AdmBwfWriter(str(output), 48_000, config).write(
        channels,
        objects=[AdmObject("Vocals", np.ones(32) * 0.1, (0.0, 1.0, 0.0))],
    )

    chunks = _chunks(output.read_bytes())
    root = _audio_format_extended(chunks[b"axml"])
    assert struct.unpack_from("<H", chunks[b"fmt "], 2)[0] == 7
    assert [obj.attrib["audioObjectID"] for obj in root.findall("audioObject")] == [
        "AO_1001", "AO_100B",
    ]
    assert len(root.findall("audioObject/audioTrackUIDRef")) == 7
    obj_channel = root.findall("audioChannelFormat")[-1]
    assert obj_channel.attrib["typeDefinition"] == "Objects"
    assert obj_channel.findtext("audioBlockFormat/cartesian") == "1"
    assert obj_channel.findtext("audioBlockFormat/jumpPosition") == "1"


def test_writes_object_extent_and_diffuse(tmp_path):
    config = UpmixConfig(output_format="5.1")
    channels = {label.value: np.zeros(32) for label in FORMAT_MAP["5.1"].channels}
    output = tmp_path / "out.wav"
    AdmBwfWriter(str(output), 48_000, config).write(
        channels,
        objects=[AdmObject("Crowd", np.ones(32) * 0.1, (0.0, -1.0, 0.0), 0.5, True)],
    )

    root = _audio_format_extended(_chunks(output.read_bytes())[b"axml"])
    block = root.findall("audioChannelFormat")[-1].find("audioBlockFormat")
    assert block is not None
    assert [block.findtext(tag) for tag in ("width", "height", "depth")] == ["0.5"] * 3
    assert block.findtext("diffuse") == "1"


def test_adm_lead_vocal_stereo_objects_are_panned_left_and_right(tmp_path):
    config = UpmixConfig(output_format="5.1")
    audio = np.column_stack([np.ones(32), -np.ones(32)])
    objects: list[AdmObject] = []
    bed = StemRouter(config, FORMAT_MAP["5.1"], 48_000).route(
        {"Lead Vocals": audio}, len(audio), object_tracks=objects,
    )
    output = tmp_path / "out.wav"
    AdmBwfWriter(str(output), 48_000, config).write(bed, objects=objects)

    root = _audio_format_extended(_chunks(output.read_bytes())[b"axml"])
    positions = {
        channel.attrib["audioChannelFormatName"]: {
            position.attrib["coordinate"]: float(position.text)
            for position in channel.findall("audioBlockFormat/position")
        }
        for channel in root.findall("audioChannelFormat")
        if channel.attrib["audioChannelFormatName"].startswith("Lead Vocals")
    }

    assert positions["Lead Vocals Left"]["X"] < 0.0
    assert positions["Lead Vocals Right"]["X"] > 0.0
    assert all(obj.object_size == 0.0 for obj in objects)


def test_writes_object_gain_importance_channel_lock_and_zones(tmp_path):
    config = UpmixConfig(output_format="5.1")
    channels = {label.value: np.zeros(32) for label in FORMAT_MAP["5.1"].channels}
    output = tmp_path / "out.wav"
    AdmBwfWriter(str(output), 48_000, config).write(
        channels,
        objects=[AdmObject(
            "Voice & FX", np.ones(32) * 0.1, (0.0, 1.0, 0.0),
            gain=0.5, importance=7, channel_lock=True,
            zone_exclusion=("ZM1", "ZT"),
        )],
    )

    root = _audio_format_extended(_chunks(output.read_bytes())[b"axml"])
    block = root.findall("audioChannelFormat")[-1].find("audioBlockFormat")
    assert block is not None
    assert block.findtext("gain") == "0.5"
    assert block.findtext("importance") == "7"
    assert block.findtext("channelLock") == "1"
    assert len(block.findall("zoneExclusion/zone")) == 2
    assert root.findall("audioObject")[-1].attrib["audioObjectName"] == "Voice & FX"


def test_five_one_bed_uses_dolby_surround_channels(tmp_path):
    config = UpmixConfig(output_format="5.1")
    channels = {label.value: np.zeros(32) for label in FORMAT_MAP["5.1"].channels}
    output = tmp_path / "out.wav"
    AdmBwfWriter(str(output), 48_000, config).write(channels)

    root = _audio_format_extended(_chunks(output.read_bytes())[b"axml"])
    surrounds = root.findall("audioChannelFormat")[4:6]
    assert [channel.findtext("audioBlockFormat/speakerLabel") for channel in surrounds] == [
        "RC_Ls", "RC_Rs",
    ]
    assert [channel.find("audioBlockFormat/position[@coordinate='Y']").text for channel in surrounds] == [
        "-1", "-1",
    ]
