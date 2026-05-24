import math

import numpy as np
from scipy.signal import butter, sosfilt

_ITU_C_COEFF: float = 1.0 / math.sqrt(2)


def db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def linear_to_db(linear: float, floor_db: float = -120.0) -> float:
    if linear <= 0:
        return floor_db
    return max(20.0 * np.log10(linear), floor_db)


def rms(signal: np.ndarray) -> float:
    return float(np.sqrt(np.mean(signal**2)))


def soft_limit(signal: np.ndarray, threshold: float = 0.95) -> np.ndarray:
    """Soft limiter using tanh saturation above threshold."""
    out = signal.copy()
    mask = np.abs(signal) > threshold
    if not np.any(mask):
        return out
    over = np.abs(signal[mask]) - threshold
    compressed = threshold + (1.0 - threshold) * np.tanh(over / (1.0 - threshold))
    out[mask] = np.sign(signal[mask]) * compressed
    return out


def elevation_eq(
    signal: np.ndarray,
    sr: int,
    low_rolloff_hz: float = 150.0,
    low_rolloff_gain: float = 0.15,
    high_shelf_hz: float = 3000.0,
    high_shelf_gain: float = 1.5,
) -> np.ndarray:
    """Elevation EQ: sub-bass rolloff + HF presence lift.

    Mirrors the HRTF elevation cue: attenuate below low_rolloff_hz,
    boost above high_shelf_hz. Used for height channel signals.

    Moved from upmixer.upmix.multichannel so the stem pipeline can
    reuse it without a circular import.
    """
    nyq = sr / 2.0
    sos_lp = butter(1, low_rolloff_hz / nyq, btype="low", output="sos")
    low_comp = sosfilt(sos_lp, signal)
    bass_shaped = signal - low_comp * (1.0 - low_rolloff_gain)
    sos_hp = butter(2, high_shelf_hz / nyq, btype="high", output="sos")
    hp = sosfilt(sos_hp, bass_shaped)
    return bass_shaped + hp * (high_shelf_gain - 1.0)


def haas_decorrelate(signal: np.ndarray, delay_samples: int) -> np.ndarray:
    """Return a copy of signal delayed by delay_samples (zero-padded at head).

    Used for Haas-effect L/R decorrelation on surround and height channel
    pairs. The left channel is undelayed; the right channel receives this
    delay. Varying delays per channel pair (13–23 ms) prevents comb filtering
    while creating perceived spatial width.
    """
    if delay_samples <= 0:
        return signal.copy()
    out = np.empty_like(signal)
    out[:delay_samples] = 0.0
    out[delay_samples:] = signal[:-delay_samples]
    return out


def diffuse_send(
    signal: np.ndarray,
    sr: int,
    delay_ms: float = 35.0,
    blend: float = 0.55,
) -> np.ndarray:
    """Early-reflection diffusion for surround/height sends.

    Blends the original signal with a delayed copy to simulate room
    diffusion without convolving a full IR. Applied post-separation so
    separation artifacts remain in their source channel and do not multiply.

    Args:
        signal:   1D audio signal.
        sr:       Sample rate.
        delay_ms: Early reflection delay in ms (default 35 ms).
        blend:    Wet mix level (1 - blend = dry).  Range [0, 1].
    """
    delay_n = int(sr * delay_ms / 1000.0)
    delayed = haas_decorrelate(signal, delay_n)
    return signal * (1.0 - blend) + delayed * blend


