import { SelectField, ToggleField } from "@/components/forms/fields";
import type { ManifestSectionProps } from "./types";

export function OutputSection({
  manifest,
  setManifest,
  configuration,
}: ManifestSectionProps) {
  const choices = configuration?.choices;
  const separation = configuration?.capabilities.stem_separation;
  return (
    <div className="grid gap-4 rounded-md border p-4 sm:grid-cols-2">
      <SelectField
        label="Processing engine"
        value={manifest.engine.mode}
        onChange={(mode) =>
          setManifest({ ...manifest, engine: { ...manifest.engine, mode } })
        }
        options={(choices?.modes || ["realtime", "stem"]).map((value) => ({
          value,
          label: value === "stem" ? "Stem separation" : "Realtime",
          disabled: value === "stem" && separation?.available === false,
        }))}
        hint={
          separation?.accelerator_issue ||
          (separation?.available
            ? `Stem backend: ${separation.backend || "CPU"}`
            : separation?.install_message || undefined)
        }
      />
      <ToggleField
        label="Stereo downmix"
        description="Write an ITU-R BS.775-compatible stereo companion file."
        checked={manifest.format.downmix?.enabled ?? false}
        onChange={(enabled) => setManifest({
          ...manifest,
          format: {
            ...manifest.format,
            downmix: { ...(manifest.format.downmix || { surround_coeff: 0.7071 }), enabled },
          },
        })}
      />
      {(manifest.format.downmix?.enabled ?? false) && <SelectField
        label="Downmix surround coefficient"
        value={String(manifest.format.downmix?.surround_coeff ?? 0.7071)}
        onChange={(surround_coeff) => setManifest({
          ...manifest,
          format: {
            ...manifest.format,
            downmix: { ...(manifest.format.downmix || { enabled: true }), surround_coeff: Number(surround_coeff) },
          },
        })}
        options={[0.7071, 0.5, 0].map((value) => ({ value: String(value), label: String(value) }))}
      />}
      <SelectField
        label="Speaker layout"
        value={manifest.mixing.channel_layout}
        onChange={(channel_layout) =>
          setManifest({
            ...manifest,
            mixing: { ...manifest.mixing, channel_layout },
          })
        }
        options={(
          choices?.channel_layouts || [
            "5.1",
            "7.1",
            "5.1.2",
            "5.1.4",
            "7.1.2",
            "7.1.4",
          ]
        ).map((value) => ({ value, label: value }))}
      />
      <SelectField
        label="Container"
        value={manifest.format.type}
        onChange={(type) =>
          setManifest({ ...manifest, format: { ...manifest.format, type } })
        }
        options={(choices?.output_types || ["wav", "adm-bwf"]).map((value) => ({
          value,
          label: value === "adm-bwf" ? "ADM-BWF" : "Multichannel WAV",
        }))}
      />
      <SelectField
        label="Sample rate"
        value={String(manifest.format.sample_rate)}
        onChange={(sample_rate) =>
          setManifest({
            ...manifest,
            format: { ...manifest.format, sample_rate: Number(sample_rate) },
          })
        }
        options={(
          choices?.sample_rates || [44100, 48000, 88200, 96000, 192000]
        ).map((value) => ({
          value: String(value),
          label: `${value / 1000} kHz`,
        }))}
      />
      <SelectField
        label="Bit depth"
        value={manifest.format.subtype}
        onChange={(subtype) =>
          setManifest({ ...manifest, format: { ...manifest.format, subtype } })
        }
        options={(
          choices?.output_subtypes || ["PCM_16", "PCM_24", "PCM_32", "FLOAT"]
        ).map((value) => ({ value, label: value }))}
        hint={
          manifest.format.type === "adm-bwf"
            ? "ADM-BWF requires PCM_24 at 48 or 96 kHz."
            : undefined
        }
      />
    </div>
  );
}
