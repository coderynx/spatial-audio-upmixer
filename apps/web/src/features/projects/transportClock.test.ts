import { describe, expect, it } from "vitest";
import { BASE_LOOKAHEAD_SECONDS, MAX_LOOKAHEAD_SECONDS, TransportClock } from "./transportClock";

// Minimal fake standing in for `BaseAudioContext`: `currentTime` is a plain
// mutable field the test advances manually, exactly like a real
// `AudioContext`'s clock advancing in real time — but under direct control
// here so the scheduling math can be asserted deterministically.
class FakeContext {
  currentTime = 0;
}

describe("TransportClock position", () => {
  it("returns the fallback when nothing is scheduled", () => {
    const clock = new TransportClock();
    const ctx = new FakeContext();
    expect(clock.position(ctx, 1.5)).toBe(1.5);
  });

  it("tracks elapsed time from the committed timeline origin", () => {
    const clock = new TransportClock();
    const ctx = new FakeContext();
    ctx.currentTime = 10;
    const startAt = clock.reserveStart(ctx);
    clock.commit(ctx, 2, startAt, 10);
    ctx.currentTime = 10 + BASE_LOOKAHEAD_SECONDS + 3;
    expect(clock.position(ctx, 0)).toBeCloseTo(5, 6);
  });

  it("clamps to duration when not looping", () => {
    const clock = new TransportClock();
    clock.setDuration(4);
    const ctx = new FakeContext();
    const startAt = clock.reserveStart(ctx);
    clock.commit(ctx, 0, startAt, 0);
    ctx.currentTime = startAt + 10;
    expect(clock.position(ctx, 0)).toBe(4);
  });

  it("wraps modulo duration when looping", () => {
    const clock = new TransportClock();
    clock.setDuration(4);
    clock.setLoop(true);
    const ctx = new FakeContext();
    const startAt = clock.reserveStart(ctx);
    clock.commit(ctx, 0, startAt, 0);
    ctx.currentTime = startAt + 9; // 9 % 4 = 1
    expect(clock.position(ctx, 0)).toBeCloseTo(1, 6);
  });

  it("clear() drops the timeline back to reporting the fallback", () => {
    const clock = new TransportClock();
    const ctx = new FakeContext();
    const startAt = clock.reserveStart(ctx);
    clock.commit(ctx, 0, startAt, 0);
    expect(clock.isRunning()).toBe(true);
    clock.clear();
    expect(clock.isRunning()).toBe(false);
    expect(clock.position(ctx, 7)).toBe(7);
  });
});

describe("TransportClock adaptive lookahead", () => {
  it("reserves the base lookahead margin when construction is instantaneous", () => {
    const clock = new TransportClock();
    const ctx = new FakeContext();
    ctx.currentTime = 100;
    expect(clock.reserveStart(ctx)).toBeCloseTo(100 + BASE_LOOKAHEAD_SECONDS, 6);
  });

  it("pads the next reservation when the previous pass took a long time to construct", () => {
    // Regression: a fixed lookahead margin can be exhausted mid-schedule
    // under main-thread jank (a big re-render, a GC pause), starting
    // sources scheduled later in the same pass immediately instead of in
    // step with the ones already scheduled — see the module comment.
    const clock = new TransportClock();
    const ctx = new FakeContext();
    const passStartedAt = 0;
    const startAt = clock.reserveStart(ctx);
    ctx.currentTime = 0.2; // this pass took 200ms to construct/start every source
    clock.commit(ctx, 0, startAt, passStartedAt);

    const nextStartAt = clock.reserveStart(ctx);
    expect(nextStartAt - ctx.currentTime).toBeGreaterThan(BASE_LOOKAHEAD_SECONDS);
  });

  it("never pads the margin below the base lookahead even after a fast pass", () => {
    const clock = new TransportClock();
    const ctx = new FakeContext();
    const startAt = clock.reserveStart(ctx);
    clock.commit(ctx, 0, startAt, 0); // instantaneous construction
    const nextStartAt = clock.reserveStart(ctx);
    expect(nextStartAt - ctx.currentTime).toBeCloseTo(BASE_LOOKAHEAD_SECONDS, 6);
  });

  it("caps the padded margin at the maximum lookahead", () => {
    const clock = new TransportClock();
    const ctx = new FakeContext();
    const startAt = clock.reserveStart(ctx);
    ctx.currentTime = 100; // absurdly slow pass
    clock.commit(ctx, 0, startAt, 0);
    const nextStartAt = clock.reserveStart(ctx);
    expect(nextStartAt - ctx.currentTime).toBeCloseTo(MAX_LOOKAHEAD_SECONDS, 6);
  });
});
