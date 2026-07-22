import {
  Check,
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
import {
  NumberField,
  SelectField,
  SliderField,
  ToggleField,
} from "@/components/forms/fields";
import { cn } from "@/lib/utils";
import { fallbackStems } from "@/lib/manifest";
import type { ManifestSectionProps } from "./types";

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

export function StemsSection({
  manifest,
  setManifest,
  configuration,
}: ManifestSectionProps) {
  const stems = configuration?.choices.stems || fallbackStems;
  return (
    <div className="space-y-4 rounded-md border p-4">
      <div>
        <p className="text-sm font-medium">Separation targets</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Compatible cached stems are shared across remix jobs automatically.
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {stems.map((stem) => {
          const active = manifest.engine.stems.includes(stem);
          const stemKey = getStemKey(stem);
          const StemIcon = getStemIcon(stem);
          return (
            <button
              key={stem}
              type="button"
              aria-pressed={active}
              onClick={() =>
                setManifest({
                  ...manifest,
                  engine: {
                    ...manifest.engine,
                    stems: active
                      ? manifest.engine.stems.filter((item) => item !== stem)
                      : [...manifest.engine.stems, stem],
                  },
                })
              }
              className={cn(
                "flex min-w-0 items-center justify-between gap-2 rounded-md border p-3 text-sm transition-colors",
                stemBorderClasses[stemKey] || stemBorderClasses.other,
                active
                  ? stemActiveClasses[stemKey] || stemActiveClasses.other
                  : "hover:bg-muted",
              )}
            >
              <span className="flex min-w-0 items-center gap-2 text-left">
                <StemIcon className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="truncate">{stem}</span>
              </span>
              {active && <Check className="h-4 w-4 shrink-0" />}
            </button>
          );
        })}
      </div>
      {manifest.engine.stems.length > 0 && (
        <section className="space-y-3 border-t pt-4">
          <div>
            <p className="text-sm font-medium">Selected stem mix</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Adjust each selected stem before spatial routing.
            </p>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {manifest.engine.stems.map((stem) => {
              const stemKey = getStemKey(stem);
              const StemIcon = getStemIcon(stem);
              return (
                <div
                  key={stem}
                  className={cn(
                    "grid gap-4 rounded-md border bg-muted/20 p-4",
                    stemBorderClasses[stemKey] || stemBorderClasses.other,
                  )}
                >
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <StemIcon className="h-4 w-4" aria-hidden="true" />
                    {stem}
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <SliderField
                      label="Gain"
                      value={manifest.mixing.stem_rebalance[stem] ?? 0}
                      min={-6}
                      max={6}
                      step={0.1}
                      suffix=" dB"
                      onChange={(value) =>
                        setManifest({
                          ...manifest,
                          mixing: {
                            ...manifest.mixing,
                            stem_rebalance: {
                              ...manifest.mixing.stem_rebalance,
                              [stem]: value,
                            },
                          },
                        })
                      }
                    />
                    <SelectField
                      label="EQ"
                      value={manifest.mixing.stem_eq[stem] || "none"}
                      onChange={(profile) => {
                        const stem_eq = { ...manifest.mixing.stem_eq };
                        if (profile === "none") delete stem_eq[stem];
                        else stem_eq[stem] = profile;
                        setManifest({
                          ...manifest,
                          mixing: { ...manifest.mixing, stem_eq },
                        });
                      }}
                      options={[
                        { value: "none", label: "None" },
                        ...(configuration?.choices.stem_eq_profiles || []).map(
                          (value) => ({ value, label: value }),
                        ),
                      ]}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}
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
                engine: { ...manifest.engine, stem_silence_pad_ms: value },
              });
          }}
        />
      </div>
    </div>
  );
}
