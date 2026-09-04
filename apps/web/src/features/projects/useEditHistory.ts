import * as React from "react";

const MERGE_WINDOW_MS = 600;
const MAX_ENTRIES = 50;

interface HistoryEntry {
  undo: () => void;
  redo: () => void;
  key: string;
  at: number;
}

// Manifest blocks are two levels deep (e.g. `mixing.stem_rebalance`,
// `mastering.eq`), so a merge key only needs to walk that far to separate
// "which control moved" — comparing by JSON rather than reference catches
// controls (routing) that rebuild a numerically identical object every tick.
function diffKey(prev: unknown, next: unknown): string | null {
  if (typeof prev !== "object" || prev === null || typeof next !== "object" || next === null) {
    return JSON.stringify(prev) === JSON.stringify(next) ? null : "value";
  }
  const a = prev as Record<string, unknown>;
  const b = next as Record<string, unknown>;
  for (const top of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (JSON.stringify(a[top]) === JSON.stringify(b[top])) continue;
    const av = a[top];
    const bv = b[top];
    if (av && bv && typeof av === "object" && typeof bv === "object") {
      const innerA = av as Record<string, unknown>;
      const innerB = bv as Record<string, unknown>;
      for (const inner of new Set([...Object.keys(innerA), ...Object.keys(innerB)])) {
        if (JSON.stringify(innerA[inner]) !== JSON.stringify(innerB[inner])) return `${top}.${inner}`;
      }
    }
    return top;
  }
  return null;
}

/** Undo/redo stack for the project page's mix edits. Every manifest funnel
 * (`updateManifest`, `updateProjectManifest`, `updateTrackManifest`) takes a
 * complete next value, so undo is just re-applying the previous one — there
 * is no diff/patch layer, only enough of a diff to name a merge key.
 *
 * `record` performs the write itself (`apply(next)`) so a call site can't
 * forget it, then pushes an undo step unless the write is a structural
 * no-op. `merge` collapses consecutive writes carrying the same key within
 * `MERGE_WINDOW_MS` (a fader drag) into one step, keeping the *oldest*
 * entry's `undo` — the first tick of a drag is always a settled value, so
 * this also protects against a `prev` that goes stale mid-drag.
 *
 * `undo`/`redo` replay the entry's raw `apply` closure directly, not
 * `record` — the caller-supplied `apply` is expected to be the low-level
 * write (e.g. a PUT bound to the track it was recorded against), not
 * something that re-enters `record`. The `applying` guard exists only to
 * make a caller mistake inert rather than corrupt the stack. */
export function useEditHistory(projectId: string | undefined, realizationKey = projectId) {
  const stacks = React.useRef(new Map<string, { past: HistoryEntry[]; future: HistoryEntry[] }>());
  const applying = React.useRef(false);
  const [, bump] = React.useReducer((n: number) => n + 1, 0);

  const stack = React.useCallback(() => {
    if (!realizationKey) return { past: [], future: [] };
    let current = stacks.current.get(realizationKey);
    if (!current) {
      current = { past: [], future: [] };
      stacks.current.set(realizationKey, current);
    }
    return current;
  }, [realizationKey]);

  React.useEffect(() => {
    stacks.current.clear();
    bump();
  }, [projectId]);

  const record = React.useCallback(<T,>(prev: T, next: T, apply: (value: T) => void, merge?: boolean) => {
    if (applying.current) return;
    apply(next);
    const key = diffKey(prev, next);
    if (key === null) return;
    const now = Date.now();
    const { past, future } = stack();
    const top = past[past.length - 1];
    if (merge && top && top.key === key && now - top.at < MERGE_WINDOW_MS) {
      top.redo = () => apply(next);
      top.at = now;
    } else {
      past.push({ undo: () => apply(prev), redo: () => apply(next), key, at: now });
      if (past.length > MAX_ENTRIES) past.shift();
    }
    future.length = 0;
    bump();
  }, [stack]);

  const undo = React.useCallback(() => {
    const { past, future } = stack();
    const entry = past.pop();
    if (!entry) return;
    applying.current = true;
    try { entry.undo(); } finally { applying.current = false; }
    future.push(entry);
    bump();
  }, [stack]);

  const redo = React.useCallback(() => {
    const { past, future } = stack();
    const entry = future.pop();
    if (!entry) return;
    applying.current = true;
    try { entry.redo(); } finally { applying.current = false; }
    past.push(entry);
    bump();
  }, [stack]);

  const clear = React.useCallback(() => {
    const { past, future } = stack();
    past.length = 0;
    future.length = 0;
    bump();
  }, [stack]);

  const { past, future } = stack();
  return { record, undo, redo, clear, canUndo: past.length > 0, canRedo: future.length > 0 };
}
