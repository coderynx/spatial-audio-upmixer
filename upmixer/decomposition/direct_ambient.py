from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig


@dataclass
class SoftMatrixResult:
    """Result of soft matrix decomposition for one frame."""

    center: np.ndarray      # Center channel spectrum (n_freq,)
    front_L: np.ndarray     # Front left spectrum (n_freq,)
    front_R: np.ndarray     # Front right spectrum (n_freq,)
    ambient_L: np.ndarray   # M-S side = (L-R)/2
    ambient_R: np.ndarray   # -(L-R)/2
    signal_L: np.ndarray    # Raw left input (for height air extraction)
    signal_R: np.ndarray    # Raw right input


@dataclass
class SoftMatrixBatchResult:
    """Result of soft matrix decomposition for full spectrogram (batch mode)."""

    center: np.ndarray      # (n_freq, n_frames)
    front_L: np.ndarray
    front_R: np.ndarray
    ambient_L: np.ndarray
    ambient_R: np.ndarray
    signal_L: np.ndarray
    signal_R: np.ndarray


class SoftMatrixDecomposer:
    """Panning-aware soft matrix upmixer.

    Center is extracted only for content that is both coherent (correlated)
    AND center-panned (instantaneous pan near zero). Wide or hard-panned
    content stays in FL/FR unmodified.

    Ambient = pure M-S side signal — no decorrelation, no artificial coloring.
    """

    def __init__(self, config: UpmixConfig):
        self._center_extraction_gain = config.center_extraction_gain
        self._center_attenuation = config.center_attenuation
        self._eps = config.epsilon

    def decompose_frame(
        self,
        X_L_frame: np.ndarray,
        X_R_frame: np.ndarray,
        coherence_frame: np.ndarray,
    ) -> SoftMatrixResult:
        mid = (X_L_frame + X_R_frame) * 0.5
        side = (X_L_frame - X_R_frame) * 0.5

        mag_L = np.abs(X_L_frame)
        mag_R = np.abs(X_R_frame)
        pan = (mag_L - mag_R) / (mag_L + mag_R + self._eps)

        # Extract center only where signal is coherent AND center-panned
        center_weight = coherence_frame * (1.0 - np.abs(pan))
        center = self._center_extraction_gain * center_weight * mid

        reduction = self._center_attenuation * center_weight * 0.5
        front_L = X_L_frame * (1.0 - reduction)
        front_R = X_R_frame * (1.0 - reduction)

        return SoftMatrixResult(
            center=center,
            front_L=front_L,
            front_R=front_R,
            ambient_L=side,
            ambient_R=-side,
            signal_L=X_L_frame,
            signal_R=X_R_frame,
        )

    def decompose(
        self,
        X_L: np.ndarray,
        X_R: np.ndarray,
        coherence: np.ndarray,
    ) -> SoftMatrixBatchResult:
        """Batch mode: process full spectrograms."""
        mid = (X_L + X_R) * 0.5
        side = (X_L - X_R) * 0.5

        mag_L = np.abs(X_L)
        mag_R = np.abs(X_R)
        pan = (mag_L - mag_R) / (mag_L + mag_R + self._eps)

        center_weight = coherence * (1.0 - np.abs(pan))
        center = self._center_extraction_gain * center_weight * mid

        reduction = self._center_attenuation * center_weight * 0.5
        front_L = X_L * (1.0 - reduction)
        front_R = X_R * (1.0 - reduction)

        return SoftMatrixBatchResult(
            center=center,
            front_L=front_L,
            front_R=front_R,
            ambient_L=side,
            ambient_R=-side,
            signal_L=X_L,
            signal_R=X_R,
        )
