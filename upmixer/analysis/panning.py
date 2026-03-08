from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig


@dataclass
class PanningResult:
    """Batch panning result (2D arrays)."""

    a_L: np.ndarray  # shape (n_freq, n_frames)
    a_R: np.ndarray
    psi: np.ndarray


@dataclass
class PanningFrameResult:
    """Per-frame panning result (1D arrays)."""

    a_L: np.ndarray  # shape (n_freq,)
    a_R: np.ndarray
    psi: np.ndarray


class PanningEstimator:
    """Estimates panning coefficients and position per TF bin."""

    def __init__(self, config: UpmixConfig):
        self._epsilon = config.epsilon

    def estimate_frame(
        self, X_L_frame: np.ndarray, X_R_frame: np.ndarray
    ) -> PanningFrameResult:
        """Per-frame panning estimation."""
        power_L = np.abs(X_L_frame) ** 2
        power_R = np.abs(X_R_frame) ** 2
        power_total = power_L + power_R + self._epsilon

        return PanningFrameResult(
            a_L=np.sqrt(power_L / power_total),
            a_R=np.sqrt(power_R / power_total),
            psi=(power_R - power_L) / power_total,
        )

    def estimate(self, X_L: np.ndarray, X_R: np.ndarray) -> PanningResult:
        """Batch panning estimation."""
        power_L = np.abs(X_L) ** 2
        power_R = np.abs(X_R) ** 2
        power_total = power_L + power_R + self._epsilon

        return PanningResult(
            a_L=np.sqrt(power_L / power_total),
            a_R=np.sqrt(power_R / power_total),
            psi=(power_R - power_L) / power_total,
        )
