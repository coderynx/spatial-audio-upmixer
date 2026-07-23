import * as React from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Drum,
  Guitar,
  MicVocal,
  Music2,
  Piano,
  UsersRound,
  Waves,
  Speech,
  type LucideIcon,
} from "lucide-react";
import { NumberField, ToggleField } from "@/components/forms/fields";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { fallbackStems } from "@/lib/manifest";
import type { ManifestSectionProps } from "./types";

const primaryStems = ["Vocals", "Bass", "Drums", "Guitar", "Piano", "Other"];

const childStemsByParent: Record<string, string[]> = {
  Vocals: ["Lead Vocals", "Backing Vocals"],
  Drums: ["Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash"],
};

const stemIcons: Record<string, LucideIcon> = {
  vocals: MicVocal,
  bass: Waves,
  drums: Drum,
  kick: Drum,
  snare: Drum,
  toms: Drum,
  guitar: Guitar,
  piano: Piano,
  "hi-hat": Drum,
  ride: Drum,
  crash: Drum,
  crowd: UsersRound,
  "lead vocals": MicVocal,
  "backing vocals": Speech,
  other: Music2,
};

const stemBorderClasses: Record<string, string> = {
  vocals: "border-rose-300/80 dark:border-rose-800",
  bass: "border-teal-300/80 dark:border-teal-800",
  drums: "border-orange-300/80 dark:border-orange-800",
  kick: "border-red-300/80 dark:border-red-800",
  snare: "border-pink-300/80 dark:border-pink-800",
  toms: "border-lime-300/80 dark:border-lime-800",
  guitar: "border-emerald-300/80 dark:border-emerald-800",
  piano: "border-violet-300/80 dark:border-violet-800",
  "hi-hat": "border-yellow-300/80 dark:border-yellow-800",
  ride: "border-cyan-300/80 dark:border-cyan-800",
  crash: "border-sky-300/80 dark:border-sky-800",
  crowd: "border-blue-300/80 dark:border-blue-800",
  "lead vocals": "border-rose-300/80 dark:border-rose-800",
  "backing vocals": "border-fuchsia-300/80 dark:border-fuchsia-800",
  other: "border-slate-300/80 dark:border-slate-700",
};

const stemActiveClasses: Record<string, string> = {
  vocals: "border-rose-500 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  bass: "border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-300",
  drums:
    "border-orange-500 bg-orange-500/10 text-orange-700 dark:text-orange-300",
  kick: "border-red-500 bg-red-500/10 text-red-700 dark:text-red-300",
  snare: "border-pink-500 bg-pink-500/10 text-pink-700 dark:text-pink-300",
  toms: "border-lime-500 bg-lime-500/10 text-lime-700 dark:text-lime-300",
  guitar:
    "border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  piano:
    "border-violet-500 bg-violet-500/10 text-violet-700 dark:text-violet-300",
  "hi-hat":
    "border-yellow-500 bg-yellow-500/10 text-yellow-700 dark:text-yellow-300",
  ride: "border-cyan-500 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300",
  crash: "border-sky-500 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  crowd: "border-blue-500 bg-blue-500/10 text-blue-700 dark:text-blue-300",
  "lead vocals":
    "border-rose-500 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  "backing vocals":
    "border-fuchsia-500 bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-300",
  other: "border-slate-500 bg-slate-500/10 text-slate-700 dark:text-slate-300",
};

function getStemKey(stem: string) {
  return stem.toLowerCase();
}

function getStemIcon(stem: string) {
  return stemIcons[getStemKey(stem)] || Music2;
}

function sameStems(left: string[], right: string[]) {
  return (
    left.length === right.length &&
    left.every((stem, index) => stem === right[index])
  );
}

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

function replaceStemFamily(
  stems: string[],
  parent: string,
  replacement: string[],
) {
  const family = [parent, ...(childStemsByParent[parent] || [])];
  const firstIndex = stems.findIndex((stem) => family.includes(stem));
  const remaining = stems.filter((stem) => !family.includes(stem));
  const insertAt = firstIndex < 0 ? remaining.length : firstIndex;
  return [
    ...remaining.slice(0, insertAt),
    ...replacement,
    ...remaining.slice(insertAt),
  ];
}

function StemTargetButton({
  stem,
  active,
  onClick,
  className,
}: {
  stem: string;
  active: boolean;
  onClick: () => void;
  className?: string;
}) {
  const stemKey = getStemKey(stem);
  const StemIcon = getStemIcon(stem);
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "flex h-9 min-w-0 items-center justify-between gap-2 rounded-md border px-3 text-sm transition-colors",
        stemBorderClasses[stemKey] || stemBorderClasses.other,
        active
          ? stemActiveClasses[stemKey] || stemActiveClasses.other
          : "hover:bg-muted",
        className,
      )}
    >
      <span className="flex min-w-0 items-center gap-2 text-left">
        <StemIcon className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="truncate">{stem}</span>
      </span>
      {active && <Check className="h-4 w-4 shrink-0" />}
    </button>
  );
}

