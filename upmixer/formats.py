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
    # Height channels (Dolby Atmos / DTS:X)
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


# Standard WAVEX channel orderings
SURROUND_51 = OutputFormat(
    name="5.1",
    channels=(
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.SL, ChannelLabel.SR,
    ),
)

# WAVEX 7.1 order: FL FR C LFE BL BR SL SR
SURROUND_71 = OutputFormat(
    name="7.1",
    channels=(
        ChannelLabel.FL, ChannelLabel.FR, ChannelLabel.C,
        ChannelLabel.LFE, ChannelLabel.BL, ChannelLabel.BR,
        ChannelLabel.SL, ChannelLabel.SR,
    ),
)

# Dolby Atmos / DTS:X height formats
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
