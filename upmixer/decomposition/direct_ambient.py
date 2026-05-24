from dataclasses import dataclass

import numpy as np

from upmixer.analysis.harmonicity import HarmonicityEstimator
from upmixer.analysis.transient import PerBandTransientDetector
from upmixer.config import UpmixConfig


@dataclass
class SoftMatrixResult:
    """Result of perceptual spectral decomposition for one STFT frame."""

    center: np.ndarray
    front_L: np.ndarray
    front_R: np.ndarray
    ambient_L: np.ndarray
    ambient_R: np.ndarray
    signal_L: np.ndarray
    signal_R: np.ndarray
    width: np.ndarray
    transient_score: np.ndarray
    harmonic_mask: np.ndarray


@dataclass
class SoftMatrixBatchResult:
    """Result of perceptual spectral decomposition for full spectrogram (batch mode)."""

    center: np.ndarray
    front_L: np.ndarray
    front_R: np.ndarray
    ambient_L: np.ndarray
    ambient_R: np.ndarray
    signal_L: np.ndarray
    signal_R: np.ndarray
    width: np.ndarray
    transient_score: np.ndarray
    harmonic_mask: np.ndarray


class SoftMatrixDecomposer:
    """Perceptual spectral decomposer for music remix upmixing.

    Per STFT frame:
    1. Panning-aware center extraction: center only for coherent, center-panned bins.
    2. Width = 1 - coherence: per-bin diffuseness passed to router.
    3. Per-band transient detection: per-bin transient_score via PerBandTransientDetector.
       A bass transient only gates bass-frequency surround routing, not treble.
    4. Harmonicity mask: per-bin spectral floor analysis via HarmonicityEstimator.
       Tonal peaks stay in front; noise-floor content routes to surrounds.
    """

    def __init__(
        self,
        config: UpmixConfig,
        sample_rate: int = 44100,
        n_freq: int = 2049,
    ):
        self._center_extraction_gain = config.center_extraction_gain
        self._center_attenuation = config.center_attenuation
        self._eps = config.epsilon

        self._transient_detector = PerBandTransientDetector(config, sample_rate, n_freq)
        self._transient_state = self._transient_detector.create_state()

        self._harmonicity_est = HarmonicityEstimator(config, n_freq)
        self._harmonicity_state = self._harmonicity_est.create_state()

        self._prev_mag: np.ndarray | None = None

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

        center_weight = 1.0 - np.abs(pan)
        center = self._center_extraction_gain * center_weight * mid

        reduction = self._center_attenuation * center_weight * 0.5
        front_L = X_L_frame * (1.0 - reduction)
        front_R = X_R_frame * (1.0 - reduction)

        width = 1.0 - coherence_frame

        transient_score = self._transient_detector.detect_frame(
            X_L_frame, X_R_frame, self._transient_state
        )
        harmonic_mask = self._harmonicity_est.estimate_frame(
            X_L_frame, X_R_frame, self._harmonicity_state
        )

        return SoftMatrixResult(
            center=center,
            front_L=front_L,
            front_R=front_R,
            ambient_L=side,
            ambient_R=-side,
            signal_L=X_L_frame,
            signal_R=X_R_frame,
            width=width,
            transient_score=transient_score,
            harmonic_mask=harmonic_mask,
        )

    def decompose(
        self,
        X_L: np.ndarray,
        X_R: np.ndarray,
        coherence: np.ndarray,
    ) -> SoftMatrixBatchResult:
        """Batch mode. Transient and harmonicity analysis not available."""
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

        n_freq = X_L.shape[0]
        n_frames = X_L.shape[1] if X_L.ndim > 1 else 1
        return SoftMatrixBatchResult(
            center=center,
            front_L=front_L,
            front_R=front_R,
            ambient_L=side,
            ambient_R=-side,
            signal_L=X_L,
            signal_R=X_R,
            width=1.0 - coherence,
            transient_score=np.zeros(n_frames),
            harmonic_mask=np.zeros((n_freq, n_frames)),
        )

    def reset(self) -> None:
        self._transient_detector.reset(self._transient_state)
        self._harmonicity_est.reset(self._harmonicity_state)
        self._prev_mag = None
