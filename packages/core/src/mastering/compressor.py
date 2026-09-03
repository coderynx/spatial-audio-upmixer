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
import upmixer_dsp

_log = logging.getLogger(__name__)

MANIFEST_FIELDS = {
        "profile":      ("config", "mastering_comp_profile"),
        "threshold_db": ("config", "mastering_comp_threshold_db"),
        "ratio":        ("config", "mastering_comp_ratio"),
        "attack_ms":    ("config", "mastering_comp_attack_ms"),
        "release_ms":   ("config", "mastering_comp_release_ms"),
        "knee_db":      ("config", "mastering_comp_knee_db"),
        "makeup_db":    ("config", "mastering_comp_makeup_db"),
        "sidechain_hpf_hz": ("config", "mastering_comp_sidechain_hpf_hz"),
}


COMP_PROFILES: dict[str, dict] = {
    "transparent": dict(
        threshold_db=-22.0,
        ratio=1.5,
        attack_ms=30.0,
        release_ms=300.0,
        knee_db=9.0,
        makeup_db=0.0,
        sidechain_hpf_hz=None,
    ),
    "glue": dict(
        threshold_db=-18.0,
        ratio=2.0,
        attack_ms=20.0,
        release_ms=200.0,
        knee_db=6.0,
        makeup_db=0.0,
        sidechain_hpf_hz=None,
    ),
    "warm": dict(
        threshold_db=-15.0,
        ratio=2.0,
        attack_ms=40.0,
        release_ms=400.0,
        knee_db=12.0,
        makeup_db=0.0,
        sidechain_hpf_hz=None,
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
        sidechain_hpf_hz: High-pass on the detector only, so low
                      frequencies stop driving gain reduction across the
                      whole bed.  ``None`` = full-band sidechain.
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
        sidechain_hpf_hz: float | None,
        sample_rate: int,
    ) -> None:
        if ratio < 1.0:
            raise ValueError(f"Compression ratio must be ≥ 1.0, got {ratio}")
        self._threshold = float(threshold_db)
        self._ratio = float(ratio)
        self._attack_ms = float(attack_ms)
        self._release_ms = float(release_ms)
        self._knee = float(max(0.0, knee_db))
        self._makeup = float(makeup_db)
        self._sidechain_hpf = (
            float(sidechain_hpf_hz) if sidechain_hpf_hz is not None else None
        )
        self._sr = int(sample_rate)
        self.gr_peak_db: float = 0.0
        self.gr_avg_db: float = 0.0


    def process(
        self,
        channels: dict[str, np.ndarray],
        lfe_key: str = "LFE",
        detector_channels: dict[str, np.ndarray] | None = None,
        detector_lfe_key: str = "LFE",
    ) -> dict[str, np.ndarray]:
        """Apply linked-sidechain bus compression to all channels except *lfe_key*.

        Sets :attr:`gr_peak_db` and :attr:`gr_avg_db` so a caller can report
        how hard the stage worked.

        Args:
            channels: Dict channel_name → 1-D float array.
            lfe_key:  Channel name to bypass (default ``"LFE"``).
            detector_channels: Optional rendered speaker programme that drives
                the gain applied to ``channels``.
            detector_lfe_key: LFE name in ``detector_channels``.

        Returns:
            New channel dict with gain reduction applied.  LFE returned
            unchanged.  All arrays have the same shape and dtype as inputs.
        """
        names = list(channels)
        if not [n for n in names if n != lfe_key] or self._ratio <= 1.0:
            return channels

        args = (
            self._sr,
            self._threshold,
            self._ratio,
            self._attack_ms,
            self._release_ms,
            self._knee,
            self._makeup,
            self._sidechain_hpf,
        )
        target = [np.ascontiguousarray(channels[name], dtype=np.float64) for name in names]
        if detector_channels is None:
            compressed, max_gr, avg_gr = upmixer_dsp.bus_compress(
                target, names.index(lfe_key) if lfe_key in channels else None, *args,
            )
        else:
            detector_names = list(detector_channels)
            compressed, max_gr, avg_gr = upmixer_dsp.bus_compress_linked(
                target,
                names.index(lfe_key) if lfe_key in channels else None,
                [
                    np.ascontiguousarray(detector_channels[name], dtype=np.float64)
                    for name in detector_names
                ],
                detector_names.index(detector_lfe_key)
                if detector_lfe_key in detector_channels else None,
                *args,
            )
        self.gr_peak_db = max_gr
        self.gr_avg_db = avg_gr

        _log.info(
            "  Bus compression: threshold=%.1f dBFS  ratio=%.1fx  "
            "GR peak=%.1f dB  GR avg=%.1f dB",
            self._threshold, self._ratio, max_gr, avg_gr,
        )

        return {
            name: channels[name] if name == lfe_key else arr.astype(channels[name].dtype)
            for name, arr in zip(names, compressed)
        }
