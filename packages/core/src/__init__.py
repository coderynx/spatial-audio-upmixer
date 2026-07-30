"""Stereo to multichannel audio upmixer."""

__version__ = "0.1.0"

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, INPUT_FORMAT_MAP
from upmixer.pipeline import StreamingProcessor, UpmixPipeline
from upmixer.result import UpmixResult

__all__ = [
    "UpmixConfig",
    "UpmixPipeline",
    "StreamingProcessor",
    "UpmixResult",
    "FORMAT_MAP",
    "INPUT_FORMAT_MAP",
]

