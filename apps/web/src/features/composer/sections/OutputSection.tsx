import { SelectField, ToggleField } from "@/components/forms/fields";
import { OUTPUT_CODECS, codecUnavailableReason, resolveCodec, subtypesFor } from "@/lib/codecs";
import { CHANNEL_LAYOUTS, OUTPUT_TYPES, deliveryTypeForLayout, isStereoLayout } from "@/lib/layouts";
import type { Manifest } from "@/lib/manifest";
import type { ManifestSectionProps } from "./types";

export function OutputSection({
  manifest,
  setManifest,
  configuration,
}: ManifestSectionProps) {
  const choices = configuration?.choices;
  const separation = configuration?.capabilities.stem_separation;
  const stereo = isStereoLayout(manifest.mixing.channel_layout);
  const codecs = choices?.output_codecs || OUTPUT_CODECS;
  const bitDepths = subtypesFor(codecs, manifest.format.codec);

  // Layout, format and sample rate all constrain the codec, so every edit to
  // one retargets it rather than leaving a combination the server rejects.
  const withFormat = (
    next: Partial<Manifest["format"]>,
    layout = manifest.mixing.channel_layout,
  ): Manifest["format"] => {
    const merged = { ...manifest.format, ...next };
    return {
      ...merged,
      codec: resolveCodec(codecs, merged.codec, layout, merged.type, merged.sample_rate),
    };
  };
  return (
    <div className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2">
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
      {!stereo && <ToggleField
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
      />}
      {!stereo && (manifest.format.downmix?.enabled ?? false) && <SelectField
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
            format: withFormat(
              { type: deliveryTypeForLayout(channel_layout, manifest.format.type) },
              channel_layout,
            ),
          })
        }
        options={(choices?.channel_layouts || CHANNEL_LAYOUTS).map((value) => ({
          value,
          label: value,
        }))}
      />
      {(() => {
        const binauralBeds = choices?.binaural_beds || ["5.1.4", "7.1.2", "7.1.4"];
        const bedSupported = binauralBeds.includes(manifest.mixing.channel_layout);
        const transauralBeds = choices?.transaural_beds || ["5.1.4", "7.1.2", "7.1.4"];
        const transauralBedSupported = transauralBeds.includes(manifest.mixing.channel_layout);
        return (
          <>
            <SelectField
              label="Format"
              value={manifest.format.type}
              onChange={(type) => setManifest({ ...manifest, format: withFormat({ type }) })}
              options={(choices?.output_types || OUTPUT_TYPES)
                .filter((value) => !stereo || value === "multichannel")
                .map((value) => ({
                  value,
                  label:
                    value === "adm-bwf"
                      ? "ADM Broadcast Wave Format"
                      : value === "binaural"
                        ? "Binaural (headphone stereo)"
                        : value === "transaural"
                          ? "Transaural (crosstalk-cancelled speakers)"
                          : stereo
                            ? "Stereo audio"
                            : "Multichannel audio",
                  disabled:
                    (value === "binaural" && !bedSupported) ||
                    (value === "transaural" && !transauralBedSupported),
                }))}
              hint={
                stereo
                  ? "Two-channel delivery — no object master, bed collapse, or downmix companion."
                  : manifest.format.type === "binaural"
                    ? "Renders the speaker layout above as headphone stereo through the Spatial Audio Engine."
                    : manifest.format.type === "transaural"
                      ? "Renders the speaker layout above as crosstalk-cancelled stereo for real speakers."
                      : !bedSupported
                        ? `Binaural requires speaker layout ${binauralBeds.join(", ")}.`
                        : !transauralBedSupported
                          ? `Transaural requires speaker layout ${transauralBeds.join(", ")}.`
                          : undefined
              }
            />
            {manifest.format.type === "binaural" && (
              <SelectField
                label="Spatial Audio Engine profile"
                value={manifest.format.binaural.profile}
                onChange={(profile) => setManifest({
                  ...manifest,
                  format: { ...manifest.format, binaural: { ...manifest.format.binaural, profile } },
                })}
                options={(choices?.binaural_profiles || ["studio", "listening", "flat"]).map((value) => ({
                  value,
                  label: value.charAt(0).toUpperCase() + value.slice(1),
                }))}
                hint="Studio = neutral monitoring room. Listening = flattering hi-fi enhance (warm, airy, cinema-wide; loudness-matched). Flat = anechoic reference."
              />
            )}
            {manifest.format.type === "transaural" && (
              <SelectField
                label="Transaural profile"
                value={manifest.format.transaural.profile}
                onChange={(profile) => setManifest({
                  ...manifest,
                  format: { ...manifest.format, transaural: { ...manifest.format.transaural, profile } },
                })}
                options={(choices?.transaural_profiles || ["stereo", "smart_speaker", "car", "laptop", "phone"]).map((value) => ({
                  value,
                  label: value
                    .split("_")
                    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
                    .join(" "),
                }))}
                hint="Stereo = standard hi-fi speaker pair. Smart speaker = narrow dual-driver cabinet. Car = off-center driver-seat position. Laptop = built-in chassis speakers. Phone = built-in handset speakers."
              />
            )}
          </>
        );
      })()}
      <SelectField
        label="Codec"
        value={manifest.format.codec}
        disabled={manifest.format.type === "adm-bwf"}
        onChange={(codec) => setManifest({ ...manifest, format: withFormat({ codec }) })}
        options={codecs.map((entry) => {
          const reason = codecUnavailableReason(
            entry,
            manifest.mixing.channel_layout,
            manifest.format.type,
            manifest.format.sample_rate,
          );
          return {
            value: entry.name,
            label: reason ? `${entry.label} — ${reason}` : entry.label,
            disabled: Boolean(reason),
          };
        })}
        hint={
          manifest.format.type === "adm-bwf"
            ? "ADM-BWF is a WAV container."
            : "FLAC is lossless but caps at 8 channels; OGG is lossy."
        }
      />
      <SelectField
        label="Sample rate"
        value={String(manifest.format.sample_rate)}
        onChange={(sample_rate) =>
          setManifest({ ...manifest, format: withFormat({ sample_rate: Number(sample_rate) }) })
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
        value={bitDepths.length ? manifest.format.subtype : ""}
        disabled={bitDepths.length === 0}
        onChange={(subtype) => setManifest({ ...manifest, format: withFormat({ subtype }) })}
        options={
          bitDepths.length
            ? bitDepths.map((value) => ({ value, label: value }))
            : [{ value: "", label: "—" }]
        }
        hint={
          manifest.format.type === "adm-bwf"
            ? "ADM-BWF requires PCM_24 at 48 or 96 kHz."
            : bitDepths.length === 0
              ? "Lossy codec — no bit depth."
              : undefined
        }
      />
    </div>
  );
}
