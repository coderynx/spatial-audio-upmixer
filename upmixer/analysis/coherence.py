from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig


@dataclass
class CoherenceState:
    """Persistent state for frame-by-frame coherence estimation."""

    Phi_LR: np.ndarray  # Smoothed cross-spectrum, shape (n_freq,)
    Phi_LL: np.ndarray  # Smoothed left auto-spectrum
    Phi_RR: np.ndarray  # Smoothed right auto-spectrum
    initialized: bool = False


class CoherenceEstimator:
    """Estimates inter-channel coherence per time-frequency bin."""

    def __init__(self, config: UpmixConfig):
        self._alpha = config.coherence_smoothing
        self._epsilon = config.epsilon

    def create_state(self, n_freq_bins: int) -> CoherenceState:
        """Create fresh coherence state for streaming."""
        return CoherenceState(
            Phi_LR=np.zeros(n_freq_bins, dtype=np.complex128),
            Phi_LL=np.zeros(n_freq_bins, dtype=np.float64),
            Phi_RR=np.zeros(n_freq_bins, dtype=np.float64),
            initialized=False,
        )

    def estimate_frame(
        self,
        X_L_frame: np.ndarray,
        X_R_frame: np.ndarray,
        state: CoherenceState,
    ) -> np.ndarray:
        """Estimate coherence for a single frame, updating state in place.

        Returns gamma of shape (n_freq,), values in [0, 1].
        """
        alpha = self._alpha
        eps = self._epsilon

        cross_LR = X_L_frame * np.conj(X_R_frame)
        power_L = np.abs(X_L_frame) ** 2
        power_R = np.abs(X_R_frame) ** 2

        if not state.initialized:
            state.Phi_LR = cross_LR.copy()
            state.Phi_LL = power_L.copy()
            state.Phi_RR = power_R.copy()
            state.initialized = True
        else:
            state.Phi_LR = alpha * state.Phi_LR + (1.0 - alpha) * cross_LR
            state.Phi_LL = alpha * state.Phi_LL + (1.0 - alpha) * power_L
            state.Phi_RR = alpha * state.Phi_RR + (1.0 - alpha) * power_R

        gamma = np.abs(state.Phi_LR) ** 2 / (state.Phi_LL * state.Phi_RR + eps)
        return np.clip(gamma, 0.0, 1.0)

    def estimate(self, X_L: np.ndarray, X_R: np.ndarray) -> np.ndarray:
        """Batch estimate (for backward compatibility and tests).

        Returns gamma of shape (n_freq, n_frames).
        """
        n_freq, n_frames = X_L.shape
        state = self.create_state(n_freq)
        gamma = np.zeros((n_freq, n_frames), dtype=np.float64)

        for n in range(n_frames):
            gamma[:, n] = self.estimate_frame(X_L[:, n], X_R[:, n], state)

        return gamma
