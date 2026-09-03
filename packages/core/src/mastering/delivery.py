"""Named delivery targets: integrated loudness, true-peak ceiling, tolerance.

A preset supplies the pair of numbers a delivery specification asks for;
``config.loudness_target_lkfs`` and ``config.loudness_max_tp`` override it
field by field, the same precedence the compressor and bass profiles use.
With no preset named, the two fields fall back to the Dolby Atmos Music
values they have always defaulted to.

Every number here is sourced in ``docs/standards/loudness_dsp_bs1770.md``
§"Delivery targets"; ``tolerance_lu`` is ``None`` where the specification
publishes a target without one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

_log = logging.getLogger(__name__)

DEFAULT_TARGET_LKFS = -18.0
DEFAULT_MAX_TP_DBTP = -1.0

DELIVERY_TARGETS: dict[str, dict[str, float | None]] = {
    "atmos-music":      {"target_lkfs": -18.0, "max_tp_dbtp": -1.0, "tolerance_lu": None},
    "netflix-atmos":    {"target_lkfs": -27.0, "max_tp_dbtp": -2.0, "tolerance_lu": 2.0},
    "ebu-r128":         {"target_lkfs": -23.0, "max_tp_dbtp": -1.0, "tolerance_lu": 0.5},
    "atsc-a85":         {"target_lkfs": -24.0, "max_tp_dbtp": -2.0, "tolerance_lu": 2.0},
    "streaming-stereo": {"target_lkfs": -14.0, "max_tp_dbtp": -1.0, "tolerance_lu": None},
    "apple-music":      {"target_lkfs": -16.0, "max_tp_dbtp": -1.0, "tolerance_lu": None},
}


@dataclass(frozen=True)
class DeliveryTarget:
    """The loudness contract one mastering pass is held to."""

    preset: str | None
    target_lkfs: float
    max_tp_dbtp: float
    tolerance_lu: float | None


def resolve_delivery_target(config) -> DeliveryTarget:
    """Resolve ``config``'s preset and overrides into one delivery target.

    Args:
        config: An :class:`~upmixer.config.UpmixConfig`.

    Returns:
        The resolved :class:`DeliveryTarget`.  An unknown preset name falls
        back to the bare defaults, so a typo cannot silently deliver to
        someone else's specification.
    """
    name = config.loudness_target_preset
    preset = DELIVERY_TARGETS.get(name or "", {})
    if name and not preset:
        _log.warning(
            "Unknown delivery target '%s' — using defaults. Valid: %s",
            name,
            sorted(DELIVERY_TARGETS),
        )
        name = None
    target = config.loudness_target_lkfs
    ceiling = config.loudness_max_tp
    return DeliveryTarget(
        preset=name,
        target_lkfs=(
            target
            if target is not None
            else preset.get("target_lkfs", DEFAULT_TARGET_LKFS)
        ),
        max_tp_dbtp=(
            ceiling
            if ceiling is not None
            else preset.get("max_tp_dbtp", DEFAULT_MAX_TP_DBTP)
        ),
        tolerance_lu=preset.get("tolerance_lu"),
    )
