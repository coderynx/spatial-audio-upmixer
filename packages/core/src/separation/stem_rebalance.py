"""Per-stem gain rebalancer for the stem-separation mixing pipeline.

Applies per-stem level adjustments to the separated stem dict *before*
spatial routing — analogous to iZotope Ozone 12 Master Rebalance / RX 12
Music Rebalance, but operating directly on already-separated stems rather
than performing a blind source separation.

Each stem receives an independent dB gain.  Zone-tagged keys
(``"Vocals@front"``) are matched by the canonical stem name
(``"Vocals"``), so a single entry applies across all zones.

Gain application
----------------
* Gain converted to linear scale.
* A 10 ms linear ramp-up at the start avoids clicks on high boosts.
* If the boost exceeds +3 dB, a tanh soft-clipper is applied after gain
  to catch transient overshoots: ``tanh(ch / threshold) * threshold``
  with ``threshold = 0.95``.

Predefined profiles
-------------------
vocal-forward   Vocals +2.5 dB, Drums -1 dB, Other -0.5 dB
instrumental    Vocals -3 dB, Drums +1 dB, Bass +1 dB
bass-heavy      Bass +2 dB, Drums +1 dB, Vocals -0.5 dB
balanced        All stems 0 dB (identity — useful as manifest placeholder)
"""
from __future__ import annotations

import logging

import numpy as np

_log = logging.getLogger("upmixer")


REBALANCE_PROFILES: dict[str, dict[str, float]] = {
    "vocal-forward": {
        "Vocals":      +2.5,
        "Lead Vocals": +2.5,
        "Drums":       -1.0,
        "Other":       -0.5,
    },
    "instrumental": {
        "Vocals":      -3.0,
        "Lead Vocals": -3.0,
        "Drums":       +1.0,
        "Bass":        +1.0,
    },
    "bass-heavy": {
        "Bass":   +2.0,
        "Drums":  +1.0,
        "Vocals": -0.5,
    },
    "balanced": {},
}

REBALANCE_PROFILE_NAMES: tuple[str, ...] = tuple(sorted(REBALANCE_PROFILES.keys()))

_SOFT_CLIP_THRESHOLD: float = 0.95
_BOOST_DB_CLIP_TRIGGER: float = 3.0


class StemRebalancer:
    """Apply per-stem dB gain adjustments before spatial routing.

    Args:
        gains:       Mapping of canonical stem name → gain_dB.
                     Zone suffixes (``@front``) in the stem dict are stripped
                     before lookup.  Stems absent from this dict are unchanged.
        sample_rate: Audio sample rate in Hz (used to compute the 10 ms ramp).
    """

    def __init__(self, gains: dict[str, float], sample_rate: int) -> None:
        self._gains = gains
        self._ramp_samples = max(1, int(round(0.010 * sample_rate)))


    @staticmethod
    def _canonical(key: str) -> str:
        """Strip ``@zone`` suffix, returning the base stem name."""
        return key.split("@")[0]

    def _gain_db_for(self, key: str) -> float:
        return self._gains.get(self._canonical(key), 0.0)


    def process(
        self,
        all_stems: dict[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        """Apply gain adjustments to every stem.

        Args:
            all_stems: Dict stem_key → ``(n_samples, 2)`` float64 array.
                       The dict is **not** modified in-place; a shallow copy
                       with replaced arrays is returned.

        Returns:
            New dict with the same keys; untouched stems share the original
            array objects (no copy).
        """
        out: dict[str, np.ndarray] = {}
        for key, audio in all_stems.items():
            gain_db = self._gain_db_for(key)
            if gain_db == 0.0:
                out[key] = audio
                continue

            gain_lin = 10.0 ** (gain_db / 20.0)
            arr = audio.astype(np.float64)

            ramp_len = min(self._ramp_samples, arr.shape[0])
            ramp = np.linspace(1.0, gain_lin, ramp_len)
            arr[:ramp_len] *= ramp[:, np.newaxis] if arr.ndim == 2 else ramp
            arr[ramp_len:] *= gain_lin

            if gain_db > _BOOST_DB_CLIP_TRIGGER:
                thr = _SOFT_CLIP_THRESHOLD
                arr = np.tanh(arr / thr) * thr

            out[key] = arr
            _log.debug("  StemRebalancer: %s  %+.1f dB", key, gain_db)

        return out
