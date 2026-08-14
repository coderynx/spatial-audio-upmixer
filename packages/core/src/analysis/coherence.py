from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig


@dataclass
class CoherenceState:
    """Persistent state for frame-by-frame coherence estimation."""

    Phi_LR: np.ndarray
    Phi_LL: np.ndarray
    Phi_RR: np.ndarray
    initialized: bool = False


class CoherenceEstimator:
    """Estimates inter-channel coherence per time-frequency bin.

    Frame path uses phase agreement to select cross-spectrum attack/release;
    auto spectra use coherence_smoothing. Batch repeats the frame path.
    """

    def __init__(self, config: UpmixConfig):
        self._alpha = config.coherence_smoothing
        self._alpha_attack = config.coherence_attack_alpha
        self._alpha_release = config.coherence_release_alpha
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
            prev_gamma = np.abs(state.Phi_LR) ** 2 / (
                state.Phi_LL * state.Phi_RR + eps
            )
            # A single-bin instantaneous coherence is always one for non-zero
            # spectra.  Compare its phase with the accumulated cross spectrum
            # instead: stable direct sound aligns, diffuse content does not.
            agreement = np.real(cross_LR * np.conj(state.Phi_LR)) / (
                np.abs(cross_LR) * np.abs(state.Phi_LR) + eps
            )
            alpha_cross = np.where(
                agreement >= np.sqrt(np.clip(prev_gamma, 0.0, 1.0)),
                self._alpha_attack,
                self._alpha_release,
            )

            state.Phi_LR = alpha_cross * state.Phi_LR + (1.0 - alpha_cross) * cross_LR
            state.Phi_LL = self._alpha * state.Phi_LL + (1.0 - self._alpha) * power_L
            state.Phi_RR = self._alpha * state.Phi_RR + (1.0 - self._alpha) * power_R

        gamma = np.abs(state.Phi_LR) ** 2 / (state.Phi_LL * state.Phi_RR + eps)
        return np.clip(gamma, 0.0, 1.0)

    def directness_frame(self, state: CoherenceState) -> np.ndarray:
        """Return in-phase directness for center extraction.

        Magnitude coherence alone treats anti-phase and quadrature material as
        centered.  Only positive real correlation is eligible for the centre.
        """
        denom = np.sqrt(state.Phi_LL * state.Phi_RR) + self._epsilon
        return np.clip(np.real(state.Phi_LR) / denom, 0.0, 1.0)

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
