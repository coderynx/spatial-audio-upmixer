export const childStemsByParent: Record<string, string[]> = {
  Vocals: ["Lead Vocals", "Backing Vocals"],
  Drums: ["Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash"],
};

export function replaceStemFamily(stems: string[], parent: string, replacement: string[]) {
  const family = [parent, ...(childStemsByParent[parent] || [])];
  const firstIndex = stems.findIndex((stem) => family.includes(stem));
  const remaining = stems.filter((stem) => !family.includes(stem));
  const insertAt = firstIndex < 0 ? remaining.length : firstIndex;
  return [...remaining.slice(0, insertAt), ...replacement, ...remaining.slice(insertAt)];
}

/** Keeps a parent stem only when none of its children is also selected. */
export function normalizeStemHierarchy(stems: string[]) {
  const deduplicated = Array.from(new Set(stems));
  return Object.entries(childStemsByParent).reduce(
    (result, [parent, children]) =>
      result.some((stem) => children.includes(stem))
        ? result.filter((stem) => stem !== parent)
        : result,
    deduplicated,
  );
}

/** Toggle one stem in a selection, keeping a parent/child family mutually
 * exclusive (selecting a child drops its parent and vice versa). */
export function toggleStemSelection(selected: string[], stem: string): string[] {
  const children = childStemsByParent[stem];
  const selectedSet = new Set(selected);
  const active = selectedSet.has(stem);
  if (children && selected.some((item) => children.includes(item))) {
    return replaceStemFamily(selected, stem, [stem]);
  }
  if (active) return selected.filter((item) => item !== stem);
  const parent = Object.entries(childStemsByParent).find(([, values]) => values.includes(stem))?.[0];
  if (parent) {
    const selectedChildren = (childStemsByParent[parent] || []).filter((child) => selectedSet.has(child));
    return replaceStemFamily(selected, parent, [...selectedChildren, stem]);
  }
  return children ? replaceStemFamily(selected, stem, [stem]) : [...selected, stem];
}
