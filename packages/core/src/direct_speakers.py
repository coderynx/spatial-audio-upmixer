"""ITU-R BS.2051/BS.2094 DirectSpeakers metadata for supported beds."""

from __future__ import annotations

from dataclasses import dataclass

from upmixer.formats import ChannelLabel, OutputFormat


@dataclass(frozen=True)
class DirectSpeaker:
    """One bed channel's standard label and nominal spatial position."""

    channel: ChannelLabel
    speaker_label: str
    azimuth_deg: float | None
    elevation_deg: float | None
    cartesian_position: tuple[float, float, float]


def _speaker(
    channel: ChannelLabel,
    speaker_label: str,
    azimuth_deg: float | None,
    elevation_deg: float | None,
    cartesian_position: tuple[float, float, float],
) -> DirectSpeaker:
    return DirectSpeaker(
        channel,
        speaker_label,
        azimuth_deg,
        elevation_deg,
        cartesian_position,
    )


_FRONT = (
    _speaker(ChannelLabel.FL, "M+030", 30.0, 0.0, (-1.0, 1.0, 0.0)),
    _speaker(ChannelLabel.FR, "M-030", -30.0, 0.0, (1.0, 1.0, 0.0)),
)
_CENTER = _speaker(ChannelLabel.C, "M+000", 0.0, 0.0, (0.0, 1.0, 0.0))
_LFE = _speaker(ChannelLabel.LFE, "LFE1", None, None, (-1.0, 1.0, -1.0))
_SURROUND = (
    _speaker(ChannelLabel.SL, "M+110", 110.0, 0.0, (-1.0, -1.0, 0.0)),
    _speaker(ChannelLabel.SR, "M-110", -110.0, 0.0, (1.0, -1.0, 0.0)),
)
_SIDE = (
    _speaker(ChannelLabel.SL, "M+090", 90.0, 0.0, (-1.0, 0.0, 0.0)),
    _speaker(ChannelLabel.SR, "M-090", -90.0, 0.0, (1.0, 0.0, 0.0)),
)
_REAR = (
    _speaker(ChannelLabel.BL, "M+135", 135.0, 0.0, (-1.0, -1.0, 0.0)),
    _speaker(ChannelLabel.BR, "M-135", -135.0, 0.0, (1.0, -1.0, 0.0)),
)
_TOP_FRONT_30 = (
    _speaker(ChannelLabel.TFL, "U+030", 30.0, 30.0, (-1.0, 1.0, 1.0)),
    _speaker(ChannelLabel.TFR, "U-030", -30.0, 30.0, (1.0, 1.0, 1.0)),
)
_TOP_FRONT_45 = (
    _speaker(ChannelLabel.TFL, "U+045", 45.0, 30.0, (-1.0, 1.0, 1.0)),
    _speaker(ChannelLabel.TFR, "U-045", -45.0, 30.0, (1.0, 1.0, 1.0)),
)
_TOP_SIDE = (
    _speaker(ChannelLabel.TFL, "U+090", 90.0, 30.0, (-1.0, 0.0, 1.0)),
    _speaker(ChannelLabel.TFR, "U-090", -90.0, 30.0, (1.0, 0.0, 1.0)),
)
_TOP_REAR_110 = (
    _speaker(ChannelLabel.TBL, "U+110", 110.0, 30.0, (-1.0, -1.0, 1.0)),
    _speaker(ChannelLabel.TBR, "U-110", -110.0, 30.0, (1.0, -1.0, 1.0)),
)
_TOP_REAR_135 = (
    _speaker(ChannelLabel.TBL, "U+135", 135.0, 30.0, (-1.0, -1.0, 1.0)),
    _speaker(ChannelLabel.TBR, "U-135", -135.0, 30.0, (1.0, -1.0, 1.0)),
)


DIRECT_SPEAKER_LAYOUTS: dict[str, tuple[DirectSpeaker, ...]] = {
    "stereo": _FRONT,
    "5.1": _FRONT + (_CENTER, _LFE) + _SURROUND,
    "7.1": _FRONT + (_CENTER, _LFE) + _SIDE + _REAR,
    "5.1.2": _FRONT + (_CENTER, _LFE) + _SURROUND + _TOP_FRONT_30,
    "5.1.4": (
        _FRONT + (_CENTER, _LFE) + _SURROUND + _TOP_FRONT_30 + _TOP_REAR_110
    ),
    "7.1.2": _FRONT + (_CENTER, _LFE) + _SIDE + _REAR + _TOP_SIDE,
    "7.1.4": (
        _FRONT + (_CENTER, _LFE) + _REAR + _SIDE + _TOP_FRONT_45 + _TOP_REAR_135
    ),
}


def direct_speakers(fmt: OutputFormat) -> tuple[DirectSpeaker, ...]:
    """Return DirectSpeakers metadata in the format's channel order."""
    speakers = DIRECT_SPEAKER_LAYOUTS[fmt.name]
    if tuple(speaker.channel for speaker in speakers) != fmt.channels:
        raise RuntimeError(f"DirectSpeakers metadata order differs from {fmt.name}")
    return speakers
