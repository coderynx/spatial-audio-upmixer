from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig


@dataclass
class HarmonicityState:
    """Persistent state for per-frame harmonicity estimation."""

    smoothed_mask: np.ndarray
    initialized: bool = False


class HarmonicityEstimator:
    """HPSS-inspired per-bin harmonicity mask via local spectral floor estimation.

    For each STFT frame, estimates how much each frequency bin EXCEEDS the local
    spectral noise floor (estimated by a sliding median in the frequency axis).

    High value at bin f: bin is a spectral peak above the floor → tonal content
        (instruments, vocals, sustained synths). These belong in FL/FR.
    Low value at bin f: bin is at the floor → diffuse/residual content
        (room reverb, broadband noise). These can route to surrounds.

    A temporal EMA suppresses frame-to-frame flicker caused by short transients
    momentarily creating apparent spectral peaks.
    """

    def __init__(self, config: UpmixConfig, n_freq: int):
        self._k = config.harmonic_median_half_width
        assert self._k < n_freq // 2, (
            f"harmonic_median_half_width={self._k} too large for n_freq={n_freq}"
        )
        self._alpha = config.harmonic_smoothing_alpha
        self._eps = config.epsilon
        self._n_freq = n_freq

    def create_state(self) -> HarmonicityState:
        return HarmonicityState(smoothed_mask=np.zeros(self._n_freq), initialized=False)

    def estimate_frame(
        self,
        X_L_frame: np.ndarray,
        X_R_frame: np.ndarray,
        state: HarmonicityState,
    ) -> np.ndarray:
        """Return harmonic mask of shape (n_freq,), values in [0, 1].

        High = tonal spectral peak. Low = at spectral noise floor.
        """
        mag = (np.abs(X_L_frame) + np.abs(X_R_frame)) * 0.5
        floor = self._local_median(mag, self._k)
        raw = np.clip((mag - floor) / (floor + self._eps), 0.0, 1.0)

        if not state.initialized:
            state.smoothed_mask = raw.copy()
            state.initialized = True
        else:
            state.smoothed_mask = (
                self._alpha * state.smoothed_mask + (1.0 - self._alpha) * raw
            )

        return state.smoothed_mask

    @staticmethod
    def _local_median(mag: np.ndarray, k: int) -> np.ndarray:
        """Sliding (2k+1)-bin median using reflect padding and stride tricks."""
        n = len(mag)
        padded = np.pad(mag, k, mode="reflect")
        shape = (n, 2 * k + 1)
        strides = (padded.strides[0], padded.strides[0])
        windows = np.lib.stride_tricks.as_strided(padded, shape=shape, strides=strides)
        return np.median(windows, axis=1)

    def reset(self, state: HarmonicityState) -> None:
        state.smoothed_mask[:] = 0.0
        state.initialized = False
