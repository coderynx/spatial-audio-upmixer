import {
  NumberField,
  SelectField,
  SliderField,
  ToggleField,
} from "@/components/forms/fields";
import type { ManifestSectionProps } from "./types";

export function MasteringSection({
  manifest,
  setManifest,
  configuration,
}: ManifestSectionProps) {
  const choices = configuration?.choices;
  return (
    <div className="grid gap-5 rounded-md border p-4 sm:grid-cols-2">
      <ToggleField
        label="Loudness normalization"
        description="BS.1770 integrated loudness normalization."
        checked={manifest.mastering.loudness.normalize}
        onChange={(normalize) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              loudness: { ...manifest.mastering.loudness, normalize },
            },
          })
        }
      />
      <SliderField
        label="Loudness target"
        value={manifest.mastering.loudness.target}
        min={-30}
        max={-10}
        step={0.5}
        suffix=" LKFS"
        onChange={(target) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              loudness: { ...manifest.mastering.loudness, target },
            },
          })
        }
      />
      <SliderField
        label="True-peak ceiling"
        value={manifest.mastering.loudness.max_tp}
        min={-6}
        max={0}
        step={0.1}
        suffix=" dBTP"
        onChange={(max_tp) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              loudness: { ...manifest.mastering.loudness, max_tp },
            },
          })
        }
      />
      <SelectField
        label="Spectral EQ"
        value={manifest.mastering.eq.profile || "none"}
        onChange={(profile) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              eq: {
                ...manifest.mastering.eq,
                profile: profile === "none" ? null : profile,
              },
            },
          })
        }
        options={["none", ...(choices?.eq_profiles || [])].map((value) => ({
          value,
          label: value
            .split("-")
            .map((part) => part[0].toUpperCase() + part.slice(1))
            .join(" "),
        }))}
      />
      <SliderField
        label="EQ strength"
        value={manifest.mastering.eq.strength}
        min={0}
        max={1}
        step={0.01}
        onChange={(strength) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              eq: { ...manifest.mastering.eq, strength },
            },
          })
        }
      />
      <SelectField
        label="Bus compressor"
        value={manifest.mastering.compressor.profile || "none"}
        onChange={(profile) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              compressor: {
                ...manifest.mastering.compressor,
                profile: profile === "none" ? null : profile,
              },
            },
          })
        }
        options={["none", ...(choices?.compressor_profiles || [])].map(
          (value) => ({
            value,
            label: value[0].toUpperCase() + value.slice(1),
          }),
        )}
      />
      {(
        [
          ["threshold_db", "Compressor threshold", "dB", 0.1],
          ["ratio", "Compressor ratio", "", 0.1],
          ["attack_ms", "Compressor attack", "ms", 1],
          ["release_ms", "Compressor release", "ms", 1],
          ["knee_db", "Compressor knee", "dB", 0.1],
          ["makeup_db", "Compressor makeup", "dB", 0.1],
        ] as const
      ).map(([key, label, suffix, step]) => (
        <NumberField
          key={key}
          label={label}
          value={manifest.mastering.compressor[key]}
          step={step}
          suffix={suffix || undefined}
          hint="Blank uses profile value."
          onChange={(value) =>
            setManifest({
              ...manifest,
              mastering: {
                ...manifest.mastering,
                compressor: { ...manifest.mastering.compressor, [key]: value },
              },
            })
          }
        />
      ))}
      <SelectField
        label="Bass control"
        value={manifest.mastering.bass.profile || "none"}
        onChange={(profile) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              bass: {
                ...manifest.mastering.bass,
                profile: profile === "none" ? null : profile,
              },
            },
          })
        }
        options={["none", ...(choices?.bass_profiles || [])].map((value) => ({
          value,
          label: value[0].toUpperCase() + value.slice(1),
        }))}
      />
      {(
        [
          ["sub_gain_db", "Sub gain", "dB", 0.1],
          ["mid_gain_db", "Mid-bass gain", "dB", 0.1],
          ["mono_cutoff_hz", "Mono cutoff", "Hz", 1],
          ["lfe_gain_db", "Mastering LFE trim", "dB", 0.1],
        ] as const
      ).map(([key, label, suffix, step]) => (
        <NumberField
          key={key}
          label={label}
          value={manifest.mastering.bass[key]}
          step={step}
          suffix={suffix}
          hint="Blank uses profile value."
          onChange={(value) =>
            setManifest({
              ...manifest,
              mastering: {
                ...manifest.mastering,
                bass: { ...manifest.mastering.bass, [key]: value },
              },
            })
          }
        />
      ))}
      <ToggleField
        label="Bass exciter"
        description="Add low-frequency harmonics before loudness normalization."
        checked={manifest.mastering.bass.excite}
        onChange={(excite) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              bass: { ...manifest.mastering.bass, excite },
            },
          })
        }
      />
    </div>
  );
}
