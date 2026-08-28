import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  childStemsByParent,
  normalizeStemHierarchy,
  toggleStemSelection,
} from "@/lib/stemHierarchy";
import { cn } from "@/lib/utils";
import { StemTargetButton, stemBorderClasses, stemToggleKey } from "./StemTargetButton";

const primaryStems = ["Vocals", "Bass", "Drums", "Guitar", "Piano", "Other"];

/** Expandable extraction-target tree, matching the job composer's stem
 * hierarchy without its gain and EQ controls. */
export function StemSelectorGrid({
  available,
  selected,
  onChange,
}: {
  available: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const selectedStems = normalizeStemHierarchy(selected);
  const selectedSet = new Set(selectedStems);
  const availableStems = new Set(available);
  const treeStems = [
    ...primaryStems.filter((stem) => availableStems.has(stem)),
    ...(availableStems.has("Crowd") ? ["Crowd"] : []),
    ...available.filter(
      (stem) =>
        stem !== "Crowd"
        && !primaryStems.includes(stem)
        && !Object.values(childStemsByParent).flat().includes(stem),
    ),
  ];
  const [expandedFamilies, setExpandedFamilies] = React.useState<Record<string, boolean>>({});

  const renderStemRow = (stem: string, nested = false) => {
    const children = childStemsByParent[stem];
    const expanded = expandedFamilies[stem] ?? false;
    const stemKey = stemToggleKey(stem);
    return (
      <React.Fragment key={stem}>
        <div className={cn(
          "flex items-center gap-1.5 border-l-4 bg-muted/10 px-3 py-2",
          stemBorderClasses[stemKey] || stemBorderClasses.other,
          nested && "ml-5 border-l-0 bg-transparent pl-2",
        )}>
          {children ? (
            <button
              type="button"
              aria-label={`Toggle ${stem} components`}
              aria-expanded={expanded}
              className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              onClick={() => setExpandedFamilies((current) => ({ ...current, [stem]: !expanded }))}
            >
              {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </button>
          ) : <span className="w-7 shrink-0" />}
          <StemTargetButton
            stem={stem}
            active={selectedSet.has(stem)}
            onClick={() => onChange(toggleStemSelection(selectedStems, stem))}
            className="h-8 flex-1 px-2.5"
          />
        </div>
        {children && expanded && (
          <div className="border-t border-dashed bg-muted/5 py-1">
            {children.map((child) => renderStemRow(child, true))}
          </div>
        )}
      </React.Fragment>
    );
  };

  return (
    <div className="overflow-hidden rounded-md border">
      <div className="divide-y">{treeStems.map((stem) => renderStemRow(stem))}</div>
    </div>
  );
}
