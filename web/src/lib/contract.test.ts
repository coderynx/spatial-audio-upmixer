// Preview/export parity constants — TypeScript-side sanity checks.
//
// The actual cross-engine check is tests/test_contract_parity.py, which
// compares upmixer.contract.canonical_constants() against this module's
// canonicalConstants(), dumped to tests/fixtures/contract/web_constants.json
// by web/scripts/dump-constants.mjs. This file only guards that the web
// side's own structure is stable and finite — see
// docs/contracts/README.md for the change protocol.
import { describe, expect, it } from "vitest";
import { canonicalConstants } from "./contract";

describe("canonicalConstants", () => {
  it("is deterministic", () => {
    expect(canonicalConstants()).toEqual(canonicalConstants());
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
