import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { NumberField, SelectField, SliderField, SwitchRow, ToggleField } from "@/components/forms/fields";
import { StemTargetButton, stemBorderClasses, stemToggleKey } from "@/components/stems/StemTargetButton";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { fallbackStems } from "@/lib/manifest";
import { childStemsByParent, normalizeStemHierarchy, replaceStemFamily } from "@/lib/stemHierarchy";
import type { ManifestSectionProps } from "./types";

export { normalizeStemHierarchy } from "@/lib/stemHierarchy";

const primaryStems = ["Vocals", "Bass", "Drums", "Guitar", "Piano", "Other"];

function sameStems(left: string[], right: string[]) {
  return (
    left.length === right.length &&
    left.every((stem, index) => stem === right[index])
  );
}

export function StemsSection({
  manifest,
  setManifest,
  configuration,
}: ManifestSectionProps) {
  const stems = configuration?.choices.stems || fallbackStems;
  const availableStems = new Set(stems);
  const referenceModels =
    configuration?.choices.stem_phase_fix_reference_models ||
    [manifest.engine.stem_phase_fix_reference_model];
  const debleedModels =
    configuration?.choices.stem_debleed_models ||
    [manifest.engine.stem_debleed_model];
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
    const stemKey = stemToggleKey(stem);
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
              {(configuration?.choices.stem_eq_profiles || []).filter((profile) => profile !== "flat").map(
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
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
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

      <details className="rounded-md border">
        <summary className="cursor-pointer px-3 py-2.5 text-sm font-medium">
          Bleed reduction
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            Phase-fixer and debleed passes on separated stems
          </span>
        </summary>
        <div className="space-y-3 border-t p-3">
          <ToggleField
            label="Bleed reduction"
            description="Phase-fixer during separation, on by default for diffuse/surround-oriented stems (not affected by 3D placement). Debleed is opt-in per stem. Off overall by default; changing it re-separates stems."
            checked={manifest.engine.stem_bleed_reduction}
            onChange={(stem_bleed_reduction) =>
              setManifest({
                ...manifest,
                engine: { ...manifest.engine, stem_bleed_reduction },
              })
            }
          />
          {manifest.engine.stem_bleed_reduction && (
            <>
              <div className="grid gap-2 sm:grid-cols-2">
                <SelectField
                  label="Phase-fix reference model"
                  value={manifest.engine.stem_phase_fix_reference_model}
                  options={referenceModels.map((model) => ({
                    value: model,
                    label: model,
                  }))}
                  onChange={(stem_phase_fix_reference_model) =>
                    setManifest({
                      ...manifest,
                      engine: {
                        ...manifest.engine,
                        stem_phase_fix_reference_model,
                      },
                    })
                  }
                />
                <SelectField
                  label="Debleed model"
                  value={manifest.engine.stem_debleed_model}
                  hint="Opt-in per stem via stem_debleed; one inference per stem."
                  options={debleedModels.map((model) => ({
                    value: model,
                    label: model,
                  }))}
                  onChange={(stem_debleed_model) =>
                    setManifest({
                      ...manifest,
                      engine: { ...manifest.engine, stem_debleed_model },
                    })
                  }
                />
              </div>
              <div className="grid gap-2 sm:grid-cols-3">
                <NumberField
                  label="Phase-fix low cutoff"
                  value={manifest.engine.stem_phase_fix_low_hz}
                  min={1}
                  step={50}
                  suffix="Hz"
                  onChange={(value) => {
                    if (value != null)
                      setManifest({
                        ...manifest,
                        engine: {
                          ...manifest.engine,
                          stem_phase_fix_low_hz: value,
                        },
                      });
                  }}
                />
                <NumberField
                  label="Phase-fix high cutoff"
                  value={manifest.engine.stem_phase_fix_high_hz}
                  min={1}
                  step={100}
                  suffix="Hz"
                  onChange={(value) => {
                    if (value != null)
                      setManifest({
                        ...manifest,
                        engine: {
                          ...manifest.engine,
                          stem_phase_fix_high_hz: value,
                        },
                      });
                  }}
                />
                <SliderField
                  label="Phase-fix scale"
                  value={manifest.engine.stem_phase_fix_scale}
                  min={0.05}
                  max={1}
                  step={0.05}
                  onChange={(stem_phase_fix_scale) =>
                    setManifest({
                      ...manifest,
                      engine: { ...manifest.engine, stem_phase_fix_scale },
                    })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <p className="text-[11px] font-medium uppercase tracking-[.08em] text-muted-foreground">
                  Debleed stems
                </p>
                <p className="text-[11px] text-muted-foreground">
                  Opt in per stem — each adds one model inference at separation.
                </p>
                <div className="grid gap-x-3 sm:grid-cols-2">
                  {manifest.engine.stems.map((stem) => (
                    <SwitchRow
                      key={stem}
                      label={stem}
                      checked={Boolean(manifest.engine.stem_debleed?.[stem])}
                      onChange={(on) => {
                        const next = { ...(manifest.engine.stem_debleed || {}) };
                        if (on) next[stem] = true;
                        else delete next[stem];
                        setManifest({
                          ...manifest,
                          engine: { ...manifest.engine, stem_debleed: next },
                        });
                      }}
                    />
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </details>
    </div>
  );
}
