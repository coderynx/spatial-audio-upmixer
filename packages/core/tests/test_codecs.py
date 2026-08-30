import numpy as np
import pytest
import soundfile as sf

from upmixer.codecs import (
    CODECS,
    codec_extension,
    delivered_channels,
    get_codec,
    resolve_subtype,
    validate_codec,
)
from upmixer.config import UpmixConfig
from upmixer.io.writer import write_audio


class TestCodecTable:
    def test_extensions_are_distinct_per_codec(self):
        extensions = {name: codec_extension(name) for name in CODECS}
        assert extensions == {
            "wav_pcm": ".wav",
            "flac": ".flac",
            "ogg_vorbis": ".ogg",
            "ogg_opus": ".opus",
        }

    def test_unknown_codec_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown output codec"):
            get_codec("mp3")

    def test_lossy_codecs_ignore_the_configured_bit_depth(self):
        assert resolve_subtype("ogg_vorbis", "PCM_24") == "VORBIS"
        assert resolve_subtype("ogg_opus", "PCM_24") == "OPUS"

    def test_pcm_codecs_keep_the_configured_bit_depth(self):
        assert resolve_subtype("wav_pcm", "PCM_32") == "PCM_32"
        assert resolve_subtype("flac", "PCM_16") == "PCM_16"


class TestDeliveredChannels:
    def test_a_bed_delivers_its_own_channel_count(self):
        assert delivered_channels("7.1.4", "multichannel") == 12
        assert delivered_channels("5.1", "multichannel") == 6

    @pytest.mark.parametrize("output_type", ["binaural", "transaural"])
    def test_a_collapsed_bed_delivers_two_channels(self, output_type):
        assert delivered_channels("7.1.4", output_type) == 2


class TestValidateCodec:
    def test_accepts_a_wide_bed_as_wav(self):
        validate_codec("7.1.4", "multichannel", "wav_pcm", "PCM_24")

    def test_requires_profile_adm_pcm_and_rate(self):
        with pytest.raises(ValueError, match="PCM_24"):
            validate_codec("5.1", "adm-bwf", "wav_pcm", "PCM_16")
        with pytest.raises(ValueError, match="48 kHz or 96 kHz"):
            validate_codec("5.1", "adm-bwf", "wav_pcm", "PCM_24", 44_100)

    @pytest.mark.parametrize("layout", ["5.1.4", "7.1.2", "7.1.4"])
    def test_rejects_flac_above_eight_channels(self, layout):
        with pytest.raises(ValueError, match="at most 8 channels"):
            validate_codec(layout, "multichannel", "flac", "PCM_24")

    @pytest.mark.parametrize("layout", ["5.1", "7.1", "5.1.2"])
    def test_accepts_flac_up_to_eight_channels(self, layout):
        validate_codec(layout, "multichannel", "flac", "PCM_24")

    def test_accepts_flac_for_a_collapsed_wide_bed(self):
        validate_codec("7.1.4", "binaural", "flac", "PCM_24")

    @pytest.mark.parametrize("subtype", ["PCM_32", "FLOAT"])
    def test_rejects_a_bit_depth_flac_cannot_carry(self, subtype):
        with pytest.raises(ValueError, match="does not support subtype"):
            validate_codec("5.1", "multichannel", "flac", subtype)

    @pytest.mark.parametrize("sample_rate", [44100, 88200, 96000, 192000])
    def test_rejects_opus_off_its_supported_rates(self, sample_rate):
        with pytest.raises(ValueError, match="supports only"):
            validate_codec("5.1", "multichannel", "ogg_opus", "PCM_24", sample_rate)

    def test_accepts_opus_at_48_khz(self):
        validate_codec("5.1", "multichannel", "ogg_opus", "PCM_24", 48_000)

    def test_ignores_the_bit_depth_for_a_lossy_codec(self):
        validate_codec("7.1.4", "multichannel", "ogg_vorbis", "FLOAT")

    @pytest.mark.parametrize("codec", ["flac", "ogg_vorbis", "ogg_opus"])
    def test_rejects_a_non_wav_codec_for_adm_bwf(self, codec):
        with pytest.raises(ValueError, match="WAV container only"):
            validate_codec("7.1.4", "adm-bwf", codec, "PCM_24")


class TestWriteAudio:
    @pytest.mark.parametrize(
        "codec,channels,sample_rate",
        [
            ("wav_pcm", 12, 48_000),
            ("flac", 6, 44_100),
            ("ogg_vorbis", 12, 48_000),
            ("ogg_opus", 2, 48_000),
        ],
    )
    def test_round_trips_through_every_codec(self, tmp_path, codec, channels, sample_rate):
        audio = np.zeros((sample_rate, channels), dtype=np.float32)
        audio[:, 0] = 0.25 * np.sin(
            2 * np.pi * 440.0 * np.arange(sample_rate) / sample_rate
        )
        destination = tmp_path / f"out{codec_extension(codec)}"
        write_audio(destination, audio, sample_rate, codec, "PCM_24", "tpdf", 1)

        info = sf.info(str(destination))
        assert info.channels == channels
        assert info.samplerate == sample_rate
        assert info.format == get_codec(codec).container

    def test_publishes_atomically_and_leaves_no_temporary(self, tmp_path):
        destination = tmp_path / "out.flac"
        write_audio(
            destination, np.zeros((480, 2), dtype=np.float32), 48_000, "flac",
            "PCM_24", "tpdf", 1,
        )
        assert [p.name for p in tmp_path.iterdir()] == ["out.flac"]


def test_config_defaults_to_a_multichannel_wav_delivery():
    config = UpmixConfig()
    assert config.output_type == "multichannel"
    assert config.output_codec == "wav_pcm"
