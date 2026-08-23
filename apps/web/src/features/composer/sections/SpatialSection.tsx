import { NumberField, SliderField } from "@/components/forms/fields";
import type { ManifestSectionProps } from "./types";

export function SpatialSection({
  manifest,
  setManifest,
}: ManifestSectionProps) {
  return (
    <div className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2">
      {(
        ["center_gain", "surround_gain", "back_gain", "height_gain"] as const
      ).map((key) => (
        <SliderField
          key={key}
          label={key
            .replace("_", " ")
            .replace(/^./, (value) => value.toUpperCase())}
          value={manifest.routing[key]}
          min={0}
          max={1.5}
          step={0.01}
          onChange={(value) =>
            setManifest({
              ...manifest,
              routing: { ...manifest.routing, [key]: value },
            })
          }
        />
      ))}
      <SliderField
        label="Source anchor"
        value={manifest.mixing.stem_source_anchor_strength}
        min={0}
        max={1}
        step={0.01}
        onChange={(stem_source_anchor_strength) =>
          setManifest({
            ...manifest,
            mixing: { ...manifest.mixing, stem_source_anchor_strength },
          })
        }
      />
      <SliderField
        label="LFE gain"
        value={manifest.routing.lfe_gain}
        min={0}
        max={1.5}
        step={0.01}
        onChange={(lfe_gain) =>
          setManifest({
            ...manifest,
            routing: { ...manifest.routing, lfe_gain },
          })
        }
      />
      <NumberField
        label="LFE cutoff"
        value={manifest.routing.lfe_cutoff}
        min={20}
        step={1}
        suffix="Hz"
        onChange={(value) => {
          if (value != null)
            setManifest({
              ...manifest,
              routing: { ...manifest.routing, lfe_cutoff: value },
            });
        }}
      />
      <SliderField
        label="Height low rolloff"
        value={manifest.routing.height_low_rolloff_gain}
        min={0}
        max={2}
        step={0.01}
        onChange={(height_low_rolloff_gain) =>
          setManifest({
            ...manifest,
            routing: { ...manifest.routing, height_low_rolloff_gain },
          })
        }
      />
      <SliderField
        label="Height high shelf"
        value={manifest.routing.height_high_shelf_gain}
        min={0}
        max={3}
        step={0.01}
        onChange={(height_high_shelf_gain) =>
          setManifest({
            ...manifest,
            routing: { ...manifest.routing, height_high_shelf_gain },
          })
        }
      />
      <SliderField
        label="Height directional band"
        value={manifest.routing.height_directional_band_gain}
        min={1}
        max={2}
        step={0.01}
        onChange={(height_directional_band_gain) =>
          setManifest({
            ...manifest,
            routing: { ...manifest.routing, height_directional_band_gain },
          })
        }
      />
    </div>
  );
}
