"""Post-decode voicing chain: crossfeed, shelving/presence EQ, M/S widen.

Applies :class:`~upmixer.binaural.profiles.VoicingParams`. Bypassed entirely
for Flat and Studio (all-zero params); only Listening uses this to apply a
flattering "hi-fi enhance" (crossfeed for externalization, a Harman-style
bass/air/presence tilt, and a wide soundstage) on top of the profile's
reference cinema room decode. Filter topology mirrors
``upmixer/utils.py`` ``elevation_eq`` (subtract/add shelf trick) so the web
preview's Web Audio ``BiquadFilterNode`` chain can match parameter-for-
parameter — see ``docs/standards/spatial_audio_engine.md`` §5.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt

from upmixer.binaural.profiles import VoicingParams


def _shelf(signal: np.ndarray, sr: int, freq_hz: float, gain_db: float, kind: str) -> np.ndarray:
    if gain_db == 0.0:
        return signal
    nyq = sr / 2.0
    sos = butter(2, freq_hz / nyq, btype=kind, output="sos")
    band = sosfilt(sos, signal)
    gain = 10.0 ** (gain_db / 20.0) - 1.0
    return signal + band * gain


def _presence(signal: np.ndarray, sr: int, freq_hz: float, gain_db: float, q: float) -> np.ndarray:
    if gain_db == 0.0:
        return signal
    nyq = sr / 2.0
    bandwidth = max(freq_hz / q, 1.0)
    low = max(freq_hz - bandwidth / 2.0, 1.0) / nyq
    high = min(freq_hz + bandwidth / 2.0, nyq - 1.0) / nyq
    sos = butter(2, [low, high], btype="bandpass", output="sos")
    band = sosfilt(sos, signal)
    gain = 10.0 ** (gain_db / 20.0) - 1.0
    return signal + band * gain


def _crossfeed(left: np.ndarray, right: np.ndarray, sr: int, amount: float, cutoff_hz: float) -> tuple[np.ndarray, np.ndarray]:
    if amount <= 0.0:
        return left, right
    nyq = sr / 2.0
    sos = butter(1, cutoff_hz / nyq, btype="low", output="sos")
    bleed_l = sosfilt(sos, left)
    bleed_r = sosfilt(sos, right)
    out_l = left * (1.0 - amount) + bleed_r * amount
    out_r = right * (1.0 - amount) + bleed_l * amount
    return out_l, out_r


def _widen(left: np.ndarray, right: np.ndarray, amount: float) -> tuple[np.ndarray, np.ndarray]:
    if amount == 0.0:
        return left, right
    mid = (left + right) * 0.5
    side = (left - right) * 0.5 * (1.0 + amount)
    return mid + side, mid - side


def apply_voicing(
    left: np.ndarray, right: np.ndarray, sample_rate: int, params: VoicingParams
) -> tuple[np.ndarray, np.ndarray]:
    """Apply the full voicing chain in signal-graph order: crossfeed → EQ → widen."""
    left, right = _crossfeed(left, right, sample_rate, params.crossfeed_amount, params.crossfeed_cutoff_hz)
    left = _shelf(left, sample_rate, params.bass_shelf_hz, params.bass_shelf_gain_db, "low")
    right = _shelf(right, sample_rate, params.bass_shelf_hz, params.bass_shelf_gain_db, "low")
    left = _shelf(left, sample_rate, params.air_shelf_hz, params.air_shelf_gain_db, "high")
    right = _shelf(right, sample_rate, params.air_shelf_hz, params.air_shelf_gain_db, "high")
    left = _presence(left, sample_rate, params.presence_hz, params.presence_gain_db, params.presence_q)
    right = _presence(right, sample_rate, params.presence_hz, params.presence_gain_db, params.presence_q)
    left, right = _widen(left, right, params.stereo_widen)
    return left, right
