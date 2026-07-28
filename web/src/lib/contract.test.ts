// Signed preview/export parity contract — TypeScript-side signature check.
//
// Asserts contractSignature() matches the value pinned in
// docs/contracts/preview_export_parity.md. tests/test_contract_parity.py
// asserts the same pinned value against the Python mirror
// (upmixer/contract.py). If either side's contracted constants drift from
// the pinned signature, its own test fails first — see
// docs/contracts/README.md for the required change protocol before
// updating the pinned value.
import { describe, expect, it } from "vitest";
import { canonicalConstants, contractSignature } from "./contract";

const PINNED_SIGNATURE = "8819f516a674d8fc9ce9be72e7733cc9cdba1b0febfa13a01097bad96811e218";

describe("contractSignature", () => {
  it("matches the signature pinned in docs/contracts/preview_export_parity.md", () => {
    expect(
      contractSignature(),
      "web/src/lib/contract.ts's constants no longer match the signature pinned in " +
        "docs/contracts/preview_export_parity.md. If this is an intentional, both-sides " +
        "change, follow docs/contracts/README.md's change protocol: update the Python " +
        "mirror (upmixer/contract.py), update the constants catalog and regenerate the " +
        "pinned signature in docs/contracts/preview_export_parity.md, then re-run this " +
        "test and tests/test_contract_parity.py.",
    ).toBe(PINNED_SIGNATURE);
  });

  it("is deterministic", () => {
    expect(contractSignature()).toBe(contractSignature());
  });

  it("has no NaN or Infinity in the canonical structure", () => {
    const walk = (value: unknown): void => {
      if (Array.isArray(value)) {
        value.forEach(walk);
      } else if (value !== null && typeof value === "object") {
        Object.values(value).forEach(walk);
      } else if (typeof value === "number") {
        expect(Number.isFinite(value)).toBe(true);
      }
    };
    walk(canonicalConstants());
  });
});
