import {
  NumberField,
  SelectField,
  SliderField,
  ToggleField,
} from "@/components/forms/fields";
import type { ManifestSectionProps } from "./types";

export function SpatialSection({
  manifest,
  setManifest,
  configuration,
}: ManifestSectionProps) {
  const profiles = configuration?.choices.spatial_profiles || [
    "auto",
    "balanced",
    "intimate",
    "rhythmic",
    "spacious",
    "live",
    "detailed",
  ];
  return (
    <div className="grid gap-5 rounded-md border p-4 sm:grid-cols-2">
      <SelectField
        label="Spatial profile"
        value={manifest.mixing.spatial.profile}
        onChange={(profile) =>
          setManifest({
            ...manifest,
            mixing: {
              ...manifest.mixing,
              spatial: { ...manifest.mixing.spatial, profile },
            },
          })
        }
        options={profiles.map((value) => ({
          value,
          label: value[0].toUpperCase() + value.slice(1),
        }))}
      />
      <SliderField
        label="Spatial intensity"
        value={manifest.mixing.spatial.intensity}
        min={0}
        max={1}
        step={0.01}
        onChange={(intensity) =>
          setManifest({
            ...manifest,
            mixing: {
              ...manifest.mixing,
              spatial: { ...manifest.mixing.spatial, intensity },
            },
          })
        }
      />
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
      <ToggleField
        label="Content pre-analysis"
        description="Analyze each track before routing to select a content-led plan."
        checked={manifest.mixing.spatial.preanalyze}
        onChange={(preanalyze) =>
          setManifest({
            ...manifest,
            mixing: {
              ...manifest.mixing,
              spatial: { ...manifest.mixing.spatial, preanalyze },
            },
          })
        }
      />
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
        label="Center extraction"
        value={manifest.routing.center_extraction_gain}
        min={0}
        max={1.5}
        step={0.01}
        onChange={(center_extraction_gain) =>
          setManifest({
            ...manifest,
            routing: { ...manifest.routing, center_extraction_gain },
          })
        }
      />
      <SliderField
        label="Center attenuation"
        value={manifest.routing.center_attenuation}
        min={0}
        max={1}
        step={0.01}
        onChange={(center_attenuation) =>
          setManifest({
            ...manifest,
            routing: { ...manifest.routing, center_attenuation },
          })
        }
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
        label="Content mix strength"
        value={manifest.routing.content_mix_strength}
        min={0}
        max={2}
        step={0.01}
        onChange={(content_mix_strength) =>
          setManifest({
            ...manifest,
            routing: { ...manifest.routing, content_mix_strength },
          })
        }
      />
    </div>
  );
}
