import * as React from "react";
import { FileAudio, Loader2, Upload, X } from "lucide-react";
import { FIELD_GRID, NullablePotField, SliderField, SwitchRow } from "@/components/forms/fields";
import { Button } from "@/components/ui/button";
import { formatBytes } from "@/lib/format";
import type { MasteringReference } from "@/api";
import type { Manifest } from "@/lib/manifest";
import { EffectPanel, POT_GRID } from "./EffectPanel";

type MatchBlock = Manifest["mastering"]["match_reference"];

/** The reference matcher's panel: the reference file itself, the two strength
 * controls, and mastering phase 7's three curve-realization controls. The
 * smoothing pot's default and range are served
 * (`docs/contracts/preview_export_parity.md` §2), not authored here. */
export function ReferenceMatchPanel({
  match,
  setMastering,
  smoothing,
  masteringReference,
  referenceUploading,
  referenceError,
  referencePending,
  onReferenceUpload,
  onReferenceClear,
}: {
  match: MatchBlock;
  setMastering: (patch: Partial<Manifest["mastering"]>) => void;
  smoothing: { defaultOct: number; minOct: number; maxOct: number };
  masteringReference: MasteringReference | null;
  referenceUploading: boolean;
  referenceError: string | null;
  referencePending: boolean;
  onReferenceUpload: (file: File) => void;
  onReferenceClear: () => void;
}) {
  const referenceInput = React.useRef<HTMLInputElement>(null);
  const hasReference = masteringReference !== null;

  return (
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
      <div className={POT_GRID}>
        <NullablePotField
          label="Smoothing"
          value={match.smooth_octaves}
          defaultValue={smoothing.defaultOct}
          min={smoothing.minOct}
          max={smoothing.maxOct}
          step={1 / 48}
          suffix=" oct"
          disabled={!hasReference || !match.spectrum}
          onChange={(smooth_octaves) =>
            setMastering({ match_reference: { ...match, smooth_octaves } })
          }
        />
        <NullablePotField
          label="Match above"
          value={match.low_hz}
          defaultValue={20}
          min={20}
          max={2000}
          step={10}
          suffix=" Hz"
          disabled={!hasReference || !match.spectrum}
          onChange={(low_hz) => setMastering({ match_reference: { ...match, low_hz } })}
        />
        <NullablePotField
          label="Match below"
          value={match.high_hz}
          defaultValue={20000}
          min={2000}
          max={20000}
          step={100}
          suffix=" Hz"
          disabled={!hasReference || !match.spectrum}
          onChange={(high_hz) => setMastering({ match_reference: { ...match, high_hz } })}
        />
      </div>
      <SwitchRow
        label="Match RMS level"
        checked={match.rms}
        disabled={!hasReference}
        onChange={(rms) => setMastering({ match_reference: { ...match, rms } })}
      />
    </EffectPanel>
  );
}
