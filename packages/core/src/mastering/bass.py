"""Multichannel bass control for the mastering bus.

Analogue to iZotope Ozone 12 Low End Focus — shaped for spatial audio where
low-end energy arrives from both the bed speakers *and* the LFE / subwoofer
channel.

Four independent processing stages (all optional):

1. **Low-end EQ** — Butterworth bandpass/lowpass gain on sub-bass (<80 Hz)
   and mid-bass (80–200 Hz) across every non-LFE channel.
2. **Bass mono-maker** — sums the low-frequency component of L/R stereo pairs
   below a configurable cutoff to mono, tightening the stereo bass image.
   Applies to pairs: FL/FR, SL/SR, BL/BR, TFL/TFR, TBL/TBR.
3. **Harmonic exciter** — blends a small amount of tanh-shaped odd harmonics
   derived from the sub-bass band back into each non-LFE channel, adding
   perceived depth on small speakers.
4. **LFE gain trim** — simple linear gain on the LFE channel only (dB).

Built-in profiles
-----------------
boost      Sub +2 dB, mid +1 dB, LFE +1.5 dB
cut        Sub −2.5 dB, mid −1.5 dB, LFE −1 dB
mono       Bass mono-maker at 100 Hz, no EQ
enhance    Sub +1.5 dB, mid +0.5 dB, mono at 80 Hz, harmonic exciter, LFE +1 dB

All profiles can be overridden by individual config params
(``mastering_bass_sub_gain_db``, etc.); ``None`` = use profile default.
"""
from __future__ import annotations

import logging

import numpy as np
import upmixer_dsp

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "bass": {
        "profile":        ("config", "mastering_bass_profile"),
        "sub_gain_db":    ("config", "mastering_bass_sub_gain_db"),
        "mid_gain_db":    ("config", "mastering_bass_mid_gain_db"),
        "mono_cutoff_hz": ("config", "mastering_bass_mono_cutoff_hz"),
        "excite":         ("config", "mastering_bass_excite"),
        "lfe_gain_db":    ("config", "mastering_bass_lfe_gain_db"),
    },
})
del _rbk


BASS_PROFILES: dict[str, dict] = {
    "boost": dict(
        sub_gain_db=2.0, mid_gain_db=1.0,
        mono_cutoff_hz=None, excite=False, lfe_gain_db=1.5,
    ),
    "cut": dict(
        sub_gain_db=-2.5, mid_gain_db=-1.5,
        mono_cutoff_hz=None, excite=False, lfe_gain_db=-1.0,
    ),
    "mono": dict(
        sub_gain_db=0.0, mid_gain_db=0.0,
        mono_cutoff_hz=100.0, excite=False, lfe_gain_db=0.0,
    ),
    "enhance": dict(
        sub_gain_db=1.5, mid_gain_db=0.5,
        mono_cutoff_hz=80.0, excite=True, lfe_gain_db=1.0,
    ),
}

BASS_PROFILE_NAMES: tuple[str, ...] = tuple(sorted(BASS_PROFILES.keys()))

# Public (not module-private) so the web engine-constants endpoint (apps/api
# system slice) can serve the exact values — see
# docs/contracts/preview_export_parity.md.
STEREO_PAIRS: list[tuple[str, str]] = [
    ("FL", "FR"),
    ("SL", "SR"),
    ("BL", "BR"),
    ("TFL", "TFR"),
    ("TBL", "TBR"),
]

SUB_CUTOFF_HZ: float = 80.0
MID_CUTOFF_HZ: float = 200.0

EXCITE_BLEND: float = 0.15
EXCITE_DRIVE: float = 3.0


class BassController:
    """Multichannel bass control for the mastering bus.

    Designed for spatial audio — each processing stage is LFE-aware.
    LFE is treated separately from the main bed at all times.

    Args:
        sub_gain_db:      Gain applied to the <80 Hz band of all non-LFE
                          channels (dB).  0.0 = bypass.
        mid_gain_db:      Gain applied to the 80–200 Hz band of all non-LFE
                          channels (dB).  0.0 = bypass.
        mono_cutoff_hz:   Cut-off frequency for the bass mono-maker (Hz).
                          ``None`` = mono-maker disabled.
        excite:           Enable harmonic exciter on the sub-bass band.
        lfe_gain_db:      dB gain trim applied to the LFE channel only.
                          0.0 = no change.
        sample_rate:      Audio sample rate in Hz.
    """

    def __init__(
        self,
        sub_gain_db: float,
        mid_gain_db: float,
        mono_cutoff_hz: float | None,
        excite: bool,
        lfe_gain_db: float,
        sample_rate: int,
    ) -> None:
        self._sub_db = float(sub_gain_db)
        self._mid_db = float(mid_gain_db)
        self._mono_hz = float(mono_cutoff_hz) if mono_cutoff_hz is not None else None
        self._excite = bool(excite)
        self._lfe_db = float(lfe_gain_db)
        self._sr = sample_rate


    def process(
        self,
        channels: dict[str, np.ndarray],
        lfe_key: str = "LFE",
    ) -> dict[str, np.ndarray]:
        """Apply bass control to the multichannel bed.

        Args:
            channels: Dict channel_name → 1-D float64 array.
            lfe_key:  Name of the LFE channel (default ``"LFE"``).

        Returns:
            Modified channel dict (new arrays for processed channels;
            unmodified channels share the original array objects).
        """
        names = list(channels)
        index = {name: i for i, name in enumerate(names)}
        pairs = [
            (index[left], index[right])
            for left, right in STEREO_PAIRS
            if left in index and right in index
        ]

        shaped = upmixer_dsp.bass_control(
            [np.ascontiguousarray(channels[name], dtype=np.float64) for name in names],
            index.get(lfe_key),
            pairs,
            self._sr,
            self._sub_db,
            self._mid_db,
            self._mono_hz,
            self._excite,
            self._lfe_db,
            SUB_CUTOFF_HZ,
            MID_CUTOFF_HZ,
            EXCITE_BLEND,
            EXCITE_DRIVE,
        )

        if self._sub_db != 0.0 or self._mid_db != 0.0:
            _log.debug(
                "  BassController: sub=%+.1f dB  mid=%+.1f dB",
                self._sub_db, self._mid_db,
            )
        if self._mono_hz is not None:
            _log.debug("  BassController: bass-mono at %.0f Hz", self._mono_hz)
        if self._excite:
            _log.debug("  BassController: harmonic exciter enabled")
        if self._lfe_db != 0.0:
            _log.debug("  BassController: LFE %+.1f dB", self._lfe_db)

        return dict(zip(names, shaped))
