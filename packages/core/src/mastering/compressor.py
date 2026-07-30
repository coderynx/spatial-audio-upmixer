"""Bus / glue compressor for the multichannel mastering bus.

Applies linked-sidechain RMS compression across all output channels (the LFE
channel is always bypassed).  The compressor is cosmetic — it adds cohesion
and glue to the mix without being a loudness-normalizer.  Loudness
normalization is handled separately by :class:`~upmixer.mastering.MasteringChain`.

Algorithm
---------
1.  **Linked sidechain**: sum of squared samples across all non-LFE channels
    per sample, converted to per-channel RMS.  A single gain signal is derived
    and applied uniformly — this preserves spatial imaging across the surround
    field.
2.  **Envelope follower** (max-envelope trick, fully vectorized):

    * Fast IIR (attack coefficient) tracks rising edges.
    * Slow IIR (release coefficient) tracks falling edges.
    * Per-sample maximum of the two gives fast-attack / slow-release behavior
      without a Python sample loop — both passes use ``scipy.signal.lfilter``
      (C-accelerated).

    Reference: Giannoulis, Massberg, Reiss (2012). "Digital Dynamic Range
    Compressor Design." JAES Vol. 60, No. 6.

3.  **Soft-knee gain computer**: parabolic blend over the knee-width range.
4.  **Makeup gain**: added after compression in dB.

Built-in profiles
-----------------
transparent   Very gentle glue.  Threshold −22 dBFS, ratio 1.5:1, attack 30 ms,
              release 300 ms, knee 9 dB.  Barely perceptible; improves density.
glue          SSL-style bus glue.  Threshold −18 dBFS, ratio 2:1, attack 20 ms,
              release 200 ms, knee 6 dB.  Classic "makes it stick together".
warm          Opto-style character.  Threshold −15 dBFS, ratio 2:1, attack 40 ms,
              release 400 ms, knee 12 dB.  Smooth, warm, musical sustain.
"""
from __future__ import annotations

import logging

import numpy as np
from scipy.signal import lfilter

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "compressor": {
        "profile":      ("config", "mastering_comp_profile"),
        "threshold_db": ("config", "mastering_comp_threshold_db"),
        "ratio":        ("config", "mastering_comp_ratio"),
        "attack_ms":    ("config", "mastering_comp_attack_ms"),
        "release_ms":   ("config", "mastering_comp_release_ms"),
        "knee_db":      ("config", "mastering_comp_knee_db"),
        "makeup_db":    ("config", "mastering_comp_makeup_db"),
    },
})
del _rbk


COMP_PROFILES: dict[str, dict[str, float]] = {
    "transparent": dict(
        threshold_db=-22.0,
        ratio=1.5,
        attack_ms=30.0,
        release_ms=300.0,
        knee_db=9.0,
        makeup_db=0.0,
    ),
    "glue": dict(
        threshold_db=-18.0,
        ratio=2.0,
        attack_ms=20.0,
        release_ms=200.0,
        knee_db=6.0,
        makeup_db=0.0,
    ),
    "warm": dict(
        threshold_db=-15.0,
        ratio=2.0,
        attack_ms=40.0,
        release_ms=400.0,
        knee_db=12.0,
        makeup_db=0.0,
    ),
}

COMP_PROFILE_NAMES: tuple[str, ...] = tuple(sorted(COMP_PROFILES.keys()))



