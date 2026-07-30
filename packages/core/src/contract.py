"""Preview/export parity constants — the live cross-check's Python half.

See ``docs/contracts/preview_export_parity.md`` for what this covers and
why. This module assembles the constants that document declares must match
between the core engine and the web preview, read straight from their real
source modules (never re-typed literals). ``tests/test_contract_parity.py``
compares this module's :func:`canonical_constants` directly against
``web/src/lib/contract.ts``'s ``canonicalConstants()``, dumped ahead of time
to ``tests/fixtures/contract/web_constants.json`` via
``npm run constants:dump`` (``web/scripts/dump-constants.mjs``) — no pinned
hash to regenerate, the test just diffs the two live structures.

Changing any constant this module reads changes what that comparison checks
against. Per the contract's change protocol (``docs/contracts/README.md``),
such a change must be mirrored on both sides and the web fixture
re-dumped — see that document before editing any value referenced here.
"""
from __future__ import annotations

import json

from upmixer.binaural.renderer import BINAURAL_LOUDNESS_MAX_GAIN_DB
from upmixer.config import UpmixConfig
from upmixer.crosstalk.renderer import CROSSTALK_LOUDNESS_MAX_GAIN_DB
from upmixer.mastering.bass import (
    BASS_PROFILES,
    EXCITE_BLEND,
    EXCITE_DRIVE,
    MID_CUTOFF_HZ,
    SUB_CUTOFF_HZ,
)
from upmixer.mastering.compressor import COMP_PROFILES
from upmixer.separation.stem_router import (
    HEIGHT_HAAS_DELAY_MS_L,
    HEIGHT_HAAS_DELAY_MS_R,
    SURROUND_HAAS_DELAY_MS_L,
    SURROUND_HAAS_DELAY_MS_R,
)
from upmixer.utils import DIFFUSE_SEND_BLEND, ITU_CENTER_COEFF


def canonical_constants() -> dict:
    """Return the Tier-1/Tier-2 constants ``preview_export_parity.md`` pins.

    Every value is read from its real source module — ``UpmixConfig``
    defaults and the mastering/routing profile tables — never re-typed as a
    literal here. That means this function's output changes automatically
    whenever any contracted constant changes, with no edit to this file
    required.

    Channel-layout/format constants (``upmixer.formats``) are deliberately
    excluded: the web has no independent static copy of them to check
    against (it fetches ``layout_channels`` from ``GET
    /api/v1/configuration`` at runtime) — that pairing is already covered by
    ``upmixer.manifest.manifest_parameter_schema()``
    (``docs/project_manifest_parity.md``), a different parity mechanism than
    this one.
    """
    cfg = UpmixConfig()
    return {
        "channel_group_gains": {
            "center": cfg.center_gain,
            "surround": cfg.surround_gain,
            "back": cfg.back_gain,
            "height": cfg.height_gain,
        },
        "lfe_gain": cfg.lfe_gain,
        "lfe_lowpass_hz": cfg.lfe_cutoff_hz,
        "surround_bass_cutoff_hz": cfg.surround_bass_cutoff_hz,
        "height_low_rolloff_hz": cfg.height_low_rolloff_hz,
        "height_low_rolloff_gain": cfg.height_low_rolloff_gain,
        "height_crossover_hz": cfg.height_crossover_hz,
        "height_high_shelf_gain": cfg.height_high_shelf_gain,
        "soft_limit_threshold": cfg.peak_limit_threshold,
        "limiter_lookahead_ms": cfg.limiter_lookahead_ms,
        "limiter_release_ms": cfg.limiter_release_ms,
        "loudness_max_gain_db": cfg.loudness_max_gain_db,
        "surround_downmix_coeff": cfg.surround_downmix_coeff,
        "itu_center_coeff": ITU_CENTER_COEFF,
        "diffuse_send_blend": DIFFUSE_SEND_BLEND,
        "surround_haas_ms": {"left": SURROUND_HAAS_DELAY_MS_L, "right": SURROUND_HAAS_DELAY_MS_R},
        "height_haas_ms": {"left": HEIGHT_HAAS_DELAY_MS_L, "right": HEIGHT_HAAS_DELAY_MS_R},
        "comp_profiles": COMP_PROFILES,
        "bass_profiles": BASS_PROFILES,
        "bass_sub_cutoff_hz": SUB_CUTOFF_HZ,
        "bass_mid_cutoff_hz": MID_CUTOFF_HZ,
        "bass_excite_blend": EXCITE_BLEND,
        "bass_excite_drive": EXCITE_DRIVE,
        "binaural_loudness_max_gain_db": BINAURAL_LOUDNESS_MAX_GAIN_DB,
        "crosstalk_loudness_max_gain_db": CROSSTALK_LOUDNESS_MAX_GAIN_DB,
    }


def _canonical_number(value: int | float) -> str:
    """Format a number so Python and TypeScript hash the same payload.

    Native ``json.dumps``/``JSON.stringify`` float formatting differs across
    the two languages (e.g. Python prints ``30.0``, JS prints ``30``), which
    would make the two sides' signatures diverge over formatting, not real
    value drift. Both this function and its TS mirror
    (``web/src/lib/contract.ts::canonicalNumber``) instead: print
    integer-valued numbers with no decimal point, and otherwise round to 12
    fractional digits and strip trailing zeros (keeping at least one). 12
    digits is well inside a float64's ~15-17 significant-digit precision, so
    both languages' correctly-rounded fixed-decimal conversions agree
    byte-for-byte on the same underlying double.
    """
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    text = f"{value:.12f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _canonical_value(value: object) -> str:
    """Recursively render *value* as deterministic, cross-language JSON text."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _canonical_number(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        items = ",".join(
            f"{json.dumps(str(k))}:{_canonical_value(v)}"
            for k, v in sorted(value.items(), key=lambda kv: kv[0])
        )
        return "{" + items + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_value(v) for v in value) + "]"
    raise TypeError(f"Unsupported type for canonical serialization: {type(value)!r}")
