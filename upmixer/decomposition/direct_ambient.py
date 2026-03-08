from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig


@dataclass
class SoftMatrixResult:
    """Result of soft matrix decomposition for one frame."""

    center: np.ndarray  # Center channel spectrum (n_freq,)
    front_L: np.ndarray  # Front left spectrum (n_freq,)
    front_R: np.ndarray  # Front right spectrum (n_freq,)
    ambient_L: np.ndarray  # Left ambient/surround spectrum (n_freq,)
    ambient_R: np.ndarray  # Right ambient/surround spectrum (n_freq,)


@dataclass
class SoftMatrixBatchResult:
    """Result of soft matrix decomposition for full spectrogram (batch mode)."""

    center: np.ndarray  # (n_freq, n_frames)
    front_L: np.ndarray
    front_R: np.ndarray
    ambient_L: np.ndarray
    ambient_R: np.ndarray


class SoftMatrixDecomposer:
    """Soft matrix upmixer: gain-based spatial remixing without spectral subtraction.

    Instead of isolating the center via Wiener masking (which causes phase
    artifacts), this approach uses gain modulation only:

    1. Center = gain * sqrt(coherence) * mid  -- weighted copy, no subtraction
    2. FL/FR = L/R * (1 - attenuation * coherence * 0.5) -- gentle gain reduction
    3. Ambient = side = (L-R)/2  -- fixed linear combination, clean phase

    This avoids all phase artifacts from spectral subtraction.
    """

    def __init__(self, config: UpmixConfig):
        self._center_extraction_gain = config.center_extraction_gain
        self._center_attenuation = config.center_attenuation

    def decompose_frame(
        self,
        X_L_frame: np.ndarray,
        X_R_frame: np.ndarray,
        coherence_frame: np.ndarray,
    ) -> SoftMatrixResult:
        """Decompose one frame using soft matrixing."""
        mid = (X_L_frame + X_R_frame) * 0.5
        side = (X_L_frame - X_R_frame) * 0.5

        # Coherence-weighted center extraction: sqrt for gentler curve
        coherence_gain = self._center_extraction_gain * np.sqrt(coherence_frame)
        center = coherence_gain * mid

        # Front L/R: gentle coherence-proportional attenuation (no subtraction)
        center_reduction = self._center_attenuation * coherence_frame
        front_L = X_L_frame * (1.0 - center_reduction * 0.5)
        front_R = X_R_frame * (1.0 - center_reduction * 0.5)

        # Ambient: side signal for surrounds
        ambient_L = side
        ambient_R = -side  # Negated so SL and SR differ

        return SoftMatrixResult(
            center=center,
            front_L=front_L,
            front_R=front_R,
            ambient_L=ambient_L,
            ambient_R=ambient_R,
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

        coherence_gain = self._center_extraction_gain * np.sqrt(coherence)
        center = coherence_gain * mid

        center_reduction = self._center_attenuation * coherence
        front_L = X_L * (1.0 - center_reduction * 0.5)
        front_R = X_R * (1.0 - center_reduction * 0.5)

        return SoftMatrixBatchResult(
            center=center,
            front_L=front_L,
            front_R=front_R,
            ambient_L=side,
            ambient_R=-side,
        )
