import numpy as np

from upmixer.decomposition.midside import MidSideDecomposer


def test_decompose_recompose_identity():
    """decompose then recompose should return original L, R."""
    rng = np.random.default_rng(42)
    X_L = rng.standard_normal((100, 50)) + 1j * rng.standard_normal((100, 50))
    X_R = rng.standard_normal((100, 50)) + 1j * rng.standard_normal((100, 50))

    decomposer = MidSideDecomposer()
    result = decomposer.decompose(X_L, X_R)
    L_recon, R_recon = decomposer.recompose(result.mid, result.side)

    np.testing.assert_allclose(L_recon, X_L, atol=1e-14)
    np.testing.assert_allclose(R_recon, X_R, atol=1e-14)


def test_center_panned_is_all_mid():
    """If L == R, all energy should be in mid, side should be zero."""
    signal = np.ones((50, 20)) + 1j * np.ones((50, 20))

    decomposer = MidSideDecomposer()
    result = decomposer.decompose(signal, signal)

    np.testing.assert_allclose(result.mid, signal, atol=1e-14)
    np.testing.assert_allclose(result.side, np.zeros_like(signal), atol=1e-14)


def test_side_panned_is_all_side():
    """If L == -R, all energy should be in side, mid should be zero."""
    signal = np.ones((50, 20)) + 1j * np.ones((50, 20))

    decomposer = MidSideDecomposer()
    result = decomposer.decompose(signal, -signal)

    np.testing.assert_allclose(result.mid, np.zeros_like(signal), atol=1e-14)
    np.testing.assert_allclose(result.side, signal, atol=1e-14)
