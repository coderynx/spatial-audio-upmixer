"""MDAP panning of a stem placement into speaker gains.

Multiple-Direction Amplitude Panning: a placement becomes a set of virtual
sources spanning its width, each panned by VBAP onto one speaker simplex; the
gain vectors sum and the result is normalized to constant power. VBAP holds a
point placement on the simplex that contains it instead of leaking into every
speaker within a falloff radius, and the virtual-source set — rather than how
densely the layout happens to be populated around the target — is what decides
the image's width.

The panner itself lives in ``packages/dsp`` (``spatial::panner``) so the export
pipeline and the web preview run the same implementation; this module is the
Python face of it, turning channel labels into the ordered name lists the core
expects and the gain lists back into channel maps. See that module for the
simplex construction, the spread model, the coplanar-wall averaging, the
out-of-hull fallbacks, and the determinism contract.
"""
from __future__ import annotations

import upmixer_dsp

from upmixer.formats import ChannelLabel

VIRTUAL_SOURCE_STEP_DEG: float = upmixer_dsp.VIRTUAL_SOURCE_STEP_DEG
"""Angular spacing of the virtual sources spanning a placement's width."""

def direction(azimuth_deg: float, elevation_deg: float) -> tuple[float, float, float]:
    """Unit vector in ``binaural.geometry``'s convention: +azimuth = left."""
    return upmixer_dsp.direction(azimuth_deg, elevation_deg)


def panning_gains(
    azimuth_deg: float,
    elevation_deg: float,
    width_deg: float,
    object_size: float,
    labels: tuple[ChannelLabel, ...],
) -> dict[str, float]:
    """Constant-power speaker gains for one placement, keyed by channel name."""
    if not labels:
        return {}
    names = [label.value for label in labels]
    gains = upmixer_dsp.panning_gains(
        azimuth_deg, elevation_deg, width_deg, object_size, names
    )
    if not any(gains):
        return {}
    return dict(zip(names, gains))