export function StemsSection({
  manifest,
  setManifest,
  configuration,
}: ManifestSectionProps) {
  const stems = configuration?.choices.stems || fallbackStems;
  const availableStems = new Set(stems);
  const selectedStems = React.useMemo(
    () => normalizeStemHierarchy(manifest.engine.stems),
    [manifest.engine.stems],
  );
  const selectedStemSet = new Set(selectedStems);
  const availablePrimaryStems = primaryStems.filter((stem) =>
    availableStems.has(stem),
  );
  const standaloneStems = stems.filter(
    (stem) =>
      stem !== "Crowd" &&
      !primaryStems.includes(stem) &&
      !Object.values(childStemsByParent).flat().includes(stem),
  );
  const treeStems = [
    ...availablePrimaryStems,
    ...(availableStems.has("Crowd") ? ["Crowd"] : []),
    ...standaloneStems,
  ];
  const [expandedFamilies, setExpandedFamilies] = React.useState<
    Record<string, boolean>
  >({});
  const updateStems = React.useCallback(
    (nextStems: string[]) => {
      const normalized = normalizeStemHierarchy(nextStems);
      setManifest((current) => ({
        ...current,
        engine: { ...current.engine, stems: normalized },
      }));
    },
    [setManifest],
  );

  React.useEffect(() => {
    if (!sameStems(manifest.engine.stems, selectedStems))
      updateStems(selectedStems);
  }, [manifest.engine.stems, selectedStems, updateStems]);

  const toggleStem = (stem: string) => {
    const children = childStemsByParent[stem];
    const active = selectedStemSet.has(stem);
    if (children && selectedStems.some((item) => children.includes(item))) {
      updateStems(replaceStemFamily(selectedStems, stem, [stem]));
      return;
    }
    if (active) {
      updateStems(selectedStems.filter((item) => item !== stem));
      return;
    }
    const parent = Object.entries(childStemsByParent).find(([, values]) =>
      values.includes(stem),
    )?.[0];
    if (parent) {
      const selectedChildren = (childStemsByParent[parent] || []).filter(
        (child) => selectedStemSet.has(child),
      );
      updateStems(
        replaceStemFamily(selectedStems, parent, [...selectedChildren, stem]),
      );
      return;
    }
    updateStems(
      children
        ? replaceStemFamily(selectedStems, stem, [stem])
        : [...selectedStems, stem],
    );
  };

  const updateStemGain = (stem: string, value: number) =>
    setManifest((current) => ({
      ...current,
      mixing: {
        ...current.mixing,
        stem_rebalance: { ...current.mixing.stem_rebalance, [stem]: value },
      },
    }));

  const updateStemEq = (stem: string, profile: string) =>
    setManifest((current) => {
      const stem_eq = { ...current.mixing.stem_eq };
      if (profile === "none") delete stem_eq[stem];
      else stem_eq[stem] = profile;
      return { ...current, mixing: { ...current.mixing, stem_eq } };
    });

  const renderStemRow = (stem: string, nested = false) => {
    const stemKey = getStemKey(stem);
    const children = childStemsByParent[stem];
    const expanded = expandedFamilies[stem] ?? false;
    const active = selectedStemSet.has(stem);
    const gain = manifest.mixing.stem_rebalance[stem] ?? 0;
    return (
      <React.Fragment key={stem}>
        <div
          className={cn(
            "grid gap-2 border-l-4 bg-muted/10 px-3 py-2 sm:grid-cols-[minmax(150px,0.8fr)_minmax(190px,1.4fr)_minmax(140px,0.8fr)] sm:items-center",
            stemBorderClasses[stemKey] || stemBorderClasses.other,
            nested && "ml-5 border-l-0 bg-transparent pl-2",
          )}
        >
          <div className="flex min-w-0 items-center gap-1.5">
            {children ? (
              <button
                type="button"
                aria-label={`Toggle ${stem} components`}
                aria-expanded={expanded}
                className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() =>
                  setExpandedFamilies((current) => ({
                    ...current,
                    [stem]: !expanded,
                  }))
                }
              >
                {expanded ? (
                  <ChevronDown className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <ChevronRight className="h-4 w-4" aria-hidden="true" />
                )}
              </button>
            ) : (
              <span className="w-7 shrink-0" />
            )}
            <StemTargetButton
              stem={stem}
              active={active}
              onClick={() => toggleStem(stem)}
              className="h-8 flex-1 px-2.5"
            />
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-xs text-muted-foreground">Gain</span>
            <Slider
              aria-label={`${stem} gain`}
              value={[gain]}
              min={-6}
              max={6}
              step={0.1}
              disabled={!active}
              onValueChange={([value]) => updateStemGain(stem, value)}
            />
            <span className="w-14 shrink-0 rounded bg-muted px-1.5 py-0.5 text-right font-mono text-xs tabular-nums">
              {gain.toFixed(1)} dB
            </span>
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-xs text-muted-foreground">EQ</span>
            <select
              aria-label={`${stem} EQ`}
              disabled={!active}
              value={manifest.mixing.stem_eq[stem] || "none"}
              onChange={(event) => updateStemEq(stem, event.target.value)}
              className="flex h-8 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-xs shadow-sm focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="none">None</option>
              {(configuration?.choices.stem_eq_profiles || []).map(
                (profile) => (
                  <option key={profile} value={profile}>
                    {profile}
                  </option>
                ),
              )}
            </select>
          </div>
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
    <div className="space-y-3">
      <section className="overflow-hidden rounded-md border">
        <div className="flex items-start justify-between gap-3">
          <div className="px-3 py-2.5">
            <p className="text-sm font-medium">Stem mixer</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Activate targets, then adjust their gain and EQ.
            </p>
          </div>
          <span className="m-3 shrink-0 rounded-full bg-muted px-2 py-1 text-xs tabular-nums text-muted-foreground">
            {selectedStems.length} selected
          </span>
        </div>
        <div className="divide-y border-t">{treeStems.map((stem) => renderStemRow(stem))}</div>

        {selectedStemSet.has("Crowd") && (
          <p className="border-t px-3 py-2 text-xs text-muted-foreground">
            {selectedStems.length === 1
              ? "This job keeps Crowd only. Crowd-free content is not a selectable final stem."
              : "Crowd stays in the mix. Crowd-free residual feeds remaining targets, then is discarded."}
          </p>
        )}
      </section>

      <details className="rounded-md border">
        <summary className="cursor-pointer px-3 py-2.5 text-sm font-medium">
          Separation performance
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            Silence skip and inference tuning
          </span>
        </summary>
        <div className="space-y-3 border-t p-3">
          <ToggleField
            label="Silence skip"
            description="Skip separation over long silent regions. Cache keys include silence settings."
            checked={manifest.engine.stem_silence_skip}
            onChange={(stem_silence_skip) =>
              setManifest({
                ...manifest,
                engine: { ...manifest.engine, stem_silence_skip },
              })
            }
          />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <NumberField
              label="Batch size"
              value={manifest.engine.stem_batch_size}
              min={1}
              step={1}
              hint="Blank selects backend-aware batching."
              onChange={(stem_batch_size) =>
                setManifest({
                  ...manifest,
                  engine: { ...manifest.engine, stem_batch_size },
                })
              }
            />
            <NumberField
              label="Silence threshold"
              value={manifest.engine.stem_silence_threshold_db}
              step={1}
              suffix="dB"
              onChange={(value) => {
                if (value != null)
                  setManifest({
                    ...manifest,
                    engine: {
                      ...manifest.engine,
                      stem_silence_threshold_db: value,
                    },
                  });
              }}
            />
            <NumberField
              label="Minimum silence"
              value={manifest.engine.stem_silence_min_duration_s}
              min={0}
              step={0.1}
              suffix="s"
              onChange={(value) => {
                if (value != null)
                  setManifest({
                    ...manifest,
                    engine: {
                      ...manifest.engine,
                      stem_silence_min_duration_s: value,
                    },
                  });
              }}
            />
            <NumberField
              label="Crossfade"
              value={manifest.engine.stem_silence_crossfade_ms}
              min={0}
              step={1}
              suffix="ms"
              onChange={(value) => {
                if (value != null)
                  setManifest({
                    ...manifest,
                    engine: {
                      ...manifest.engine,
                      stem_silence_crossfade_ms: value,
                    },
                  });
              }}
            />
            <NumberField
              label="Silence padding"
              value={manifest.engine.stem_silence_pad_ms}
              min={0}
              step={1}
              suffix="ms"
              onChange={(value) => {
                if (value != null)
                  setManifest({
                    ...manifest,
                    engine: {
                      ...manifest.engine,
                      stem_silence_pad_ms: value,
                    },
                  });
              }}
            />
          </div>
        </div>
      </details>
    </div>
  );
}
