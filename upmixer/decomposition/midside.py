from dataclasses import dataclass

import numpy as np


@dataclass
class MidSideResult:
    mid: np.ndarray
    side: np.ndarray


class MidSideDecomposer:
    """Frequency-domain mid/side decomposition."""

    def decompose(self, X_L: np.ndarray, X_R: np.ndarray) -> MidSideResult:
        """M = (L + R) / 2, S = (L - R) / 2"""
        return MidSideResult(
            mid=(X_L + X_R) / 2.0,
            side=(X_L - X_R) / 2.0,
        )

    def recompose(
        self, mid: np.ndarray, side: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """L = M + S, R = M - S"""
        return mid + side, mid - side
