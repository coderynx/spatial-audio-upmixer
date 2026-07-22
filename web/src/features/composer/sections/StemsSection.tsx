import { Check } from "lucide-react";
import {
  NumberField,
  SelectField,
  ToggleField,
} from "@/components/forms/fields";
import { cn } from "@/lib/utils";
import { fallbackStems } from "@/lib/manifest";
import type { ManifestSectionProps } from "./types";

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
          return (
            <button
              key={stem}
              type="button"
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
                "flex items-center justify-between rounded-md border p-3 text-sm transition-colors",
                active
                  ? "border-primary bg-primary/5 text-primary"
                  : "hover:bg-muted",
              )}
            >
              <span>{stem}</span>
              {active && <Check className="h-4 w-4" />}
            </button>
          );
        })}
      </div>
      {manifest.engine.stems.length > 0 && (
        <div className="grid gap-3 border-t pt-4 sm:grid-cols-2">
          {manifest.engine.stems.map((stem) => (
            <div
              key={stem}
              className="grid gap-3 rounded-md border bg-muted/20 p-3 sm:grid-cols-2"
            >
              <NumberField
                label={`${stem} gain`}
                value={manifest.mixing.stem_rebalance[stem] ?? 0}
                step={0.1}
                suffix="dB"
                onChange={(value) => {
                  if (value != null)
                    setManifest({
                      ...manifest,
                      mixing: {
                        ...manifest.mixing,
                        stem_rebalance: {
                          ...manifest.mixing.stem_rebalance,
                          [stem]: value,
                        },
                      },
                    });
                }}
              />
              <SelectField
                label={`${stem} EQ`}
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
          ))}
        </div>
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
