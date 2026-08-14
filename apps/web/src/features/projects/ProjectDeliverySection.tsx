import type { LucideIcon } from "lucide-react";
import { AudioLines, Boxes, Headphones, Speaker } from "lucide-react";
import { Panel, PanelBody, PanelHeader } from "@/app/Panel";
import { FIELD_GRID, SelectField, SwitchRow } from "@/components/forms/fields";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import type { Configuration } from "@/api";
import { OUTPUT_TYPES, isStereoLayout } from "@/lib/layouts";
import type { Manifest } from "@/lib/manifest";

// Delivery/encoding controls for a project. Deliberately not a reuse of the
// Job Composer's OutputSection/ProcessingSection: those also carry
// `engine.mode` (forced server-side to "stem" for projects) and test-render
// fields (`processing.preview`/`fft_size`/`block_size`/`preview_start`) that
// don't apply — a project already has a real client-side live preview.

// Neutral icons, not vendor marks: "ADM-BWF" is an ITU/EBU container and
// Atmos is Dolby's trademark, so nothing here impersonates a brand logo.
const FORMATS: Record<string, { label: string; note: string; icon: LucideIcon }> = {
  wav: { label: "Multichannel WAV", note: "Discrete channels", icon: AudioLines },
  "adm-bwf": { label: "ADM-BWF", note: "Object master", icon: Boxes },
  binaural: { label: "Binaural", note: "Headphone stereo", icon: Headphones },
  transaural: { label: "Transaural", note: "Crosstalk-cancelled speaker stereo", icon: Speaker },
};

function formatMeta(value: string, stereo = false) {
  if (value === "wav" && stereo) return { label: "Stereo WAV", note: "FL/FR", icon: AudioLines };
  return FORMATS[value] || { label: value, note: "", icon: AudioLines };
}

/** Format mark standing in for the container's logo. The border is what keeps
 * it legible: the badge sits on `secondary` in the trigger and on `popover`
 * in the menu, and no single neutral fill reads against both. */
function FormatBadge({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border bg-card text-foreground">
      <Icon className="h-4 w-4" />
    </span>
  );
}

function FormatOption({ value, note, stereo }: { value: string; note?: string; stereo?: boolean }) {
  const meta = formatMeta(value, stereo);
  return (
    <>
      <FormatBadge icon={meta.icon} />
      <span className="min-w-0 flex-1 truncate font-medium">{meta.label}</span>
      <span className="shrink-0 text-[11px] text-muted-foreground">{note ?? meta.note}</span>
    </>
  );
}

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
  const transauralBeds = choices?.transaural_beds || ["5.1.4", "7.1.2", "7.1.4"];
  const transauralBedSupported = transauralBeds.includes(manifest.mixing.channel_layout);
  const stereo = isStereoLayout(manifest.mixing.channel_layout);
  const type = manifest.format.type;
  const isBinaural = type === "binaural";
  const isTransaural = type === "transaural";
  // Anything already delivering two channels — a collapsed bed or a stereo
  // layout — has no use for a separate BS.775 stereo companion file.
  const isTwoChannelDelivery = isBinaural || isTransaural || stereo;
  const downmixEnabled = manifest.format.downmix?.enabled ?? false;

  // A disabled option has to say why it is disabled, but that belongs on the
  // option itself rather than as prose under the picker.
  const noteFor = (value: string) => {
    if (value === "binaural" && !bedSupported) return `Needs ${binauralBeds.join(" / ")}`;
    if (value === "transaural" && !transauralBedSupported) return `Needs ${transauralBeds.join(" / ")}`;
    return undefined;
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label>Format</Label>
        <Select
          value={type}
          onValueChange={(next) => onChange({ ...manifest, format: { ...manifest.format, type: next } })}
        >
          <SelectTrigger aria-label="Format" className="h-11 px-2">
            <span className="flex min-w-0 flex-1 items-center gap-2">
              <FormatOption value={type} stereo={stereo} />
            </span>
          </SelectTrigger>
          <SelectContent>
            {(choices?.output_types || OUTPUT_TYPES)
              .filter((value) => !stereo || value === "wav")
              .map((value) => (
              <SelectItem
                key={value}
                value={value}
                disabled={(value === "binaural" && !bedSupported) || (value === "transaural" && !transauralBedSupported)}
                className="h-11"
              >
                <FormatOption value={value} note={noteFor(value)} stereo={stereo} />
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Panel>
        <PanelHeader title={`${formatMeta(type, stereo).label} options`} />
        <PanelBody className="space-y-2.5 overflow-visible">
          <div className={FIELD_GRID}>
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
              hint={type === "adm-bwf" ? "Requires PCM_24 at 48 or 96 kHz." : undefined}
            />
          </div>

          {isBinaural && (
            <SelectField
              label="Spatial Audio Engine profile"
              value={manifest.format.binaural.profile}
              onChange={(profile) =>
                onChange({
                  ...manifest,
                  format: {
                    ...manifest.format,
                    binaural: { ...manifest.format.binaural, profile },
                  },
                })
              }
              options={(choices?.binaural_profiles || ["studio", "listening", "flat"]).map(
                (value) => ({ value, label: value.charAt(0).toUpperCase() + value.slice(1) }),
              )}
            />
          )}

          {isTransaural && (
            <SelectField
              label="Spatial Audio Engine profile"
              value={manifest.format.transaural.profile}
              onChange={(profile) =>
                onChange({
                  ...manifest,
                  format: {
                    ...manifest.format,
                    transaural: { ...manifest.format.transaural, profile },
                  },
                })
              }
              options={(choices?.transaural_profiles || ["stereo", "smart_speaker", "car", "laptop", "phone"]).map(
                (value) => ({ value, label: value.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ") }),
              )}
            />
          )}

          {/* A binaural/transaural render is already two-channel, so a
              stereo companion file would just duplicate it. */}
          {!isTwoChannelDelivery && (
            <SwitchRow
              label="Stereo downmix"
              hint="BS.775 companion file."
              checked={downmixEnabled}
              onChange={(enabled) =>
                onChange({
                  ...manifest,
                  format: {
                    ...manifest.format,
                    downmix: {
                      ...(manifest.format.downmix || { surround_coeff: 0.7071 }),
                      enabled,
                    },
                  },
                })
              }
            />
          )}
          {!isTwoChannelDelivery && downmixEnabled && (
            <SelectField
              label="Surround coefficient"
              value={String(manifest.format.downmix?.surround_coeff ?? 0.7071)}
              onChange={(surround_coeff) =>
                onChange({
                  ...manifest,
                  format: {
                    ...manifest.format,
                    downmix: {
                      ...(manifest.format.downmix || { enabled: true }),
                      surround_coeff: Number(surround_coeff),
                    },
                  },
                })
              }
              options={[0.7071, 0.5, 0].map((value) => ({
                value: String(value),
                label: String(value),
              }))}
            />
          )}

          <SwitchRow
            label="Normalize output"
            checked={manifest.processing.normalize_output}
            onChange={(normalize_output) =>
              onChange({
                ...manifest,
                processing: { ...manifest.processing, normalize_output },
              })
            }
          />
        </PanelBody>
      </Panel>
    </div>
  );
}
