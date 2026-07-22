import {
  NumberField,
  SelectField,
  ToggleField,
} from "@/components/forms/fields";
import type { ManifestSectionProps } from "./types";

export function ProcessingSection({
  manifest,
  setManifest,
}: ManifestSectionProps) {
  return (
    <div className="grid gap-5 rounded-md border p-4 sm:grid-cols-2">
      <ToggleField
        label="Short test render"
        description="Process only a short section. Source playback remains available in Overview."
        checked={manifest.processing.preview}
        onChange={(preview) =>
          setManifest({
            ...manifest,
            processing: { ...manifest.processing, preview },
          })
        }
      />
      <ToggleField
        label="Normalize output"
        description="Apply output peak normalization before export."
        checked={manifest.processing.normalize_output}
        onChange={(normalize_output) =>
          setManifest({
            ...manifest,
            processing: { ...manifest.processing, normalize_output },
          })
        }
      />
      <NumberField
        label="Test render duration"
        value={manifest.processing.preview_duration}
        min={0.1}
        step={0.5}
        suffix="s"
        onChange={(value) => {
          if (value != null)
            setManifest({
              ...manifest,
              processing: { ...manifest.processing, preview_duration: value },
            });
        }}
      />
      <NumberField
        label="Test render start"
        value={manifest.processing.preview_start}
        min={0}
        step={0.5}
        suffix="s"
        hint="Blank uses pipeline default."
        onChange={(preview_start) =>
          setManifest({
            ...manifest,
            processing: { ...manifest.processing, preview_start },
          })
        }
      />
      <SelectField
        label="FFT size"
        value={String(manifest.processing.fft_size)}
        onChange={(fft_size) =>
          setManifest({
            ...manifest,
            processing: { ...manifest.processing, fft_size: Number(fft_size) },
          })
        }
        options={[1024, 2048, 4096, 8192, 16384].map((value) => ({
          value: String(value),
          label: String(value),
        }))}
      />
      <SelectField
        label="Block size"
        value={String(manifest.processing.block_size)}
        onChange={(block_size) =>
          setManifest({
            ...manifest,
            processing: {
              ...manifest.processing,
              block_size: Number(block_size),
            },
          })
        }
        options={[1024, 2048, 4096, 8192, 16384].map((value) => ({
          value: String(value),
          label: String(value),
        }))}
      />
    </div>
  );
}
