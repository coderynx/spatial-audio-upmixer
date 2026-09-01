"""Frequency-response measurement for the stem-routing send chains."""
from __future__ import annotations

import math

import numpy as np
import pytest

from test_mix_measurement import (
    _NOTCH_HI_HZ,
    _NOTCH_LO_HZ,
    _SR,
    _band_means_db,
    _dip_count,
    _freqs,
    _impulse,
    _print_table,
    _router,
    _tf_db,
    _worst_notch_db,
)
from upmixer.utils import HEIGHT_VELVET_SEED, SURROUND_VELVET_SEED, velvet_send

pytestmark = pytest.mark.perf


def test_send_frequency_response() -> None:
    """Measurement 1 — surround/height send chains and derived-channel chains."""
    router = _router("7.1.4", "balanced")
    impulse = _impulse()

    chains = {
        "surround L (HP250 → velvet L)": (
            router._surround_send(impulse),
            "left",
            SURROUND_VELVET_SEED,
        ),
        "surround R (HP250 → velvet R)": (
            router._surround_send(impulse),
            "right",
            SURROUND_VELVET_SEED,
        ),
        "height L (elev EQ → velvet L)": (
            router._height_send(impulse),
            "left",
            HEIGHT_VELVET_SEED,
        ),
        "height R (elev EQ → velvet R)": (
            router._height_send(impulse),
            "right",
            HEIGHT_VELVET_SEED,
        ),
    }

    eq_only = {name: _tf_db(eq) for name, (eq, _, _) in chains.items()}
    full = {
        name: _tf_db(velvet_send(eq, _SR, side, seed))
        for name, (eq, side, seed) in chains.items()
    }
    decorrelator = {name: full[name] - eq_only[name] for name in chains}

    names = tuple(chains)
    band_levels = {name: dict(_band_means_db(full[name])) for name in names}
    _print_table(
        "1a. Send chain response, third-octave band level (dB, full chain)",
        ("Band Hz",) + names,
        [
            (f"{center:.0f}",)
            + tuple(f"{band_levels[name][center]:+.2f}" for name in names)
            for center in sorted(band_levels[names[0]])
        ],
    )

    rows = []
    for name, (eq, side, seed) in chains.items():
        depth, freq = _worst_notch_db(decorrelator[name])
        eq_depth, eq_freq = _worst_notch_db(full[name])
        band = [level for _, level in _band_means_db(decorrelator[name])]
        gain = 10.0 * math.log10(
            float(np.dot(velvet_send(eq, _SR, side, seed), velvet_send(eq, _SR, side, seed)))
            / float(np.dot(eq, eq))
        )
        rows.append(
            (
                name,
                f"{depth:.2f} @ {freq:.0f} Hz",
                _dip_count(decorrelator[name]),
                f"{max(band) - min(band):.2f}",
                f"{gain:+.2f}",
                f"{eq_depth:.2f} @ {eq_freq:.0f} Hz",
            )
        )
    _print_table(
        "1b. Decorrelator response, isolated from the send EQ",
        (
            "Chain",
            "worst notch (dB)",
            "dips < −10 dB",
            "band ripple p-p (dB)",
            "broadband gain (dB)",
            "worst notch, full chain",
        ),
        rows,
    )

    pairs = {
        "StemRouter surround (velvet L/R)": (
            velvet_send(router._surround_send(impulse), _SR, "left", SURROUND_VELVET_SEED),
            velvet_send(router._surround_send(impulse), _SR, "right", SURROUND_VELVET_SEED),
        ),
        "StemRouter height (velvet L/R)": (
            velvet_send(router._height_send(impulse), _SR, "left", HEIGHT_VELVET_SEED),
            velvet_send(router._height_send(impulse), _SR, "right", HEIGHT_VELVET_SEED),
        ),
    }
    fine = (_freqs() >= _NOTCH_LO_HZ) & (_freqs() <= _NOTCH_HI_HZ)
    rows = []
    for name, (left, right) in pairs.items():
        summed = left + right
        loss = 10.0 * math.log10(
            float(np.dot(summed, summed))
            / (float(np.dot(left, left)) + float(np.dot(right, right)))
        )
        tf = _tf_db(summed)
        band = [level for _, level in _band_means_db(tf)]
        depth, freq = _worst_notch_db(tf)
        rows.append(
            (
                name,
                f"{loss:+.2f}",
                f"{depth - max(band):.2f} @ {freq:.0f} Hz",
                f"{float(np.std(tf[fine])):.2f}",
            )
        )
    _print_table(
        "1d. Mono fold-down of each decorrelated pair (L+R)",
        (
            "Pair",
            "sum vs power sum (dB)",
            "worst notch rel. (dB)",
            "per-bin ripple σ (dB)",
        ),
        rows,
    )

    for name in chains:
        dips = _dip_count(decorrelator[name])
        assert dips < 120, f"{name} has {dips} dips, comb-like"
    for name, (left, right) in pairs.items():
        summed = left + right
        loss = 10.0 * math.log10(
            float(np.dot(summed, summed))
            / (float(np.dot(left, left)) + float(np.dot(right, right)))
        )
        assert abs(loss) < 0.5, f"{name} fold-down lost {loss:.2f} dB"
