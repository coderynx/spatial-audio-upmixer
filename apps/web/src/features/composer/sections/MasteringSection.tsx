import * as React from "react";
import type { LucideIcon } from "lucide-react";
import { Sparkles, TrendingDown, TrendingUp, Waves } from "lucide-react";
import { Panel, PanelBody, PanelHeader } from "@/app/Panel";
import {
  FIELD_GRID,
  NullablePotField,
  SelectField,
  SliderField,
} from "@/components/forms/fields";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import { resolveBassParams, resolveDeliveryTarget } from "@/features/projects/masteringProfiles";
import type { MasteringReference } from "@/api";
import type { ManifestSectionProps } from "./types";
import { DynamicEqPanel } from "./DynamicEqPanel";
import { ReferenceMatchPanel } from "./ReferenceMatchPanel";
import { EffectPanel, POT_GRID, titleCase } from "./EffectPanel";

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

/** What each bass profile does, in the terms someone picking one thinks in.
 * The note is the documentation — §6.3 puts it on the option rather than as
 * prose under the picker. Icons are neutral shapes, not brand marks. */
const BASS_PROFILES: Record<string, { label: string; note: string; icon: LucideIcon }> = {
  boost: { label: "Boost", note: "More low end", icon: TrendingUp },
  cut: { label: "Cut", note: "Less low end", icon: TrendingDown },
  enhance: { label: "Enhance", note: "Fuller, richer low end", icon: Sparkles },
  deep: { label: "Deep", note: "Focused low bass and harmonics", icon: Waves },
};

const BASS_PRESETS = ["boost", "cut", "enhance", "deep"] as const;

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

/** Shown by the loudness pots only until `GET /api/v1/configuration` lands,
 * the way the bass pots' literal defaults do. The real numbers are served
 * (`delivery_default`); this never reaches the preview or the export. */
const PRE_BOOTSTRAP_DELIVERY = { target_lkfs: -18, max_tp_dbtp: -1, tolerance_lu: null };

/** Same role as `PRE_BOOTSTRAP_DELIVERY` for the match-smoothing pot: what it
 * displays before the configuration request lands. Never realized against. */
