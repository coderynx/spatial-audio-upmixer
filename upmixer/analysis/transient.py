from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig


@dataclass
class TransientState:
    """Persistent state for per-band transient detection."""

    prev_mag: np.ndarray
    band_ema_flux: np.ndarray
    initialized: bool = False


class PerBandTransientDetector:
    """Per-bin transient mask via octave-band spectral flux with adaptive thresholding.

    Divides the spectrum into log-spaced octave bands. Per band, computes the
    positive spectral flux (sum of magnitude increases) and compares it against
    a slowly-adapting EMA of the band's own flux history. This makes the detector
    self-normalising: a loud passage raises its threshold so that only RELATIVE
    spikes score as transients.

    The per-band scores are interpolated back to full n_freq bins so that a bass
    transient (kick at 80 Hz) gates only the low-frequency portion of the surround
    routing, leaving treble reverb tails unaffected.
    """

    def __init__(self, config: UpmixConfig, sample_rate: int, n_freq: int):
        self._n_bands = config.transient_n_bands
        self._ema_alpha = config.transient_ema_alpha
        self._k = config.transient_sensitivity_k
        self._eps = config.epsilon
        self._n_freq = n_freq

        self._band_slices, self._band_center_bins = self._build_bands(
            sample_rate, n_freq, config.transient_n_bands
        )

    @staticmethod
    def _build_bands(
        sample_rate: int, n_freq: int, n_bands: int
    ) -> tuple[list[slice], np.ndarray]:
        nyquist = sample_rate / 2.0
        freq_of_bin = np.arange(n_freq) * (nyquist / (n_freq - 1))
        edges = np.logspace(np.log10(20.0), np.log10(nyquist), n_bands + 1)

        slices: list[slice] = []
        centers: list[float] = []
        for i in range(n_bands):
            mask = (freq_of_bin >= edges[i]) & (freq_of_bin < edges[i + 1])
            idx = np.where(mask)[0]
            if len(idx) == 0:
                slices.append(slice(0, 0))
                centers.append((edges[i] + edges[i + 1]) * 0.5 / nyquist * (n_freq - 1))
            else:
                slices.append(slice(int(idx[0]), int(idx[-1]) + 1))
                centers.append((idx[0] + idx[-1]) / 2.0)

        return slices, np.array(centers, dtype=np.float64)

    def create_state(self) -> TransientState:
        return TransientState(
            prev_mag=np.zeros(self._n_freq),
            band_ema_flux=np.zeros(self._n_bands),
            initialized=False,
        )

    def detect_frame(
        self,
        X_L_frame: np.ndarray,
        X_R_frame: np.ndarray,
        state: TransientState,
    ) -> np.ndarray:
        """Return per-bin transient mask of shape (n_freq,), values in [0, 1].

        High at frequency f means a transient onset is present at that frequency.
        """
        mag = np.abs(X_L_frame) + np.abs(X_R_frame)

        if not state.initialized:
            state.prev_mag = mag.copy()
            state.initialized = True
            return np.zeros(self._n_freq)

        positive_diff = np.maximum(0.0, mag - state.prev_mag)

        band_scores = np.empty(self._n_bands)
        for i, sl in enumerate(self._band_slices):
            band_flux = float(positive_diff[sl].sum()) if sl.start != sl.stop else 0.0
            state.band_ema_flux[i] = (
                self._ema_alpha * state.band_ema_flux[i]
                + (1.0 - self._ema_alpha) * band_flux
            )
            threshold = state.band_ema_flux[i] * self._k + self._eps
            band_scores[i] = np.clip(band_flux / threshold, 0.0, 1.0)

        state.prev_mag = mag

        return np.interp(
            np.arange(self._n_freq, dtype=np.float64),
            self._band_center_bins,
            band_scores,
        ).clip(0.0, 1.0)

    def reset(self, state: TransientState) -> None:
        state.prev_mag[:] = 0.0
        state.band_ema_flux[:] = 0.0
        state.initialized = False
