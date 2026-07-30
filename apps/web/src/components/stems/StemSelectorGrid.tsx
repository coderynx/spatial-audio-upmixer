import { normalizeStemHierarchy, toggleStemSelection } from "@/lib/stemHierarchy";
import { StemTargetButton } from "./StemTargetButton";

/** Flat grid of stem toggle pills — used where only the extraction target
 * list matters (no per-stem gain/EQ, which only exist once a track has been
 * prepared). `StemsSection` in the job composer covers the richer tree with
 * gain/EQ for an already-prepared context. */
export function StemSelectorGrid({
  available,
  selected,
  onChange,
}: {
  available: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const selectedSet = new Set(normalizeStemHierarchy(selected));
  return (
    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
      {available.map((stem) => (
        <StemTargetButton
          key={stem}
          stem={stem}
          active={selectedSet.has(stem)}
          onClick={() => onChange(toggleStemSelection(selected, stem))}
        />
      ))}
    </div>
  );
}
