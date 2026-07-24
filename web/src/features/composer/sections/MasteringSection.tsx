import * as React from "react";
import { FileAudio, Upload, X } from "lucide-react";
import {
  NumberField,
  SelectField,
  SliderField,
  ToggleField,
} from "@/components/forms/fields";
import { Button } from "@/components/ui/button";
import { formatBytes } from "@/lib/format";
import type { MasteringReference } from "@/api";
import type { ManifestSectionProps } from "./types";

type MasteringSectionProps = ManifestSectionProps & {
  masteringReference: MasteringReference | null;
  referenceUploading: boolean;
  referenceError: string | null;
  onReferenceUpload: (file: File) => void;
  onReferenceClear: () => void;
  /** Hide the reference-EQ-match block. Used where there's no reference-file
   * association to attach it to (e.g. projects, which don't support a
   * mastering reference the way one-off jobs do). */
  hideReferenceMatch?: boolean;
};

export function MasteringSection({
  manifest,
  setManifest,
  configuration,
  masteringReference,
  referenceUploading,
  referenceError,
  onReferenceUpload,
  onReferenceClear,
  hideReferenceMatch = false,
}: MasteringSectionProps) {
  const choices = configuration?.choices;
  const referenceInput = React.useRef<HTMLInputElement>(null);
  const match = manifest.mastering.match_reference;
  const hasReference = masteringReference !== null;
  return (
    <div className="grid gap-5 rounded-md border p-4 sm:grid-cols-2">
      {!hideReferenceMatch && (
      <section className="space-y-3 rounded-md border bg-muted/20 p-3 sm:col-span-2">
        <div>
          <p className="text-sm font-medium">Reference EQ match</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Match this job to one WAV or FLAC reference before preset EQ. One
            reference applies to every album track.
          </p>
        </div>
        {masteringReference ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-background p-3">
            <div className="flex min-w-0 items-center gap-2">
              <FileAudio className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">
                  {masteringReference.filename}
                </p>
                <p className="text-xs text-muted-foreground">
                  {formatBytes(masteringReference.size_bytes)}
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={referenceUploading}
                onClick={() => referenceInput.current?.click()}
              >
                <Upload /> Replace
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={referenceUploading}
                onClick={onReferenceClear}
              >
                <X /> Remove
              </Button>
            </div>
          </div>
        ) : (
          <Button
            type="button"
            variant="outline"
            disabled={referenceUploading}
            onClick={() => referenceInput.current?.click()}
          >
            <Upload />
            {referenceUploading ? "Uploading reference" : "Choose reference track"}
          </Button>
        )}
        <input
          ref={referenceInput}
          className="hidden"
          type="file"
          aria-label="Reference audio track"
          accept="audio/wav,audio/flac,.wav,.flac"
          onChange={(event) => {
            const [file] = Array.from(event.target.files || []);
            if (file) onReferenceUpload(file);
            event.currentTarget.value = "";
          }}
        />
        {referenceError && (
          <p className="text-xs text-destructive">{referenceError}</p>
        )}
      </section>
      )}
      {!hideReferenceMatch && (
      <>
      <SliderField
        label="Spectral match strength"
        value={match.strength}
        min={0}
        max={1}
        step={0.01}
        disabled={!hasReference || !match.spectrum}
        onChange={(strength) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              match_reference: { ...match, strength },
            },
          })
        }
      />
      <NumberField
        label="Maximum spectral correction"
        value={match.max_db}
        min={0}
        step={0.5}
        suffix="dB"
        hint="Limit per-band correction magnitude."
        disabled={!hasReference || !match.spectrum}
        onChange={(max_db) => {
          if (max_db != null)
            setManifest({
              ...manifest,
              mastering: {
                ...manifest.mastering,
                match_reference: { ...match, max_db },
              },
            });
        }}
      />
      <ToggleField
        label="Match spectrum"
        description="Apply per-channel spectral envelope correction."
        checked={match.spectrum}
        disabled={!hasReference}
        onChange={(spectrum) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              match_reference: { ...match, spectrum },
            },
          })
        }
      />
      <ToggleField
        label="Match RMS level"
        description="Match overall reference loudness before final mastering."
        checked={match.rms}
        disabled={!hasReference}
        onChange={(rms) =>
          setManifest({
            ...manifest,
            mastering: {
              ...manifest.mastering,
              match_reference: { ...match, rms },
            },
          })
        }
      />
      </>
      )}
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
