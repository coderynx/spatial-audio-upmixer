import numpy as np


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
