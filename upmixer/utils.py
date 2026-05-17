import numpy as np
from scipy.signal import butter, sosfilt


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
    # tanh compression above threshold
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
