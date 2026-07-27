import * as React from "react";
import { FileAudio, Loader2, Upload, X } from "lucide-react";
import { Panel, PanelBody, PanelHeader } from "@/app/Panel";
import {
  FIELD_GRID,
  NullablePotField,
  SelectField,
  SliderField,
  SwitchRow,
} from "@/components/forms/fields";
import { Button } from "@/components/ui/button";
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
  /** Hide the reference-EQ-match block. Used where there's no reference-file
   * association to attach it to (e.g. projects, which don't support a
   * mastering reference the way one-off jobs do). */
  hideReferenceMatch?: boolean;
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

function titleCase(value: string) {
  return value
    .split("-")
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ");
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
  hideReferenceMatch = false,
  referencePending = false,
}: MasteringSectionProps) {
  const choices = configuration?.choices;
  const referenceInput = React.useRef<HTMLInputElement>(null);
  const match = manifest.mastering.match_reference;
  const hasReference = masteringReference !== null;
  const { eq, compressor, bass, loudness } = manifest.mastering;

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
      {!hideReferenceMatch && (
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
              max={24}
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
      )}

      <EffectPanel
        title="Loudness"
        enabled={loudness.normalize}
        onEnabledChange={(normalize) =>
          setMastering({ loudness: { ...loudness, normalize } })
        }
      >
        <div className={FIELD_GRID}>
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
        title="Bass control"
        enabled={bass.profile !== null}
        toggleDisabled={cannotEnable(bass.profile, choices?.bass_profiles)}
        onEnabledChange={profileToggle(bass.profile, lastBass, choices?.bass_profiles, (profile) =>
          setMastering({ bass: { ...bass, profile } }),
        )}
      >
        <SelectField
          label="Profile"
          value={bass.profile || ""}
          disabled={!bass.profile}
          onChange={(profile) => setMastering({ bass: { ...bass, profile } })}
          options={(choices?.bass_profiles || []).map((value) => ({
            value,
            label: titleCase(value),
          }))}
        />
        <SwitchRow
          label="Bass exciter"
          checked={bass.excite}
          disabled={!bass.profile}
          onChange={(excite) => setMastering({ bass: { ...bass, excite } })}
        />
        <div className={POT_GRID}>
          {(
            [
              ["sub_gain_db", "Sub gain", "dB", 0.1, -12, 12, 0],
              ["mid_gain_db", "Mid-bass gain", "dB", 0.1, -12, 12, 0],
              ["mono_cutoff_hz", "Mono cutoff", "Hz", 1, 40, 250, 100],
              ["lfe_gain_db", "LFE trim", "dB", 0.1, -12, 12, 0],
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
              disabled={!bass.profile}
              onChange={(value) => setMastering({ bass: { ...bass, [key]: value } })}
            />
          ))}
        </div>
      </EffectPanel>
    </div>
  );
}