def preview_slice(
    audio: np.ndarray,
    sr: int,
    duration_s: float = 30.0,
    start_s: float | None = None,
) -> tuple[np.ndarray, float, float]:
    """Slice audio to a preview window.

    Args:
        audio:      2D array (n_samples, n_channels).
        sr:         Sample rate.
        duration_s: Desired preview length in seconds.
        start_s:    Explicit start time. None = auto-center (middle of track).

    Returns:
        (sliced_audio, actual_start_s, actual_end_s)
    """
    n_total = audio.shape[0]
    clip_len = min(int(duration_s * sr), n_total)

    if start_s is None:
        center = n_total // 2
        start = max(0, center - clip_len // 2)
    else:
        start = max(0, min(int(start_s * sr), n_total - clip_len))

    end = start + clip_len
    return audio[start:end], start / sr, end / sr


def itu_downmix_stereo(
    channels: dict[str, np.ndarray],
    surround_coeff: float = _ITU_C_COEFF,
) -> tuple[np.ndarray, np.ndarray]:
    """ITU-R BS.775-4 Annex 4 Table 2 — multichannel to 2/0 stereo downmix.

    L' = FL + (1/√2)·C + k_s·SL  [+ k_s·(1/√2)·BL if present]
    R' = FR + (1/√2)·C + k_s·SR  [+ k_s·(1/√2)·BR if present]

    LFE and height channels excluded per standard.
    Back surrounds fold into side surrounds attenuated by (1/√2) so total
    surround energy matches a 3/2 source.

    Args:
        channels:       Multichannel dict — any subset of FL, FR, C, SL, SR, BL, BR.
        surround_coeff: k_s per Annex 8.  Valid values: 0.7071 (default), 0.5, 0.0.

    Returns:
        (L_out, R_out) 1D float64 arrays.
    """
    _skip = {"LFE", "TFL", "TFR", "TBL", "TBR"}
    n = next((len(v) for k, v in channels.items() if k not in _skip), 0)
    if n == 0:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.float64)

    def _ch(key: str) -> np.ndarray:
        return channels.get(key, np.zeros(n, dtype=np.float64))

    SL = _ch("SL") + (_ITU_C_COEFF * _ch("BL") if "BL" in channels else 0.0)
    SR = _ch("SR") + (_ITU_C_COEFF * _ch("BR") if "BR" in channels else 0.0)

    L_out = _ch("FL") + _ITU_C_COEFF * _ch("C") + surround_coeff * SL
    R_out = _ch("FR") + _ITU_C_COEFF * _ch("C") + surround_coeff * SR

    return L_out.astype(np.float64), R_out.astype(np.float64)


def itu_downmix_mono(
    channels: dict[str, np.ndarray],
    surround_coeff: float = 0.5,
) -> np.ndarray:
    """ITU-R BS.775-4 Annex 4 Table 2 — multichannel to 1/0 mono downmix.

    M = (1/√2)·(FL + FR) + C + k_s·(SL + SR)

    LFE and height channels excluded per standard.
    Default surround_coeff = 0.5 per Table 2 mono row.

    Args:
        channels:       Multichannel dict — any subset of FL, FR, C, SL, SR, BL, BR.
        surround_coeff: Surround mixing coefficient (default: 0.5 per Table 2 mono).

    Returns:
        M 1D float64 array.
    """
    _skip = {"LFE", "TFL", "TFR", "TBL", "TBR"}
    n = next((len(v) for k, v in channels.items() if k not in _skip), 0)
    if n == 0:
        return np.zeros(0, dtype=np.float64)

    def _ch(key: str) -> np.ndarray:
        return channels.get(key, np.zeros(n, dtype=np.float64))

    SL = _ch("SL") + (_ITU_C_COEFF * _ch("BL") if "BL" in channels else 0.0)
    SR = _ch("SR") + (_ITU_C_COEFF * _ch("BR") if "BR" in channels else 0.0)

    return (_ITU_C_COEFF * (_ch("FL") + _ch("FR")) + _ch("C") + surround_coeff * (SL + SR)).astype(np.float64)


def normalize_energy(
    channels: dict[str, np.ndarray],
    original_left: np.ndarray,
    original_right: np.ndarray,
) -> dict[str, np.ndarray]:
    """Scale output channels so total energy matches the original stereo signal."""
    original_energy = np.sum(original_left**2) + np.sum(original_right**2)
    output_energy = sum(np.sum(ch**2) for ch in channels.values())

    if output_energy < 1e-20:
        return channels

    scale = np.sqrt(original_energy / output_energy)
    return {name: ch * scale for name, ch in channels.items()}
