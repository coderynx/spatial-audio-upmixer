"""Signed preview/export parity contract — Python-side signature check.

Asserts upmixer.contract.contract_signature() matches the value pinned in
docs/contracts/preview_export_parity.md. web/src/lib/contract.test.ts
asserts the same pinned value against the TypeScript mirror
(web/src/lib/contract.ts). If either side's contracted constants drift from
the pinned signature, its own test fails first — see
docs/contracts/README.md for the required change protocol before updating
the pinned value.
"""
from __future__ import annotations

from upmixer.contract import canonical_constants, contract_signature

_PINNED_SIGNATURE = "8819f516a674d8fc9ce9be72e7733cc9cdba1b0febfa13a01097bad96811e218"


def test_contract_signature_matches_pinned_doc_value():
    assert contract_signature() == _PINNED_SIGNATURE, (
        "upmixer/contract.py's constants no longer match the signature pinned in "
        "docs/contracts/preview_export_parity.md. If this is an intentional, "
        "both-sides change, follow docs/contracts/README.md's change protocol: "
        "update the TypeScript mirror (web/src/lib/contract.ts / "
        "masteringProfiles.ts), update the constants catalog and regenerate the "
        "pinned signature in docs/contracts/preview_export_parity.md, then "
        "re-run this test and web/src/lib/contract.test.ts."
    )


def test_contract_signature_is_deterministic():
    assert contract_signature() == contract_signature()


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
