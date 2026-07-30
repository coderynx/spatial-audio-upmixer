"""Backward-compatibility shim — re-exports from upmixer.mastering.compressor.

Prefer importing from ``upmixer.mastering.compressor`` directly.
"""
from upmixer.mastering.compressor import (  # noqa: F401
    COMP_PROFILES,
    COMP_PROFILE_NAMES,
    BusCompressor,
)
