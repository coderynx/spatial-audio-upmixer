"""Mode-aware CPU resource policy tests."""
from __future__ import annotations

import types
from unittest.mock import patch

from upmixer.__main__ import _apply_resource_limits


def _run(policy: str, mode: str):
    calls: dict[str, list[int]] = {
        "torch": [], "interop": [], "blas": [], "nice": [],
    }
    torch = types.SimpleNamespace(
        set_num_threads=lambda n: calls["torch"].append(n),
        set_num_interop_threads=lambda n: calls["interop"].append(n),
    )
    threadpoolctl = types.SimpleNamespace(
        threadpool_limits=lambda limits: calls["blas"].append(limits)
    )
    with (
        patch.dict("sys.modules", {"torch": torch, "threadpoolctl": threadpoolctl}),
        patch("os.cpu_count", return_value=12),
        patch("os.nice", side_effect=lambda n: calls["nice"].append(n)),
    ):
        _apply_resource_limits(policy, mode)
    return calls


def test_auto_stem_uses_full_resources():
    calls = _run("auto", "stem")
    assert calls == {
        "torch": [12], "interop": [1], "blas": [12], "nice": [],
    }


def test_auto_realtime_preserves_polite_resources():
    calls = _run("auto", "realtime")
    assert calls == {
        "torch": [6], "interop": [1], "blas": [6], "nice": [10],
    }


def test_explicit_low_overrides_stem_default():
    calls = _run("low", "stem")
    assert calls == {
        "torch": [6], "interop": [1], "blas": [6], "nice": [10],
    }
