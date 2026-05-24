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
from scipy.signal import butter, sosfilt

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

_STEREO_PAIRS: list[tuple[str, str]] = [
    ("FL", "FR"),
    ("SL", "SR"),
    ("BL", "BR"),
    ("TFL", "TFR"),
    ("TBL", "TBR"),
]

_SUB_CUTOFF_HZ: float = 80.0
_MID_CUTOFF_HZ: float = 200.0

_EXCITE_BLEND: float = 0.15
_EXCITE_DRIVE: float = 3.0


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

        nyq = sample_rate / 2.0

        self._sos_sub_lp = butter(2, _SUB_CUTOFF_HZ / nyq, btype="low", output="sos")

        self._sos_mid_lp = butter(2, _MID_CUTOFF_HZ / nyq, btype="low", output="sos")
        self._sos_mid_hp = butter(2, _SUB_CUTOFF_HZ / nyq, btype="high", output="sos")

        if self._mono_hz is not None:
            mono_norm = float(np.clip(self._mono_hz / nyq, 1e-4, 0.999))
            self._sos_mono_lp = butter(2, mono_norm, btype="low",  output="sos")
            self._sos_mono_hp = butter(2, mono_norm, btype="high", output="sos")
        else:
            self._sos_mono_lp = None
            self._sos_mono_hp = None


    def _apply_band_gain(
        self, ch: np.ndarray,
        band_lin: float,
        sos_lp: np.ndarray,
        sos_hp: np.ndarray | None = None,
    ) -> np.ndarray:
        """Boost/cut a frequency band in *ch*.

        If *sos_hp* is ``None``, the band is everything below the LP cutoff
        (sub-bass).  Otherwise the band is the bandpass (LP then HP, mid-bass).
        """
        band = sosfilt(sos_lp, ch)
        if sos_hp is not None:
            band = sosfilt(sos_hp, band)
        return (ch - band) + band * band_lin


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
        sub_lin = 10.0 ** (self._sub_db / 20.0)
        mid_lin = 10.0 ** (self._mid_db / 20.0)

        out: dict[str, np.ndarray] = dict(channels)

        if self._sub_db != 0.0 or self._mid_db != 0.0:
            for name, ch in channels.items():
                if name == lfe_key:
                    continue
                arr = ch.astype(np.float64)
                if self._sub_db != 0.0:
                    arr = self._apply_band_gain(arr, sub_lin, self._sos_sub_lp)
                if self._mid_db != 0.0:
                    arr = self._apply_band_gain(
                        arr, mid_lin, self._sos_mid_lp, self._sos_mid_hp
                    )
                out[name] = arr
            _log.debug(
                "  BassController: sub=%+.1f dB  mid=%+.1f dB",
                self._sub_db, self._mid_db,
            )

        if self._sos_mono_lp is not None:
            for l_key, r_key in _STEREO_PAIRS:
                if l_key not in out or r_key not in out:
                    continue
                l = out[l_key].astype(np.float64)
                r = out[r_key].astype(np.float64)

                l_low = sosfilt(self._sos_mono_lp, l)
                r_low = sosfilt(self._sos_mono_lp, r)
                mono_bass = (l_low + r_low) * 0.5

                out[l_key] = mono_bass + sosfilt(self._sos_mono_hp, l)
                out[r_key] = mono_bass + sosfilt(self._sos_mono_hp, r)

            _log.debug(
                "  BassController: bass-mono at %.0f Hz", self._mono_hz
            )

        if self._excite:
            for name, ch in out.items():
                if name == lfe_key:
                    continue
                arr = ch.astype(np.float64)
                sub = sosfilt(self._sos_sub_lp, arr)
                harmonics = np.tanh(sub * _EXCITE_DRIVE) * _EXCITE_BLEND
                out[name] = arr + harmonics
            _log.debug("  BassController: harmonic exciter enabled")

        if self._lfe_db != 0.0 and lfe_key in out:
            lfe_lin = 10.0 ** (self._lfe_db / 20.0)
            out[lfe_key] = out[lfe_key].astype(np.float64) * lfe_lin
            _log.debug(
                "  BassController: LFE %+.1f dB", self._lfe_db
            )

        return out
