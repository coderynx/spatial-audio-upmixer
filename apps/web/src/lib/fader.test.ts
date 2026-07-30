import { describe, expect, it } from "vitest";
import { dbToFaderPosition, FADER_MIN_DB, faderPositionToDb, faderPositionToGain, formatFaderDb } from "./fader";

describe("faderPositionToGain", () => {
  it("is exactly silent at position 0", () => {
    expect(faderPositionToGain(0)).toBe(0);
  });

  it("is exactly unity at position 1", () => {
    expect(faderPositionToGain(1)).toBeCloseTo(1, 10);
  });

  it("is monotonically increasing across the travel", () => {
    const positions = Array.from({ length: 21 }, (_, i) => i / 20);
    const gains = positions.map(faderPositionToGain);
    for (let i = 1; i < gains.length; i++) {
      expect(gains[i]).toBeGreaterThan(gains[i - 1]);
    }
  });

  it("clamps out-of-range positions", () => {
    expect(faderPositionToGain(-0.5)).toBe(0);
    expect(faderPositionToGain(1.5)).toBeCloseTo(1, 10);
  });
});

describe("faderPositionToDb", () => {
  it("maps 0.5 to -30dB and 1 to 0dB", () => {
    expect(faderPositionToDb(0.5)).toBeCloseTo(-30, 10);
    expect(faderPositionToDb(1)).toBeCloseTo(0, 10);
  });

  it("is -Infinity at position 0", () => {
    expect(faderPositionToDb(0)).toBe(-Infinity);
  });

  it("respects FADER_MIN_DB as the floor approached at position 0", () => {
    expect(faderPositionToDb(0.0001)).toBeGreaterThan(FADER_MIN_DB);
  });
});

describe("dbToFaderPosition", () => {
  it("round-trips with faderPositionToDb", () => {
    for (const position of [0.1, 0.25, 0.5, 0.75, 0.9, 1]) {
      const db = faderPositionToDb(position);
      expect(dbToFaderPosition(db)).toBeCloseTo(position, 6);
    }
  });

  it("floors at 0 for -Infinity or values at/below FADER_MIN_DB", () => {
    expect(dbToFaderPosition(-Infinity)).toBe(0);
    expect(dbToFaderPosition(FADER_MIN_DB)).toBe(0);
    expect(dbToFaderPosition(FADER_MIN_DB - 10)).toBe(0);
  });

  it("caps at 1 for values above unity", () => {
    expect(dbToFaderPosition(6)).toBe(1);
  });
});

describe("formatFaderDb", () => {
  it("formats a mid-travel position with one decimal", () => {
    expect(formatFaderDb(0.5)).toBe("-30.0 dB");
  });

  it("formats unity and silence", () => {
    expect(formatFaderDb(1)).toBe("0.0 dB");
    expect(formatFaderDb(0)).toBe("-∞");
  });
});
