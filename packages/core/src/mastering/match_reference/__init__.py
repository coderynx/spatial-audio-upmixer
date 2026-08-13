"""Reference-EQ matching for the mastering bus (step 0).

See :class:`.processor.ReferenceMatchProcessor` for the class and
:mod:`.curve`/:mod:`.spectrum` for the underlying gated, BS.1770-weighted
spectral-matching algorithm. Constants here are contracted against
``docs/contracts/preview_export_parity.md`` §3 — this package computes the
FIR once and the browser preview convolves against it, rather than
re-running the algorithm per side.
"""
from __future__ import annotations

from .curve import build_curve_fir, compute_reference_curve
from .processor import ReferenceMatchProcessor

__all__ = ["ReferenceMatchProcessor", "build_curve_fir", "compute_reference_curve"]