const PRE_BOOTSTRAP_SMOOTH = { defaultOct: 1 / 3, minOct: 1 / 12, maxOct: 1 };

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
  const match = manifest.mastering.match_reference;
  const { eq, compressor, bass, loudness, highpass, clip, dynamic_eq } = manifest.mastering;
  // What the two loudness controls show while unset — the named target's own
  // numbers, resolved the same way the export resolves them. The placeholder
  // only stands in before the constants land, like the bass pots' defaults;
  // nothing on the audio path reads it.
  const delivery = resolveDeliveryTarget(
    loudness,
    configuration?.constants?.delivery_targets,
    configuration?.constants?.delivery_default ?? PRE_BOOTSTRAP_DELIVERY,
  );
  const served = configuration?.constants?.reference_match_smooth;
  const smoothing = served
    ? { defaultOct: served.default_oct, minOct: served.min_oct, maxOct: served.max_oct }
    : PRE_BOOTSTRAP_SMOOTH;

  // Switching an effect off clears its profile, which is what the manifest
  // means by "off". Remembering the last profile means switching back on
  // restores the choice instead of silently resetting it.
  const lastEq = React.useRef(eq.profile);
  const lastCompressor = React.useRef(compressor.profile);

  const setMastering = (patch: Partial<typeof manifest.mastering>) =>
    setManifest({ ...manifest, mastering: { ...manifest.mastering, ...patch } });

  const bassProfiles = configuration?.constants?.bass_profiles ?? {};
  const defaultUnifyHz = configuration?.constants?.bass_unify_default_hz ?? 90;
  const resolvedBass = resolveBassParams(bass, bassProfiles, defaultUnifyHz);
  const bassValues = {
    sub_gain_db: resolvedBass?.sub_gain_db ?? 0,
    mid_gain_db: resolvedBass?.mid_gain_db ?? 0,
    punch: resolvedBass?.punch ?? 0,
    harmonics: resolvedBass?.harmonics ?? 0,
  };
  const expectedUnify = bassValues.punch !== 0 || bassValues.harmonics > 0
    ? defaultUnifyHz
    : null;
  const legacyBass = bass.profile !== null
    || bass.excite !== null
    || (bass.unify_hz !== null && bass.unify_hz !== expectedUnify)
    || (bass.spread !== null && bass.spread !== "bed")
    || (bass.lfe_mode !== null && bass.lfe_mode !== "off")
    || (bass.lfe_send !== null && bass.lfe_send !== 0)
    || (bass.lfe_gain_db !== null && bass.lfe_gain_db !== 0)
    || (bass.decorrelate !== null && bass.decorrelate !== 0);
  const selectedBassPreset = legacyBass ? "legacy" : BASS_PRESETS.find((name) => {
    const preset = bassProfiles[name];
    return preset
      && preset.sub_gain_db === bassValues.sub_gain_db
      && preset.mid_gain_db === bassValues.mid_gain_db
      && preset.punch === bassValues.punch
      && Number(preset.excite) === bassValues.harmonics;
  }) ?? "custom";

  const applyBass = (values: typeof bassValues) => {
    const needsBus = values.punch !== 0 || values.harmonics > 0;
    setMastering({
      bass: {
        profile: null,
        sub_gain_db: values.sub_gain_db,
        mid_gain_db: values.mid_gain_db,
        unify_hz: needsBus ? defaultUnifyHz : null,
        spread: "bed",
        punch: values.punch,
        harmonics: values.harmonics,
        excite: null,
        lfe_mode: "off",
        lfe_send: 0,
        lfe_gain_db: 0,
        decorrelate: 0,
      },
    });
  };

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
      {/* First in the chain: nothing downstream should be matching, shaping
          or measuring rumble. The subwoofer keeps its sub content and loses
          only DC. */}
      <EffectPanel
        title="Subsonic filter"
        enabled={highpass.enabled}
        onEnabledChange={(enabled) => setMastering({ highpass: { ...highpass, enabled } })}
      >
        <div className={FIELD_GRID}>
          <SliderField
            label="Cutoff"
            value={highpass.cutoff_hz}
            min={10}
            max={30}
            step={1}
            suffix=" Hz"
            disabled={!highpass.enabled}
            onChange={(cutoff_hz) => setMastering({ highpass: { ...highpass, cutoff_hz } })}
          />
        </div>
      </EffectPanel>
      <ReferenceMatchPanel
        match={match}
        setMastering={setMastering}
        smoothing={smoothing}
        masteringReference={masteringReference}
        referenceUploading={referenceUploading}
        referenceError={referenceError}
        referencePending={referencePending}
        onReferenceUpload={onReferenceUpload}
        onReferenceClear={onReferenceClear}
      />

      <EffectPanel
        title="Loudness"
        enabled={loudness.normalize}
        onEnabledChange={(normalize) =>
          setMastering({ loudness: { ...loudness, normalize } })
        }
      >
        <div className={FIELD_GRID}>
          {/* Picking a target clears both overrides, so the controls below show
              what the specification asks for until one is deliberately moved.
              That is the precedence the backend resolves by, rather than the
              web pre-filling numbers that would then read as overrides. */}
          <SelectField
            label="Delivery target"
            value={loudness.target_preset ?? ""}
            onChange={(preset) =>
              setMastering({
                loudness: {
                  ...loudness,
                  target_preset: preset || null,
                  target: null,
                  max_tp: null,
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
          <NullablePotField
            label="Target"
            value={loudness.target}
            defaultValue={delivery.target_lkfs}
            min={-30}
            max={-10}
            step={0.5}
            suffix=" LKFS"
            disabled={!loudness.normalize}
            onChange={(target) => setMastering({ loudness: { ...loudness, target } })}
          />
          {/* True-peak limiting runs whether or not loudness is normalized, so
              this one stays live when the switch is off. */}
          <NullablePotField
            label="True-peak ceiling"
            value={loudness.max_tp}
            defaultValue={delivery.max_tp_dbtp}
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

      {/* Surgical correction before glue: it runs between the profile curve
          above and the compressor below, which is where it sits in the chain. */}
      <DynamicEqPanel
        profile={dynamic_eq.profile}
        profiles={choices?.dyneq_profiles}
        onChange={(profile) => setMastering({ dynamic_eq: { ...dynamic_eq, profile } })}
      />

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

      <Panel>
        <PanelHeader title="Bass" />
        <PanelBody className="space-y-2.5 overflow-visible">
        <div className="space-y-1.5">
          <Label>Preset</Label>
          <Select
            value={selectedBassPreset}
            disabled={!BASS_PRESETS.some((name) => bassProfiles[name])}
            onValueChange={(name) => {
              const preset = bassProfiles[name];
              if (preset) {
                applyBass({
                  sub_gain_db: preset.sub_gain_db,
                  mid_gain_db: preset.mid_gain_db,
                  punch: preset.punch,
                  harmonics: Number(preset.excite),
                });
              }
            }}
          >
            <SelectTrigger aria-label="Preset" className="h-11 px-2">
              <span className="flex min-w-0 flex-1 items-center gap-2">
                {selectedBassPreset in BASS_PROFILES
                  ? <BassOption value={selectedBassPreset} />
                  : <span>{legacyBass ? "Legacy settings" : "Custom"}</span>}
              </span>
            </SelectTrigger>
            <SelectContent>
              {legacyBass && <SelectItem value="legacy" disabled>Legacy settings</SelectItem>}
              {!legacyBass && <SelectItem value="custom" disabled>Custom</SelectItem>}
              {BASS_PRESETS.filter((name) => bassProfiles[name]).map((value) => (
                <SelectItem key={value} value={value} className="h-11">
                  <BassOption value={value} />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {legacyBass && (
          <p role="status" className="rounded-md bg-warning/10 px-2 py-1.5 text-[11px] text-warning">
            Legacy bass routing is active. Choose a preset or move a control to replace it with QC-safe bass settings.
          </p>
        )}
        <div className={POT_GRID}>
          <NullablePotField
            label="Low end"
            value={bassValues.sub_gain_db}
            defaultValue={0}
            min={-12}
            max={12}
            step={0.1}
            suffix=" dB"
            onChange={(value) => applyBass({ ...bassValues, sub_gain_db: value ?? 0 })}
          />
          <NullablePotField
            label="Body"
            value={bassValues.mid_gain_db}
            defaultValue={0}
            min={-12}
            max={12}
            step={0.1}
            suffix=" dB"
            onChange={(value) => applyBass({ ...bassValues, mid_gain_db: value ?? 0 })}
          />
          <NullablePotField
            label="Punch"
            value={bassValues.punch * 100}
            defaultValue={0}
            min={-100}
            max={100}
            step={1}
            suffix="%"
            onChange={(value) => applyBass({ ...bassValues, punch: (value ?? 0) / 100 })}
          />
          <NullablePotField
            label="Harmonics"
            value={bassValues.harmonics * 100}
            defaultValue={0}
            min={0}
            max={100}
            step={1}
            suffix="%"
            onChange={(value) => applyBass({ ...bassValues, harmonics: (value ?? 0) / 100 })}
          />
        </div>
        </PanelBody>
      </Panel>

      {/* Last before the true-peak limiter, which is what the depth is
          measured down from. Off by default: it trades distortion for
          headroom, which is a choice, not a default. */}
      <EffectPanel
        title="Soft clip"
        enabled={clip.enabled}
        onEnabledChange={(enabled) => setMastering({ clip: { ...clip, enabled } })}
      >
        <div className={FIELD_GRID}>
          <SliderField
            label="Depth below ceiling"
            value={clip.clip_db}
            min={0}
            max={6}
            step={0.1}
            suffix=" dB"
            disabled={!clip.enabled}
            onChange={(clip_db) => setMastering({ clip: { ...clip, clip_db } })}
          />
          <SliderField
            label="Softness"
            value={clip.knee}
            min={0}
            max={1}
            step={0.01}
            disabled={!clip.enabled}
            onChange={(knee) => setMastering({ clip: { ...clip, knee } })}
          />
        </div>
      </EffectPanel>
    </div>
  );
}
