import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEditHistory } from "./useEditHistory";

describe("useEditHistory", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("merges same-key writes within the window, keeping the oldest entry's undo", () => {
    const { result } = renderHook(() => useEditHistory("p1"));
    const apply = vi.fn();

    act(() => result.current.record("a", "b", apply, true));
    act(() => result.current.record("b", "c", apply, true));

    expect(result.current.canUndo).toBe(true);
    act(() => result.current.undo());
    expect(apply).toHaveBeenLastCalledWith("a");
    expect(result.current.canUndo).toBe(false);
  });

  it("does not merge once the window has expired", () => {
    const { result } = renderHook(() => useEditHistory("p1"));
    const apply = vi.fn();

    act(() => result.current.record("a", "b", apply, true));
    act(() => vi.advanceTimersByTime(700));
    act(() => result.current.record("b", "c", apply, true));

    act(() => result.current.undo());
    expect(result.current.canUndo).toBe(true); // second entry still pending
    act(() => result.current.undo());
    expect(result.current.canUndo).toBe(false);
  });

  it("distinguishes merge keys by manifest field, not just presence of a change", () => {
    const { result } = renderHook(() => useEditHistory("p1"));
    const apply = vi.fn();

    act(() => result.current.record({ mixing: { gain: 1 } }, { mixing: { gain: 2 } }, apply, true));
    act(() => result.current.record(
      { mixing: { gain: 2 }, mastering: { eq: "flat" } },
      { mixing: { gain: 2 }, mastering: { eq: "bright" } },
      apply,
      true,
    ));

    // "mixing.gain" vs "mastering.eq" — different field, so no merge even
    // though both writes land inside the merge window.
    act(() => result.current.undo());
    expect(result.current.canUndo).toBe(true);
    act(() => result.current.undo());
    expect(result.current.canUndo).toBe(false);
  });

  it("skips recording a structural no-op but still applies it", () => {
    const { result } = renderHook(() => useEditHistory("p1"));
    const apply = vi.fn();

    act(() => result.current.record({ mixing: { gain: 1 } }, { mixing: { gain: 1 } }, apply, false));

    expect(apply).toHaveBeenCalledTimes(1);
    expect(result.current.canUndo).toBe(false);
  });

  it("the applying guard makes a reentrant record() call during undo() inert", () => {
    const { result } = renderHook(() => useEditHistory("p1"));
    let value = "start";
    const apply = (v: string) => {
      value = v;
      if (v === "a") {
        // Simulates a caller mistake: the raw apply passed to record() below
        // itself calls record() again. This only fires while replaying an
        // undo entry — v === "a" is only ever the *prev* value here.
        result.current.record("a", "z", () => {}, false);
      }
    };

    act(() => result.current.record("a", "b", apply, false));
    expect(value).toBe("b");

    act(() => result.current.undo());
    expect(value).toBe("a");
    // Without the guard, the reentrant record("a", "z", ...) call above
    // would have pushed a second entry, leaving canUndo true right after
    // undo() instead of false.
    expect(result.current.canUndo).toBe(false);
    expect(result.current.canRedo).toBe(true);

    act(() => result.current.redo());
    expect(value).toBe("b");
    expect(result.current.canUndo).toBe(true);
    expect(result.current.canRedo).toBe(false);
  });

  it("clears both stacks when the project changes", () => {
    const { result, rerender } = renderHook(({ projectId }) => useEditHistory(projectId), { initialProps: { projectId: "p1" } });
    act(() => result.current.record("a", "b", () => {}, false));
    expect(result.current.canUndo).toBe(true);

    rerender({ projectId: "p2" });
    expect(result.current.canUndo).toBe(false);
    expect(result.current.canRedo).toBe(false);
  });
});
