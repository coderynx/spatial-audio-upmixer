import { describe, expect, it } from "vitest";
import { STRIP_DB_TICKS, dbToY } from "@/lib/meterScale";
import { BAR_GAP, BAR_WIDTH, SCALE_WIDTH, stripMeterWidth } from "./StripMeter";

describe("stripMeterWidth", () => {
  it("reserves one bar for a mono stem and two for a stereo one", () => {
    expect(stripMeterWidth(1)).toBe(SCALE_WIDTH + BAR_WIDTH);
    expect(stripMeterWidth(2)).toBe(SCALE_WIDTH + BAR_WIDTH * 2 + BAR_GAP);
  });

  it("grows by exactly one bar plus its gap per extra channel", () => {
    expect(stripMeterWidth(2) - stripMeterWidth(1)).toBe(BAR_WIDTH + BAR_GAP);
  });
});

describe("the channel-strip dB scale", () => {
  it("prints Logic's strip stops, finest at the top", () => {
    expect(STRIP_DB_TICKS[0]).toBe(0);
    expect(STRIP_DB_TICKS.at(-1)).toBe(-60);
    expect(STRIP_DB_TICKS.slice(0, 7)).toEqual([0, -3, -6, -9, -12, -15, -18]);
  });

  it("maps the scale ends to the meter ends", () => {
    expect(dbToY(0, 0, 100, STRIP_DB_TICKS)).toBe(0);
    expect(dbToY(-60, 0, 100, STRIP_DB_TICKS)).toBe(100);
  });

  it("gives the top 18 dB half the travel, unlike the coarser field scale", () => {
    const stripY = dbToY(-18, 0, 100, STRIP_DB_TICKS);
    expect(stripY).toBeCloseTo(50, 5);
    // The ChannelMeters scale reaches -18 much sooner, which is the whole
    // reason the strip carries its own tick set.
    expect(dbToY(-18, 0, 100)).toBeGreaterThan(stripY);
  });

  it("keeps monotonic ordering across the whole range", () => {
    const positions = [0, -3, -12, -24, -40, -60].map((db) => dbToY(db, 0, 100, STRIP_DB_TICKS));
    for (let i = 1; i < positions.length; i += 1) {
      expect(positions[i]).toBeGreaterThan(positions[i - 1]);
    }
  });
});
