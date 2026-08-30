"""Delivery containers and codecs, and the libsndfile limits that bound them.

``UpmixConfig.output_type`` says *what* is rendered (a multichannel bed, a
binaural or transaural stereo pair, an ADM object master); the codecs here say
how that render is encoded. The two are orthogonal apart from ADM-BWF, whose
RIFF chunk writer only produces WAV.
"""
from __future__ import annotations

from dataclasses import dataclass

from upmixer.formats import FORMAT_MAP

WAV_SUBTYPES: tuple[str, ...] = ("PCM_16", "PCM_24", "PCM_32", "FLOAT")


@dataclass(frozen=True)
class Codec:
    """One delivery encoding, with the libsndfile limits it imposes."""

    name: str
    label: str
    container: str
    extension: str
    media_type: str
    subtypes: tuple[str, ...]
    max_channels: int | None = None
    sample_rates: tuple[int, ...] | None = None

    @property
    def fixed_subtype(self) -> str | None:
        """The only subtype, when the codec offers no bit-depth choice."""
        return self.subtypes[0] if len(self.subtypes) == 1 else None


CODECS: dict[str, Codec] = {
    "wav_pcm": Codec(
        name="wav_pcm",
        label="WAV (PCM)",
        container="WAV",
        extension=".wav",
        media_type="audio/wav",
        subtypes=WAV_SUBTYPES,
    ),
    "flac": Codec(
        name="flac",
        label="FLAC",
        container="FLAC",
        extension=".flac",
        media_type="audio/flac",
        subtypes=("PCM_16", "PCM_24"),
        max_channels=8,
    ),
    "ogg_vorbis": Codec(
        name="ogg_vorbis",
        label="OGG (Vorbis)",
        container="OGG",
        extension=".ogg",
        media_type="audio/ogg",
        subtypes=("VORBIS",),
    ),
    "ogg_opus": Codec(
        name="ogg_opus",
        label="OGG (Opus)",
        container="OGG",
        extension=".opus",
        media_type="audio/ogg",
        subtypes=("OPUS",),
        sample_rates=(8_000, 12_000, 16_000, 24_000, 48_000),
    ),
}

DEFAULT_CODEC = "wav_pcm"

LOSSY_CODECS: frozenset[str] = frozenset({"ogg_vorbis", "ogg_opus"})
"""Codecs whose written frame count need not match what was handed to them."""


def get_codec(codec: str) -> Codec:
    """Look up a codec by name, raising on unknown values."""
    try:
        return CODECS[codec]
    except KeyError:
        raise ValueError(
            f"Unknown output codec '{codec}'. Choose one of {sorted(CODECS)}"
        ) from None


def codec_extension(codec: str) -> str:
    """Return the file extension a codec's deliveries must use."""
    return get_codec(codec).extension


def resolve_subtype(codec: str, output_subtype: str) -> str:
    """Return the libsndfile subtype to write with.

    Lossy containers carry no bit depth, so ``output_subtype`` is ignored for
    them rather than forcing manifests to spell out ``VORBIS``/``OPUS``.
    """
    return get_codec(codec).fixed_subtype or output_subtype


def delivered_channels(output_format: str, output_type: str) -> int:
    """Channel count of the file that actually lands on disk.

    Binaural and transaural collapse their bed to a stereo pair, so a 7.1.4
    bed delivers 2 channels — which is what a codec's channel cap applies to.
    """
    if output_type in ("binaural", "transaural"):
        return 2
    return FORMAT_MAP[output_format].n_channels


def validate_codec(
    output_format: str,
    output_type: str,
    output_codec: str,
    output_subtype: str,
    sample_rate: int | None = None,
) -> None:
    """Raise ValueError when a codec cannot carry a delivery.

    ``sample_rate`` is the resolved delivery rate when it is already known;
    pass ``None`` to skip the rate check (it follows the source otherwise).
    """
    codec = get_codec(output_codec)
    if output_type == "adm-bwf" and codec.name != DEFAULT_CODEC:
        raise ValueError(
            f"adm-bwf output is a WAV container only; codec '{codec.name}' "
            f"is not available for it"
        )
    if output_type == "adm-bwf":
        if output_subtype != "PCM_24":
            raise ValueError("Dolby ADM-BWF requires PCM_24")
        if sample_rate is not None and sample_rate not in (48_000, 96_000):
            raise ValueError("Dolby ADM-BWF requires 48 kHz or 96 kHz")
    channels = delivered_channels(output_format, output_type)
    if codec.max_channels is not None and channels > codec.max_channels:
        raise ValueError(
            f"{codec.label} supports at most {codec.max_channels} channels, but "
            f"'{output_format}' {output_type} output delivers {channels}"
        )
    if codec.fixed_subtype is None and output_subtype not in codec.subtypes:
        raise ValueError(
            f"{codec.label} does not support subtype '{output_subtype}'; "
            f"choose one of {list(codec.subtypes)}"
        )
    if sample_rate is not None and codec.sample_rates is not None:
        if sample_rate not in codec.sample_rates:
            raise ValueError(
                f"{codec.label} supports only {list(codec.sample_rates)} Hz, "
                f"got {sample_rate}"
            )
