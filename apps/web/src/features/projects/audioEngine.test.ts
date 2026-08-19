import { describe, expect, it } from "vitest";

import { withReferenceMatchParams } from "./audioEngine";
import { monitorMastering } from "./masterPreview";
import { bypassMatchDb, correctionGain } from "./audioAnalysis";
import { resolveDeliveryTarget } from "./masteringProfiles";
import { TEST_ENGINE_CONSTANTS } from "./engineConstants.fixture";

const TARGETS = TEST_ENGINE_CONSTANTS.deliveryTargets;
const FALLBACK = TEST_ENGINE_CONSTANTS.deliveryDefault;

describe("resolveDeliveryTarget", () => {
  it("uses the served default with no preset named and no override set", () => {
    expect(resolveDeliveryTarget(undefined, TARGETS, FALLBACK)).toEqual(FALLBACK);
    expect(resolveDeliveryTarget({ target_preset: null }, TARGETS, FALLBACK)).toEqual(FALLBACK);
  });

  it("takes both numbers and the tolerance from a named target", () => {
    expect(resolveDeliveryTarget({ target_preset: "ebu-r128" }, TARGETS, FALLBACK)).toEqual({
      target_lkfs: -23,
      max_tp_dbtp: -1,
      tolerance_lu: 0.5,
    });
  });

  it("lets an explicit field override the preset one at a time", () => {
    const resolved = resolveDeliveryTarget(
      { target_preset: "ebu-r128", target: -20 },
      TARGETS,
      FALLBACK,
    );
    expect(resolved.target_lkfs).toBe(-20);
    expect(resolved.max_tp_dbtp).toBe(-1);
    expect(resolved.tolerance_lu).toBe(0.5);
  });

  it("falls back rather than guessing when the preset name is unknown", () => {
    expect(resolveDeliveryTarget({ target_preset: "atmos" }, TARGETS, FALLBACK)).toEqual(
      FALLBACK,
    );
  });
});

describe("loudness-matched bypass", () => {
  const DELIVERY = { target_lkfs: -18, max_tp_dbtp: -1 };
  const MAX_GAIN_DB = 30;
  // Normalizes cleanly to the target: +2 dB of gain still leaves 2 dB of
  // true-peak headroom.
  const MASTERED = { lkfs: -20, dbtp: -3 };
  // The unmastered side has no limiter, so its ceiling clamp bites first and
  // leaves it 5 LU quiet even after normalization.
  const BYPASSED = { lkfs: -24, dbtp: -2 };

  it("stays at unity until both sides are measured", () => {
    expect(bypassMatchDb(MASTERED, undefined, DELIVERY, MAX_GAIN_DB, true)).toBe(0);
    expect(bypassMatchDb(undefined, BYPASSED, DELIVERY, MAX_GAIN_DB, true)).toBe(0);
  });

  it("closes the gap the true-peak ceiling leaves between the two sides", () => {
    const mastered = -20 + 20 * Math.log10(correctionGain(MASTERED, DELIVERY, MAX_GAIN_DB, true));
    const bypassed = -24 + 20 * Math.log10(correctionGain(BYPASSED, DELIVERY, MAX_GAIN_DB, true));
    expect(mastered).toBeCloseTo(-18, 6);
    expect(bypassed).toBeCloseTo(-23, 6);
    expect(bypassMatchDb(MASTERED, BYPASSED, DELIVERY, MAX_GAIN_DB, true)).toBeCloseTo(5, 6);
  });

  it("matches the raw measurements when normalization is off", () => {
    expect(bypassMatchDb(MASTERED, BYPASSED, DELIVERY, MAX_GAIN_DB, false)).toBeCloseTo(4, 6);
  });

  it("is a no-op when both sides land on the target", () => {
    const same = { lkfs: -20, dbtp: -6 };
    expect(bypassMatchDb(same, same, DELIVERY, MAX_GAIN_DB, true)).toBeCloseTo(0, 12);
  });
});

describe("withReferenceMatchParams", () => {
  it("appends strength/max_db as the first query params", () => {
    expect(withReferenceMatchParams("/api/v1/projects/1/reference-match/fir", 0.5, 4)).toBe(
      "/api/v1/projects/1/reference-match/fir?strength=0.5&max_db=4",
    );
  });

  it("appends with & when the base url already carries a query param", () => {
    expect(withReferenceMatchParams("/fir?v=2", 1, 6)).toBe("/fir?v=2&strength=1&max_db=6");
  });

  it("leaves unset realization controls off the url", () => {
    expect(withReferenceMatchParams("/fir", 1, 6, null, null, null)).toBe(
      "/fir?strength=1&max_db=6",
    );
  });

  it("appends only the realization controls that are set", () => {
    expect(withReferenceMatchParams("/fir", 1, 6, null, 300, null)).toBe(
      "/fir?strength=1&max_db=6&low_hz=300",
    );
    expect(withReferenceMatchParams("/fir", 1, 6, 0.5, 300, 9000)).toBe(
      "/fir?strength=1&max_db=6&smooth_oct=0.5&low_hz=300&high_hz=9000",
    );
  });
});

describe("monitorMastering", () => {
  const mastering = {
    loudness: { normalize: true },
    eq: { profile: "spatial-air" },
    match_reference: { fir_url: "/fir", spectrum: true, rms: true, rms_gain_db: 2 },
  };

  it("passes the block through when nothing is bypassed", () => {
    expect(monitorMastering(mastering, false)).toBe(mastering);
  });

  it("strips everything but loudness for the whole-chain bypass", () => {
    expect(monitorMastering(mastering, true)).toEqual({ loudness: mastering.loudness });
  });

  it("strips both reference-match stages for the stage-scoped bypass", () => {
    const out = monitorMastering(mastering, false, true);
    expect(out?.eq).toEqual(mastering.eq);
    expect(out?.match_reference).toEqual({
      fir_url: "/fir", spectrum: false, rms: false, rms_gain_db: 2,
    });
  });

  it("lets the whole-chain bypass win over the stage-scoped one", () => {
    expect(monitorMastering(mastering, true, true)).toEqual({ loudness: mastering.loudness });
  });
});
