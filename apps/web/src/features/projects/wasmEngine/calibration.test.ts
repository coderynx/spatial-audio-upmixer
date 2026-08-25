import { describe, expect, it, vi } from "vitest";

import { LoudnessCalibration } from "./calibration";
import type { Measured } from "../audioAnalysis";

function harness(results: Record<string, Measured>) {
  const measured: string[] = [];
  const state = { key: "", playing: false, resumed: 0, measuring: [] as boolean[], events: [] as string[] };
  const calibration = new LoudnessCalibration({
    measure: async () => {
      measured.push(state.key);
      return results[state.key] ?? null;
    },
    apply: vi.fn(),
    onMeasuring: (value) => {
      state.measuring.push(value);
      state.events.push(`measuring:${value}`);
    },
    onProgress: vi.fn(),
    pause: () => {
      const wasPlaying = state.playing;
      state.playing = false;
      return wasPlaying;
    },
    resume: () => {
      state.resumed += 1;
      state.playing = true;
      state.events.push("resume");
    },
  });
  const ensure = async (key: string) => {
    state.key = key;
    await calibration.ensure(key, [1, 1]);
  };
  return { calibration, ensure, measured, state };
}

const MASTERED = { lkfs: -20, dbtp: -3 };
const BYPASSED = { lkfs: -24, dbtp: -2 };

describe("LoudnessCalibration", () => {
  it("gates playback until the programme it will play has been measured", async () => {
    const { calibration, ensure } = harness({ "native:mastered": MASTERED });
    expect(calibration.covers("native:mastered")).toBe(false);

    await ensure("native:mastered");
    expect(calibration.covers("native:mastered")).toBe(true);
    expect(calibration.measured).toEqual(MASTERED);
    // The A/B's other side is a different programme, so it is not covered by
    // the one just measured.
    expect(calibration.covers("native:bypassed")).toBe(false);
  });

  it("measures each side of the A/B once, then switches between them free", async () => {
    const { calibration, ensure, measured } = harness({
      "native:mastered": MASTERED,
      "native:bypassed": BYPASSED,
    });

    await ensure("native:mastered");
    await ensure("native:bypassed");
    await ensure("native:mastered");
    await ensure("native:bypassed");

    expect(measured).toEqual(["native:mastered", "native:bypassed"]);
    expect(calibration.get("native:mastered")).toEqual(MASTERED);
    expect(calibration.get("native:bypassed")).toEqual(BYPASSED);
    expect(calibration.measured).toEqual(BYPASSED);
  });

  it("renders uncorrected only while a pass is in flight", async () => {
    const { calibration, ensure, state } = harness({ "native:mastered": MASTERED });
    expect(calibration.raw).toBe(false);
    const pass = ensure("native:mastered");
    expect(calibration.raw).toBe(true);
    await pass;
    expect(calibration.raw).toBe(false);
    expect(state.measuring).toEqual([true, false]);
  });

  it("pauses a playing transport for a real pass and resumes it after", async () => {
    const { ensure, state } = harness({
      "native:mastered": MASTERED,
      "native:bypassed": BYPASSED,
    });
    state.playing = true;
    await ensure("native:mastered");
    expect(state.resumed).toBe(1);
    expect(state.playing).toBe(true);
    expect(state.events).toEqual(["measuring:true", "measuring:false", "resume"]);

    // The cached side needs no pass, so it costs no pause either.
    await ensure("native:bypassed");
    await ensure("native:mastered");
    expect(state.resumed).toBe(2);
  });

  it("keeps the refinement that belongs to the pass it resolved", async () => {
    const { calibration, ensure } = harness({ "native:mastered": MASTERED });
    await ensure("native:mastered");
    const exact = { lkfs: -19.4, dbtp: -2.8 };
    expect(calibration.refine(exact)).toBe(true);
    expect(calibration.measured).toEqual(exact);
    expect(calibration.get("native:mastered")).toEqual(exact);
  });

  it("drops a refinement posted for a programme a newer pass replaced", async () => {
    const { calibration, ensure } = harness({ "native:mastered": MASTERED });
    await ensure("native:mastered");
    const pending = ensure("native:bypassed");
    expect(calibration.refine({ lkfs: -19.4, dbtp: -2.8 })).toBe(false);
    expect(calibration.measured).toEqual(MASTERED);
    await pending;
  });

  it("forgets everything on reset, so a new project re-measures", async () => {
    const { calibration, ensure, measured } = harness({ "native:mastered": MASTERED });
    await ensure("native:mastered");
    calibration.reset();
    expect(calibration.covers("native:mastered")).toBe(false);
    await ensure("native:mastered");
    expect(measured).toHaveLength(2);
  });
});
