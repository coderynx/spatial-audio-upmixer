import math

import numpy as np
import upmixer_dsp

ITU_CENTER_COEFF: float = 1.0 / math.sqrt(2)

# Decorrelator tap-set seeds, one per zone class, taken from the shared DSP
# core so the preview builds the same filters — see
# docs/contracts/preview_export_parity.md.
SURROUND_VELVET_SEED: int = upmixer_dsp.VELVET_SEED
HEIGHT_VELVET_SEED: int = upmixer_dsp.VELVET_SEED_HEIGHT


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
    return upmixer_dsp.soft_limit(
        np.ascontiguousarray(signal, dtype=np.float64), threshold
    )


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
    return upmixer_dsp.elevation_eq(
        np.ascontiguousarray(signal, dtype=np.float64),
        sr,
        low_rolloff_hz,
        low_rolloff_gain,
        high_shelf_hz,
        high_shelf_gain,
    )


def velvet_send(
    signal: np.ndarray,
    sr: int,
    side: str,
    seed: int = upmixer_dsp.VELVET_SEED,
) -> np.ndarray:
    """Decorrelate one side of a channel pair for a surround or height send.

    Applies one side of the velvet-noise decorrelator pair: a sparse aperiodic
    FIR that diffuses without the comb a delayed copy produces, and whose two
    sides share no tap, so a BS.775 fold-down of the pair cannot cancel. Both
    sides of a pair must use the same seed for that to hold; zone classes use
    different seeds so a stem's surround and height sends stay decorrelated
    from each other too.

    Applied post-separation so separation artifacts remain in their source
    channel and do not multiply.

    Args:
        signal: 1D audio signal.
        sr:     Sample rate.
        side:   "left" or "right".
        seed:   Tap-set seed — VELVET_SEED (surround) or VELVET_SEED_HEIGHT.
    """
    return upmixer_dsp.velvet_pair_send(
        np.ascontiguousarray(signal, dtype=np.float64),
        sr,
        side,
        upmixer_dsp.VELVET_LENGTH_MS,
        upmixer_dsp.VELVET_TAPS_PER_SIDE,
        seed,
        upmixer_dsp.VELVET_WET,
    )


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


_DOWNMIX_SOURCES = (
    "FL", "FR", "C", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR",
)


def _downmix_sources(
    channels: dict[str, np.ndarray],
) -> tuple[list[str], list[np.ndarray]]:
    """Select the channels a stereo/mono downmix draws from, in a fixed order.

    LFE is excluded; heights fold in by project convention — see
    docs/standards/spatial_layouts_bs775_bs2051.md.
    """
    present = [name for name in _DOWNMIX_SOURCES if name in channels]
    return present, [
        np.ascontiguousarray(channels[name], dtype=np.float64) for name in present
    ]


def itu_downmix_stereo(
    channels: dict[str, np.ndarray],
    surround_coeff: float = ITU_CENTER_COEFF,
    height_coeff: float = ITU_CENTER_COEFF,
) -> tuple[np.ndarray, np.ndarray]:
    """ITU-R BS.775-4 Annex 4 Table 2 — multichannel to 2/0 stereo downmix.

    L' = FL + (1/√2)·C + k_s·SL  [+ k_s·(1/√2)·BL] [+ k_h·(TFL + k_s·TBL)]
    R' = FR + (1/√2)·C + k_s·SR  [+ k_s·(1/√2)·BR] [+ k_h·(TFR + k_s·TBR)]

    LFE is excluded per standard. BS.775 predates height channels; folding
    them in at k_h is a project convention — see
    docs/standards/spatial_layouts_bs775_bs2051.md.
    Back surrounds fold into side surrounds attenuated by (1/√2) so total
    surround energy matches a 3/2 source.

    Args:
        channels:       Multichannel dict — any subset of FL, FR, C, SL, SR, BL,
                        BR, TFL, TFR, TBL, TBR.
        surround_coeff: k_s per Annex 8.  Valid values: 0.7071 (default), 0.5, 0.0.
        height_coeff:   k_h, the height fold level. 0.0 drops heights.

    Returns:
        (L_out, R_out) 1D float64 arrays.
    """
    names, audio = _downmix_sources(channels)
    if not names:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.float64)
    return upmixer_dsp.itu_downmix_stereo(names, audio, surround_coeff, height_coeff)


def itu_downmix_mono(
    channels: dict[str, np.ndarray],
    surround_coeff: float = 0.5,
    height_coeff: float = ITU_CENTER_COEFF,
) -> np.ndarray:
    """ITU-R BS.775-4 Annex 4 Table 2 — multichannel to 1/0 mono downmix.

    M = (1/√2)·(FL + FR + k_h·(TFL + TFR)) + C
        + k_s·(SL + SR + (1/√2)·(BL + BR) + k_h·(TBL + TBR))

    LFE is excluded per standard; heights fold through the same front/surround
    routes the stereo downmix uses, by project convention — see
    docs/standards/spatial_layouts_bs775_bs2051.md.
    Default surround_coeff = 0.5 per Table 2 mono row.

    Args:
        channels:       Multichannel dict — any subset of FL, FR, C, SL, SR, BL,
                        BR, TFL, TFR, TBL, TBR.
        surround_coeff: Surround mixing coefficient (default: 0.5 per Table 2 mono).
        height_coeff:   k_h, the height fold level. 0.0 drops heights.

    Returns:
        M 1D float64 array.
    """
    names, audio = _downmix_sources(channels)
    if not names:
        return np.zeros(0, dtype=np.float64)
    return upmixer_dsp.itu_downmix_mono(names, audio, surround_coeff, height_coeff)
