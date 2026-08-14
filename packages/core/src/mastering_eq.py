"""Backward-compatibility shim — re-exports from upmixer.mastering.eq.

Prefer importing from ``upmixer.mastering.eq`` directly.
"""
from upmixer.mastering.eq import (  # noqa: F401
    EQ_PROFILES,
    EQ_PROFILE_NAMES,
    SpectralShaper,
    _build_fir,
    _build_fir_from_breakpoints,
)