class BusCompressor:
    """Linked-sidechain RMS bus compressor for spatial audio beds.

    Args:
        threshold_db: Compression threshold in dBFS.
        ratio:        Compression ratio ≥ 1.0 (e.g. ``2.0`` for 2:1).
        attack_ms:    Attack time constant in milliseconds.
        release_ms:   Release time constant in milliseconds.
        knee_db:      Soft-knee width in dB (``0.0`` = hard knee).
        makeup_db:    Makeup gain in dB applied after compression.
        sample_rate:  Audio sample rate in Hz.

    Raises:
        ValueError: if *ratio* < 1.0.
    """

    def __init__(
        self,
        threshold_db: float,
        ratio: float,
        attack_ms: float,
        release_ms: float,
        knee_db: float,
        makeup_db: float,
        sample_rate: int,
    ) -> None:
        if ratio < 1.0:
            raise ValueError(f"Compression ratio must be ≥ 1.0, got {ratio}")
        self._threshold = float(threshold_db)
        self._ratio = float(ratio)
        self._knee = float(max(0.0, knee_db))
        self._makeup = float(makeup_db)
        self._sr = int(sample_rate)

        dt = 1.0 / sample_rate
        self._alpha_a = float(1.0 - np.exp(-dt / (max(attack_ms, 0.01) / 1000.0)))
        self._alpha_r = float(1.0 - np.exp(-dt / (max(release_ms, 0.01) / 1000.0)))


    def _gain_computer(self, env_db: np.ndarray) -> np.ndarray:
        """Soft-knee gain computer (vectorized).

        Returns gain reduction in dB (always ≤ 0 before makeup gain).
        """
        T = self._threshold
        R = self._ratio
        W = self._knee

        if W > 0.0:
            knee_lo = T - W / 2.0
            knee_hi = T + W / 2.0
            below = env_db <= knee_lo
            above = env_db >= knee_hi

            output_db = np.where(
                below,
                env_db,
                np.where(
                    above,
                    T + (env_db - T) / R,
                    env_db + ((1.0 / R - 1.0) * (env_db - knee_lo) ** 2)
                    / (2.0 * W),
                ),
            )
        else:
            output_db = np.where(
                env_db <= T,
                env_db,
                T + (env_db - T) / R,
            )

        return output_db - env_db


    def process(
        self,
        channels: dict[str, np.ndarray],
        lfe_key: str = "LFE",
    ) -> dict[str, np.ndarray]:
        """Apply linked-sidechain bus compression to all channels except *lfe_key*.

        Args:
            channels: Dict channel_name → 1-D float array.
            lfe_key:  Channel name to bypass (default ``"LFE"``).

        Returns:
            New channel dict with gain reduction applied.  LFE returned
            unchanged.  All arrays have the same shape and dtype as inputs.
        """
        bed_chs = {k: v for k, v in channels.items() if k != lfe_key}
        if not bed_chs or self._ratio <= 1.0:
            return channels

        n = max(len(v) for v in bed_chs.values())
        n_ch = len(bed_chs)

        x_sq = np.zeros(n, dtype=np.float64)
        for ch in bed_chs.values():
            ch64 = ch.astype(np.float64)
            length = min(len(ch64), n)
            x_sq[:length] += ch64[:length] ** 2
        x_rms = np.sqrt(x_sq / n_ch + 1e-20)

        b_a = np.array([self._alpha_a], dtype=np.float64)
        a_a = np.array([1.0, -(1.0 - self._alpha_a)], dtype=np.float64)
        b_r = np.array([self._alpha_r], dtype=np.float64)
        a_r = np.array([1.0, -(1.0 - self._alpha_r)], dtype=np.float64)

        level_fast = lfilter(b_a, a_a, x_rms)
        level_slow = lfilter(b_r, a_r, x_rms)
        envelope = np.maximum(level_fast, level_slow)

        envelope_db = 20.0 * np.log10(np.maximum(envelope, 1e-20))
        gain_db = self._gain_computer(envelope_db) + self._makeup
        gain_linear = np.power(10.0, gain_db / 20.0)

        max_gr = float(np.max(np.abs(gain_db - self._makeup)))
        avg_gr = float(np.mean(np.abs(gain_db - self._makeup)))
        _log.info(
            "  Bus compression: threshold=%.1f dBFS  ratio=%.1fx  "
            "GR peak=%.1f dB  GR avg=%.1f dB",
            self._threshold, self._ratio, max_gr, avg_gr,
        )

        out = dict(channels)
        for name, ch in bed_chs.items():
            ch64 = ch.astype(np.float64)
            gl = gain_linear[: len(ch64)]
            out[name] = (ch64 * gl).astype(ch.dtype)

        return out
