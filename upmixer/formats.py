from dataclasses import dataclass
from enum import Enum


class ChannelLabel(Enum):
    FL = "FL"
    FR = "FR"
    C = "C"
    LFE = "LFE"
    SL = "SL"
    SR = "SR"
    BL = "BL"
    BR = "BR"
    TFL = "TFL"
    TFR = "TFR"
    TBL = "TBL"
    TBR = "TBR"


_HEIGHT_LABELS = {ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR}


@dataclass(frozen=True)
class OutputFormat:
    name: str
    channels: tuple[ChannelLabel, ...]

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def has_height(self) -> bool:
        return any(ch in _HEIGHT_LABELS for ch in self.channels)

    @property
    def n_height_channels(self) -> int:
        return sum(1 for ch in self.channels if ch in _HEIGHT_LABELS)

    @property
    def has_back(self) -> bool:
        return ChannelLabel.BL in self.channels

    @property
    def bs2051_system(self) -> str:
        """ITU-R BS.2051-3 system code, or empty string if no direct mapping."""
        return {"5.1": "B", "5.1.2": "C", "5.1.4": "D", "7.1": "I", "7.1.4": "J"}.get(self.name, "")


SURROUND_51 = OutputFormat(
    name="5.1",
    channels=(
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.SL, ChannelLabel.SR,
    ),
)

SURROUND_71 = OutputFormat(
    name="7.1",
    channels=(
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.BL, ChannelLabel.BR,
        ChannelLabel.SL, ChannelLabel.SR,
    ),
)

SURROUND_512 = OutputFormat(
    name="5.1.2",
    channels=(
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.SL, ChannelLabel.SR,
        ChannelLabel.TFL, ChannelLabel.TFR,
    ),
)

SURROUND_514 = OutputFormat(
    name="5.1.4",
    channels=(
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.SL, ChannelLabel.SR,
        ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR,
    ),
)

SURROUND_712 = OutputFormat(
    name="7.1.2",
    channels=(
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.BL, ChannelLabel.BR,
        ChannelLabel.SL, ChannelLabel.SR,
        ChannelLabel.TFL, ChannelLabel.TFR,
    ),
)

SURROUND_714 = OutputFormat(
    name="7.1.4",
    channels=(
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.BL, ChannelLabel.BR,
        ChannelLabel.SL, ChannelLabel.SR,
        ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR,
    ),
)

FORMAT_MAP = {
    "5.1": SURROUND_51,
    "7.1": SURROUND_71,
    "5.1.2": SURROUND_512,
    "5.1.4": SURROUND_514,
    "7.1.2": SURROUND_712,
    "7.1.4": SURROUND_714,
}


@dataclass(frozen=True)
class InputFormat:
    name: str
    channels: tuple[ChannelLabel, ...]

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def has_lfe(self) -> bool:
        return ChannelLabel.LFE in self.channels

    @property
    def has_center(self) -> bool:
        return ChannelLabel.C in self.channels

    @property
    def has_surround(self) -> bool:
        return ChannelLabel.SL in self.channels

    @property
    def has_back(self) -> bool:
        return ChannelLabel.BL in self.channels

    @property
    def has_height(self) -> bool:
        return any(ch in _HEIGHT_LABELS for ch in self.channels)


MONO = InputFormat("mono", (ChannelLabel.C,))
STEREO = InputFormat("stereo", (ChannelLabel.FL, ChannelLabel.FR))
INPUT_5_0 = InputFormat(
    "5.0",
    (ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C, ChannelLabel.SL, ChannelLabel.SR),
)
INPUT_5_1 = InputFormat(
    "5.1",
    (
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.SL, ChannelLabel.SR,
    ),
)
INPUT_7_1 = InputFormat(
    "7.1",
    (
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.BL, ChannelLabel.BR,
        ChannelLabel.SL, ChannelLabel.SR,
    ),
)
INPUT_5_1_2 = InputFormat(
    "5.1.2",
    (
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.SL, ChannelLabel.SR,
        ChannelLabel.TFL, ChannelLabel.TFR,
    ),
)
INPUT_5_1_4 = InputFormat(
    "5.1.4",
    (
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.SL, ChannelLabel.SR,
        ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR,
    ),
)
INPUT_7_1_2 = InputFormat(
    "7.1.2",
    (
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.BL, ChannelLabel.BR,
        ChannelLabel.SL, ChannelLabel.SR,
        ChannelLabel.TFL, ChannelLabel.TFR,
    ),
)

INPUT_FORMAT_MAP: dict[str, InputFormat] = {
    "mono": MONO,
    "stereo": STEREO,
    "5.0": INPUT_5_0,
    "5.1": INPUT_5_1,
    "7.1": INPUT_7_1,
    "5.1.2": INPUT_5_1_2,
    "5.1.4": INPUT_5_1_4,
    "7.1.2": INPUT_7_1_2,
}

_INPUT_FORMAT_BY_CHANNELS: dict[int, InputFormat] = {
    1: MONO,
    2: STEREO,
    5: INPUT_5_0,
    6: INPUT_5_1,
    8: INPUT_7_1,
    10: INPUT_7_1_2,
}


def detect_input_format(n_channels: int) -> InputFormat:
    """Auto-detect input format from channel count.

    For ambiguous counts (e.g. 8 = 7.1 or 5.1.2) returns the most common default.
    Use --input-format to override.
    """
    if n_channels not in _INPUT_FORMAT_BY_CHANNELS:
        supported = sorted(_INPUT_FORMAT_BY_CHANNELS.keys())
        raise ValueError(
            f"Cannot auto-detect input format for {n_channels} channels. "
            f"Use --input-format to specify explicitly. "
            f"Supported channel counts: {supported}"
        )
    return _INPUT_FORMAT_BY_CHANNELS[n_channels]


def can_upmix(input_fmt: InputFormat, output_fmt: OutputFormat) -> bool:
    """True if output_fmt is a valid upmix target for input_fmt.

    Valid when all input channel labels exist in the output AND output
    has strictly more channels (no information loss, only addition).
    """
    input_labels = set(input_fmt.channels)
    output_labels = set(output_fmt.channels)
    return input_labels <= output_labels and output_fmt.n_channels > input_fmt.n_channels
