import * as React from "react";
import type { LucideIcon } from "lucide-react";
import {
  FileAudio, Loader2, Minimize2, Sparkles, Speaker, TrendingDown, TrendingUp, Upload, Waves, X,
} from "lucide-react";
import { Panel, PanelBody, PanelHeader } from "@/app/Panel";
import {
  FIELD_GRID,
  NullablePotField,
  SelectField,
  SliderField,
  SwitchRow,
} from "@/components/forms/fields";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { formatBytes } from "@/lib/format";
import type { MasteringReference } from "@/api";
import type { ManifestSectionProps } from "./types";

type MasteringSectionProps = ManifestSectionProps & {
  masteringReference: MasteringReference | null;
  referenceUploading: boolean;
  referenceError: string | null;
  onReferenceUpload: (file: File) => void;
  onReferenceClear: () => void;
  /** True while the backend is (re)computing the reference-match FIR asset
   * (`project.reference_match_pending`). The controls below stay live — they
   * only edit the manifest — but the audible match itself isn't ready yet,
   * so surface that instead of letting the attached reference imply it is. */
  referencePending?: boolean;
};

/** One mastering effect. The header switch is the effect's power button, the
 * way a plug-in bypasses: it replaces the "None" entry every profile picker
 * used to carry and the standalone enable toggles that used to sit in the
 * body. Placed on the trailing edge, matching Apple's settings rows and the
 * `Switch` position in `ToggleField`. */
function EffectPanel({
  title,
  enabled,
  onEnabledChange,
  toggleDisabled = false,
  status,
  children,
}: {
  title: string;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  toggleDisabled?: boolean;
  status?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Panel>
      <PanelHeader
        title={title}
        actions={
          <>
            {status}
            <Switch
              aria-label={title}
              checked={enabled}
              disabled={toggleDisabled}
              onCheckedChange={onEnabledChange}
            />
          </>
        }
      />
      <PanelBody className="space-y-2.5 overflow-visible">{children}</PanelBody>
    </Panel>
  );
}

const POT_GRID = "grid grid-cols-[repeat(auto-fit,minmax(76px,1fr))] gap-3";

/** What each bass profile does, in the terms someone picking one thinks in.
 * The note is the documentation — §6.3 puts it on the option rather than as
 * prose under the picker. Icons are neutral shapes, not brand marks. */
const BASS_PROFILES: Record<string, { label: string; note: string; icon: LucideIcon }> = {
  deep: { label: "Deep", note: "Bass from every speaker", icon: Waves },
  enhance: { label: "Enhance", note: "Fuller, richer low end", icon: Sparkles },
  cinema: { label: "Cinema", note: "Bass moved to the subwoofer", icon: Speaker },
  mono: { label: "Tighten", note: "Bass centred up front", icon: Minimize2 },
  boost: { label: "Boost", note: "More low end", icon: TrendingUp },
  cut: { label: "Cut", note: "Less low end", icon: TrendingDown },
};

const SPREAD_LABELS: Record<string, string> = {
  front: "Front pair only",
  bed: "All floor speakers",
  all: "Floor and height speakers",
};

const LFE_MODE_LABELS: Record<string, string> = {
  off: "Leave the subwoofer alone",
  add: "Add a copy to the subwoofer",
  split: "Move the bass to the subwoofer",
};

const bassMeta = (value: string) =>
  BASS_PROFILES[value] || { label: titleCase(value), note: "", icon: Waves };

function BassOption({ value }: { value: string }) {
  const meta = bassMeta(value);
  return (
    <>
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border bg-card text-foreground">
        <meta.icon className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1 truncate font-medium">{meta.label}</span>
      <span className="shrink-0 text-[11px] text-muted-foreground">{meta.note}</span>
    </>
  );
}

/** Splits a panel body into named runs of controls. Reuses the table-header
 * micro-label rather than introducing a heading style of its own. */
function FieldGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2.5 border-t pt-2.5 first:border-t-0 first:pt-0">
      <p className="text-[11px] font-semibold uppercase tracking-[.06em] text-muted-foreground">
        {title}
      </p>
      {children}
    </div>
  );
}

function titleCase(value: string) {
  return value
    .split("-")
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ");
}

/** The loudness and ceiling a delivery target carries, as manifest fields.
 * Empty for "Custom", which leaves whatever the two controls already show. */
function deliveryTargetValues(
  preset: string,
  targets: Record<string, { target_lkfs: number; max_tp_dbtp: number }> | undefined,
) {
  const target = preset ? targets?.[preset] : undefined;
  return target ? { target: target.target_lkfs, max_tp: target.max_tp_dbtp } : {};
}

