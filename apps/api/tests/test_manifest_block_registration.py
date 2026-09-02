"""Every mastering block the web can send must survive validation.

`MasteringChain` imports its stage modules lazily inside `process()`, so the
manifest blocks they register (`mastering.highpass`, `.clip`, `.dynamic_eq`,
…) exist only once something has imported those modules. `shared/manifests.py`
does that on purpose. Forgetting one there does not fail any unit test — the
stage works, the chain works — it fails at runtime with
"Unknown manifest field 'mastering.<block>'" the first time a browser saves a
project, which is the worst place to find out.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

CORE_MASTERING = Path(__file__).resolve().parents[3] / "packages" / "core" / "src" / "mastering"


def _modules_registering_blocks() -> set[str]:
    """Mastering modules that call `register_block_keys` at import time."""
    found = set()
    for path in CORE_MASTERING.rglob("*.py"):
        if "register_block_keys" in path.read_text():
            relative = path.relative_to(CORE_MASTERING).with_suffix("")
            parts = [part for part in relative.parts if part != "__init__"]
            found.add("upmixer.mastering" + ("." + ".".join(parts) if parts else ""))
    return found


def test_importing_shared_manifests_reaches_every_module_that_registers_a_block():
    """In a *fresh* interpreter — the whole point is what one import pulls in.

    Run in-process this would pass vacuously: by the time any other test has
    run, the chain has already imported its stages for its own reasons.
    """
    expected = _modules_registering_blocks()
    reached = subprocess.run(
        [
            sys.executable,
            "-c",
            "import upmixer_web.shared.manifests, sys, json;"
            "print(json.dumps([m for m in sys.modules if m.startswith('upmixer.mastering')]))",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    missing = expected - set(__import__("json").loads(reached))
    assert not missing, (
        f"{', '.join(sorted(missing))} register manifest blocks but are not reached by "
        f"shared/manifests.py — their fields are rejected as 'Unknown manifest field'."
    )


def test_the_full_mastering_block_the_web_sends_validates(web_client):
    """The web's `defaultManifest.mastering`, sent whole on every save.

    `normalizeManifest` fills in every block, so a project save carries all of
    them whether or not the user touched them.
    """
    from upmixer_web.shared.manifests import normalize_job_manifest

    normalize_job_manifest({
        "version": "1.0.0",
        "mastering": {
            "loudness": {
                "normalize": True, "target_preset": None, "target": None, "max_tp": None,
            },
            "highpass": {"enabled": False, "cutoff_hz": 20},
            "clip": {"enabled": False, "clip_db": 0.5, "knee": 1},
            "eq": {"profile": None, "strength": 1},
            "dynamic_eq": {"bands": []},
            "match_reference": {"strength": 0.7, "spectrum": True, "rms": True, "max_db": 6},
            "compressor": {
                "profile": "transparent", "threshold_db": None, "ratio": None,
                "attack_ms": None, "release_ms": None, "knee_db": None,
                "makeup_db": None, "sidechain_hpf_hz": None,
            },
            "bass": {
                "profile": None, "sub_gain_db": None, "mid_gain_db": None, "unify_hz": None,
                "spread": None, "punch": None, "excite": None, "lfe_mode": None,
                "harmonics": None,
                "lfe_send": None, "lfe_gain_db": None, "decorrelate": None,
            },
        },
    })
