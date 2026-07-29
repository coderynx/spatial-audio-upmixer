"""Preview/export parity — live value cross-check.

Compares upmixer.contract.canonical_constants() directly against the web
side's own canonicalConstants(), dumped ahead of time to
tests/fixtures/contract/web_constants.json by
web/scripts/dump-constants.mjs (`npm run constants:dump` from web/).
web/src/lib/contract.test.ts covers the equivalent TypeScript-side sanity
checks (determinism, no NaN/Infinity). If either side's contracted
constants drift, this test fails with the specific diverging keys — see
docs/contracts/README.md for the required change protocol before editing a
contracted constant.
"""
from __future__ import annotations

import json
import os

from upmixer.contract import _canonical_value, canonical_constants

_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "contract", "web_constants.json"
)


def _diverging_keys(python_value: object, web_value: object, path: str = "") -> list[str]:
    if isinstance(python_value, dict) and isinstance(web_value, dict):
        diffs: list[str] = []
        keys = set(python_value) | set(web_value)
        for key in sorted(keys):
            child_path = f"{path}.{key}" if path else key
            if key not in python_value:
                diffs.append(f"{child_path}: missing on Python side")
            elif key not in web_value:
                diffs.append(f"{child_path}: missing on web side")
            else:
                diffs.extend(_diverging_keys(python_value[key], web_value[key], child_path))
        return diffs
    if _canonical_value(python_value) != _canonical_value(web_value):
        return [f"{path}: python={python_value!r} web={web_value!r}"]
    return []


def test_web_constants_match_python():
    assert os.path.exists(_FIXTURE_PATH), (
        f"{_FIXTURE_PATH} not found — run `npm run constants:dump` from web/ "
        "to generate it (see web/scripts/dump-constants.mjs)."
    )
    with open(_FIXTURE_PATH) as f:
        web_constants = json.load(f)

    diffs = _diverging_keys(canonical_constants(), web_constants)
    assert not diffs, (
        "upmixer/contract.py's constants no longer match "
        f"tests/fixtures/contract/web_constants.json:\n" + "\n".join(diffs) + "\n\n"
        "If this is an intentional, both-sides change, follow "
        "docs/contracts/README.md's change protocol: update the TypeScript mirror "
        "(web/src/lib/contract.ts / masteringProfiles.ts), re-run "
        "`npm run constants:dump` from web/, then re-run this test."
    )


def test_canonical_constants_has_no_nan_or_inf():
    def _walk(value):
        if isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                _walk(v)
        elif isinstance(value, float):
            assert value == value, "NaN is not representable in the canonical contract"
            assert value not in (float("inf"), float("-inf")), (
                "Infinity is not representable in the canonical contract"
            )

    _walk(canonical_constants())
