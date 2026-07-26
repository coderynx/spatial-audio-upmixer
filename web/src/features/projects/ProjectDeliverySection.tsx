import { SelectField, ToggleField } from "@/components/forms/fields";
import type { Configuration } from "@/api";
import type { Manifest } from "@/lib/manifest";

// Delivery/encoding controls for a project. Deliberately not a reuse of the
// Job Composer's OutputSection/ProcessingSection: those also carry
// `engine.mode` (forced server-side to "stem" for projects) and test-render
// fields (`processing.preview`/`fft_size`/`block_size`/`preview_start`) that
// don't apply — a project already has a real client-side live preview.
export function ProjectDeliverySection({
  manifest,
  configuration,
  onChange,
}: {
  manifest: Manifest;
  configuration: Configuration | null;
  onChange: (next: Manifest) => void;
}) {
  const choices = configuration?.choices;
  const binauralBeds = choices?.binaural_beds || ["5.1.4", "7.1.2", "7.1.4"];
  const bedSupported = binauralBeds.includes(manifest.mixing.channel_layout);
  return (
    <div className="grid gap-4 rounded-md border p-4 sm:grid-cols-2">
      <SelectField
        label="Format"
        value={manifest.format.type}
        onChange={(type) =>
          onChange({ ...manifest, format: { ...manifest.format, type } })
        }
        options={(choices?.output_types || ["wav", "adm-bwf", "binaural"]).map((value) => ({
          value,
          label: value === "adm-bwf" ? "ADM-BWF" : value === "binaural" ? "Binaural (headphone stereo)" : "Multichannel WAV",
          disabled: value === "binaural" && !bedSupported,
        }))}
        hint={
          manifest.format.type === "binaural"
            ? `Renders speaker layout ${manifest.mixing.channel_layout}'s bed through the Spatial Audio Engine as headphone stereo — set the layout in Project settings.`
            : !bedSupported
              ? `Binaural is disabled: set the speaker layout in Project settings to ${binauralBeds.join(", ")} to enable it.`
              : undefined
        }
      />
      {manifest.format.type === "binaural" && (
        <SelectField
          label="Spatial Audio Engine profile"
          value={manifest.format.binaural.profile}
          onChange={(profile) => onChange({
            ...manifest,
            format: { ...manifest.format, binaural: { ...manifest.format.binaural, profile } },
          })}
          options={(choices?.binaural_profiles || ["studio", "listening", "flat"]).map((value) => ({
            value,
            label: value.charAt(0).toUpperCase() + value.slice(1),
          }))}
          hint="Studio = neutral monitoring room. Listening = Apple Music Atmos-style enhance. Flat = anechoic reference. Matches the in-preview Spatial Audio Engine selector 1:1."
        />
      )}
      <SelectField
        label="Sample rate"
        value={String(manifest.format.sample_rate)}
        onChange={(sample_rate) =>
          onChange({
            ...manifest,
            format: { ...manifest.format, sample_rate: Number(sample_rate) },
          })
        }
        options={(choices?.sample_rates || [44100, 48000, 88200, 96000, 192000]).map(
          (value) => ({ value: String(value), label: `${value / 1000} kHz` }),
        )}
      />
      <SelectField
        label="Bit depth"
        value={manifest.format.subtype}
        onChange={(subtype) =>
          onChange({ ...manifest, format: { ...manifest.format, subtype } })
        }
        options={(choices?.output_subtypes || ["PCM_16", "PCM_24", "PCM_32", "FLOAT"]).map(
          (value) => ({ value, label: value }),
        )}
        hint={
          manifest.format.type === "adm-bwf"
            ? "ADM-BWF requires PCM_24 at 48 or 96 kHz."
            : undefined
        }
      />
      <ToggleField
        label="Normalize output"
        description="Apply output peak normalization before export."
        checked={manifest.processing.normalize_output}
        onChange={(normalize_output) =>
          onChange({
            ...manifest,
            processing: { ...manifest.processing, normalize_output },
          })
        }
      />
      <ToggleField
        label="Stereo downmix"
        description="Write an ITU-R BS.775-compatible stereo companion file."
        checked={manifest.format.downmix?.enabled ?? false}
        onChange={(enabled) =>
          onChange({
            ...manifest,
            format: {
              ...manifest.format,
              downmix: { ...(manifest.format.downmix || { surround_coeff: 0.7071 }), enabled },
            },
          })
        }
      />
      {(manifest.format.downmix?.enabled ?? false) && <SelectField
        label="Downmix surround coefficient"
        value={String(manifest.format.downmix?.surround_coeff ?? 0.7071)}
        onChange={(surround_coeff) => onChange({
          ...manifest,
          format: {
            ...manifest.format,
            downmix: { ...(manifest.format.downmix || { enabled: true }), surround_coeff: Number(surround_coeff) },
          },
        })}
        options={[0.7071, 0.5, 0].map((value) => ({ value: String(value), label: String(value) }))}
      />}
    </div>
  );
}