export function MasteringSection({
  manifest,
  setManifest,
  configuration,
  masteringReference,
  referenceUploading,
  referenceError,
  onReferenceUpload,
  onReferenceClear,
  referencePending = false,
}: MasteringSectionProps) {
  const choices = configuration?.choices;
  const referenceInput = React.useRef<HTMLInputElement>(null);
  const match = manifest.mastering.match_reference;
  const hasReference = masteringReference !== null;
  const { eq, compressor, bass, loudness } = manifest.mastering;
  // Every bass override is nullable, so an unset control has to show what the
  // profile does. A native <select> given a value no option carries displays
  // its first option instead, which silently claimed "front"/"off" while the
  // profile was running "bed"/"add".
  const bassProfile = bass.profile
    ? configuration?.constants?.bass_profiles?.[bass.profile]
    : undefined;
  const profileExcite = bassProfile?.excite ?? false;
  // Bass management redistributes the low band across a speaker array, so
  // most of its controls have nothing to act on until the layout provides
  // one: an LFE send needs an LFE channel, and on a two-channel bed every
  // spread resolves to FL+FR, leaving unification as a plain bass mono-maker.
  const layoutChannels = choices?.layout_channels?.[manifest.mixing.channel_layout] ?? [];
  const hasLfe = layoutChannels.includes("LFE");
  // Two bed channels means every spread resolves to the same pair, leaving
  // unification as a plain bass mono-maker — there is no placement to choose.
  const canSpread = layoutChannels.filter((name) => name !== "LFE").length > 2;
  const bassOff = !bass.profile;

  // Switching an effect off clears its profile, which is what the manifest
  // means by "off". Remembering the last profile means switching back on
  // restores the choice instead of silently resetting it.
  const lastEq = React.useRef(eq.profile);
  const lastCompressor = React.useRef(compressor.profile);
  const lastBass = React.useRef(bass.profile);

  const setMastering = (patch: Partial<typeof manifest.mastering>) =>
    setManifest({ ...manifest, mastering: { ...manifest.mastering, ...patch } });

  const profileToggle = (
    current: string | null,
    remembered: React.MutableRefObject<string | null>,
    available: string[] | undefined,
    apply: (profile: string | null) => void,
  ) => (enabled: boolean) => {
    if (!enabled) {
      if (current) remembered.current = current;
      apply(null);
      return;
    }
    apply(remembered.current || available?.[0] || null);
  };

  /** Switching an effect off never needs the profile list; switching it on
   * does, so only that direction is blocked while the list is unavailable. */
  const cannotEnable = (current: string | null, available: string[] | undefined) =>
    current === null && !available?.length;

  return (
    <div className="space-y-3">
      <EffectPanel
        title="Reference EQ match"
        enabled={match.spectrum}
        toggleDisabled={!hasReference}
        onEnabledChange={(spectrum) =>
          setMastering({ match_reference: { ...match, spectrum } })
        }
        status={
          hasReference && referencePending ? (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              Preparing
            </span>
          ) : null
        }
      >
        {masteringReference ? (
          <div className="flex items-center justify-between gap-2 rounded-md border bg-muted/40 p-2">
            <div className="flex min-w-0 items-center gap-2">
              <FileAudio className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="truncate text-[13px] font-medium">{masteringReference.filename}</p>
                <p className="text-[11px] text-muted-foreground">
                  {formatBytes(masteringReference.size_bytes)}
                </p>
              </div>
            </div>
            <div className="flex shrink-0 gap-1">
              <Button
                type="button"
                variant="secondary"
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
          <div className="space-y-1.5">
            <Button
              type="button"
              variant="secondary"
              disabled={referenceUploading}
              onClick={() => referenceInput.current?.click()}
            >
              <Upload />
              {referenceUploading ? "Uploading" : "Choose reference track"}
            </Button>
            <p className="text-[11px] text-muted-foreground">
              One WAV or FLAC, matched across every track.
            </p>
          </div>
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
        {referenceError && <p className="text-[11px] text-destructive">{referenceError}</p>}
        <div className={FIELD_GRID}>
          <SliderField
            label="Strength"
            value={match.strength}
            min={0}
            max={1}
            step={0.01}
            disabled={!hasReference || !match.spectrum}
            onChange={(strength) => setMastering({ match_reference: { ...match, strength } })}
          />
          <SliderField
            label="Max correction"
            value={match.max_db}
            min={0}
            max={12}
            step={0.5}
            suffix=" dB"
            disabled={!hasReference || !match.spectrum}
            onChange={(max_db) => setMastering({ match_reference: { ...match, max_db } })}
          />
        </div>
        <SwitchRow
          label="Match RMS level"
          checked={match.rms}
          disabled={!hasReference}
          onChange={(rms) => setMastering({ match_reference: { ...match, rms } })}
        />
      </EffectPanel>

      <EffectPanel
        title="Loudness"
        enabled={loudness.normalize}
        onEnabledChange={(normalize) =>
          setMastering({ loudness: { ...loudness, normalize } })
        }
      >
        <div className={FIELD_GRID}>
          {/* Picking a target writes its numbers into the two controls below,
              which stay live as overrides — the backend resolves the same way
              round (preset first, explicit field wins). */}
          <SelectField
            label="Delivery target"
            value={loudness.target_preset ?? ""}
            onChange={(preset) =>
              setMastering({
                loudness: {
                  ...loudness,
                  target_preset: preset || null,
                  ...deliveryTargetValues(preset, configuration?.constants?.delivery_targets),
                },
              })
            }
            options={[
              { value: "", label: "Custom" },
              ...(choices?.delivery_targets || []).map((value) => ({
                value,
                label: titleCase(value),
              })),
            ]}
          />
          <SliderField
            label="Target"
            value={loudness.target}
            min={-30}
            max={-10}
            step={0.5}
            suffix=" LKFS"
            disabled={!loudness.normalize}
            onChange={(target) => setMastering({ loudness: { ...loudness, target } })}
          />
          {/* True-peak limiting runs whether or not loudness is normalized, so
              this one stays live when the switch is off. */}
          <SliderField
            label="True-peak ceiling"
            value={loudness.max_tp}
            min={-6}
            max={0}
            step={0.1}
            suffix=" dBTP"
            onChange={(max_tp) => setMastering({ loudness: { ...loudness, max_tp } })}
          />
        </div>
      </EffectPanel>

      <EffectPanel
        title="Spectral EQ"
        enabled={eq.profile !== null}
        toggleDisabled={cannotEnable(eq.profile, choices?.eq_profiles)}
        onEnabledChange={profileToggle(eq.profile, lastEq, choices?.eq_profiles, (profile) =>
          setMastering({ eq: { ...eq, profile } }),
        )}
      >
        <div className={FIELD_GRID}>
          <SelectField
            label="Profile"
            value={eq.profile || ""}
            disabled={!eq.profile}
            onChange={(profile) => setMastering({ eq: { ...eq, profile } })}
            options={(choices?.eq_profiles || []).map((value) => ({
              value,
              label: titleCase(value),
            }))}
          />
          <SliderField
            label="Strength"
            value={eq.strength}
            min={0}
            max={1}
            step={0.01}
            disabled={!eq.profile}
            onChange={(strength) => setMastering({ eq: { ...eq, strength } })}
          />
        </div>
      </EffectPanel>

      <EffectPanel
        title="Bus compressor"
        enabled={compressor.profile !== null}
        toggleDisabled={cannotEnable(compressor.profile, choices?.compressor_profiles)}
        onEnabledChange={profileToggle(
          compressor.profile,
          lastCompressor,
          choices?.compressor_profiles,
          (profile) => setMastering({ compressor: { ...compressor, profile } }),
        )}
      >
        <SelectField
          label="Profile"
          value={compressor.profile || ""}
          disabled={!compressor.profile}
          onChange={(profile) => setMastering({ compressor: { ...compressor, profile } })}
          options={(choices?.compressor_profiles || []).map((value) => ({
            value,
            label: titleCase(value),
          }))}
        />
        <div className={POT_GRID}>
          {(
            [
              ["threshold_db", "Threshold", "dB", 0.5, -40, 0, -18],
              ["ratio", "Ratio", "", 0.1, 1, 10, 2],
              ["attack_ms", "Attack", "ms", 1, 1, 100, 20],
              ["release_ms", "Release", "ms", 5, 20, 1000, 200],
              ["knee_db", "Knee", "dB", 0.5, 0, 24, 6],
              ["makeup_db", "Makeup gain", "dB", 0.5, 0, 12, 0],
              // Pairs with bass control's Punch, which a full-band sidechain
              // squashes before the shaper ever sees it.
              ["sidechain_hpf_hz", "Sidechain HPF", "Hz", 5, 20, 300, 100],
            ] as const
          ).map(([key, label, suffix, step, min, max, defaultValue]) => (
            <NullablePotField
              key={key}
              label={label}
              value={compressor[key]}
              defaultValue={defaultValue}
              min={min}
              max={max}
              step={step}
              suffix={suffix ? ` ${suffix}` : undefined}
              disabled={!compressor.profile}
              onChange={(value) => setMastering({ compressor: { ...compressor, [key]: value } })}
            />
          ))}
        </div>
      </EffectPanel>

      <EffectPanel
        title="Bass"
        enabled={bass.profile !== null}
        toggleDisabled={cannotEnable(bass.profile, choices?.bass_profiles)}
        onEnabledChange={profileToggle(bass.profile, lastBass, choices?.bass_profiles, (profile) =>
          setMastering({ bass: { ...bass, profile } }),
        )}
      >
        {/* The profile is what everything below refines, so it gets the
            full-width rich picker and its own row (§6.3). */}
        <div className="space-y-1.5">
          <Label>Profile</Label>
          <Select
            value={bass.profile || ""}
            disabled={bassOff}
            onValueChange={(profile) => setMastering({ bass: { ...bass, profile } })}
          >
            <SelectTrigger aria-label="Profile" className="h-11 px-2">
              <span className="flex min-w-0 flex-1 items-center gap-2">
                {bass.profile ? <BassOption value={bass.profile} /> : null}
              </span>
            </SelectTrigger>
            <SelectContent>
              {(choices?.bass_profiles || []).map((value) => (
                <SelectItem key={value} value={value} className="h-11">
                  <BassOption value={value} />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <FieldGroup title="Tone">
          <SwitchRow
            label="Exciter"
            hint={bass.excite === null ? "from profile" : undefined}
            checked={bass.excite ?? profileExcite}
            disabled={bassOff}
            onChange={(excite) => setMastering({ bass: { ...bass, excite } })}
          />
          <div className={POT_GRID}>
            {(
              [
                ["sub_gain_db", "Sub gain", "dB", 0.1, -12, 12, 0],
                ["mid_gain_db", "Mid-bass", "dB", 0.1, -12, 12, 0],
                ["unify_hz", "Crossover", "Hz", 1, 40, 120, 90],
                ["punch", "Punch", "", 0.01, -1, 1, 0],
              ] as const
            ).map(([key, label, suffix, step, min, max, defaultValue]) => (
              <NullablePotField
                key={key}
                label={label}
                value={bass[key]}
                defaultValue={defaultValue}
                min={min}
                max={max}
                step={step}
                suffix={suffix ? ` ${suffix}` : undefined}
                disabled={bassOff}
                onChange={(value) => setMastering({ bass: { ...bass, [key]: value } })}
              />
            ))}
          </div>
        </FieldGroup>

        {/* Bass management redistributes the low band across a speaker array.
            Spread and the subwoofer feed need a layout that offers one, so
            they are not rendered at all rather than shown dimmed. Width acts
            between any two channels, so the group itself always renders. */}
        <FieldGroup title="Placement">
            {canSpread && (
              <SelectField
                label="Spread"
                value={bass.spread || bassProfile?.spread || ""}
                disabled={bassOff}
                onChange={(spread) => setMastering({ bass: { ...bass, spread } })}
                options={(choices?.bass_spreads || []).map((value) => ({
                  value,
                  label: SPREAD_LABELS[value] || titleCase(value),
                }))}
              />
            )}
            {hasLfe && (
              <>
                <SelectField
                  label="Subwoofer"
                  value={bass.lfe_mode || bassProfile?.lfe_mode || ""}
                  disabled={bassOff}
                  onChange={(lfe_mode) => setMastering({ bass: { ...bass, lfe_mode } })}
                  options={(choices?.bass_lfe_modes || []).map((value) => ({
                    value,
                    label: LFE_MODE_LABELS[value] || titleCase(value),
                  }))}
                />
                <div className={POT_GRID}>
                  {(
                    [
                      ["lfe_send", "Sub level", "", 0.01, 0, 1, 0],
                      ["lfe_gain_db", "Sub trim", "dB", 0.1, -12, 12, 0],
                    ] as const
                  ).map(([key, label, suffix, step, min, max, defaultValue]) => (
                    <NullablePotField
                      key={key}
                      label={label}
                      value={bass[key]}
                      defaultValue={defaultValue}
                      min={min}
                      max={max}
                      step={step}
                      suffix={suffix ? ` ${suffix}` : undefined}
                      disabled={bassOff}
                      onChange={(value) => setMastering({ bass: { ...bass, [key]: value } })}
                    />
                  ))}
                </div>
              </>
            )}
            {/* Decorrelation spreads the sustained 100-300 Hz band across
                channels; everything under the crossover stays mono. */}
            <div className={POT_GRID}>
              <NullablePotField
                label="Width"
                value={bass.decorrelate}
                defaultValue={0}
                min={0}
                max={1}
                step={0.01}
                disabled={bassOff}
                onChange={(decorrelate) => setMastering({ bass: { ...bass, decorrelate } })}
              />
            </div>
          </FieldGroup>
      </EffectPanel>
    </div>
  );
}
