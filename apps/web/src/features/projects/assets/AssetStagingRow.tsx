import { X } from "lucide-react";
import { NumberField, SelectField, SliderField, SwitchRow, ToggleField } from "@/components/forms/fields";
import { Button } from "@/components/ui/button";
import { StemSelectorGrid } from "@/components/stems/StemSelectorGrid";
import { formatBytes } from "@/lib/format";

export type StagedAsset = {
  localId: string;
  file: File;
  stems: string[];
  sampleRate: number;
  subtype: string;
  channelLayout: string;
  bleedReduction: boolean;
  phaseFixLowHz: number;
  phaseFixHighHz: number;
  phaseFixScale: number;
  phaseFixReferenceModel: string;
  debleedModel: string;
  debleed: Record<string, boolean>;
};

/** One uploaded-but-not-yet-submitted file's extraction settings — stems,
 * sample rate, bit depth, channel layout — mirroring the JobComposer's
 * `StemsSection` toggle-pill pattern (see `StemSelectorGrid`) but without
 * gain/EQ, which only exist once a track has actually been separated. */
export function AssetStagingRow({
  asset,
  availableStems,
  sampleRates,
  subtypes,
  channelLayouts,
  referenceModels,
  debleedModels,
  onChange,
  onRemove,
}: {
  asset: StagedAsset;
  availableStems: string[];
  sampleRates: number[];
  subtypes: string[];
  channelLayouts: string[];
  referenceModels: string[];
  debleedModels: string[];
  onChange: (patch: Partial<StagedAsset>) => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-md border p-3">
      <div className="mb-2.5 flex items-center gap-2">
        <p className="min-w-0 flex-1 truncate text-[13px] font-medium">{asset.file.name}</p>
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">{formatBytes(asset.file.size)}</span>
        <Button variant="ghost" size="icon" className="h-6 w-6" aria-label={`Remove ${asset.file.name}`} onClick={onRemove}>
          <X />
        </Button>
      </div>
      <div className="mb-2.5 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2">
        <SelectField
          label="Sample rate"
          value={String(asset.sampleRate)}
          onChange={(value) => onChange({ sampleRate: Number(value) })}
          options={sampleRates.map((value) => ({ value: String(value), label: `${value / 1000} kHz` }))}
        />
        <SelectField
          label="Bit depth"
          value={asset.subtype}
          onChange={(subtype) => onChange({ subtype })}
          options={subtypes.map((value) => ({ value, label: value }))}
        />
        <SelectField
          label="Channel layout"
          value={asset.channelLayout}
          onChange={(channelLayout) => onChange({ channelLayout })}
          options={channelLayouts.map((value) => ({ value, label: value }))}
        />
      </div>
      <StemSelectorGrid available={availableStems} selected={asset.stems} onChange={(stems) => onChange({ stems })} />
      <details className="mt-2.5 rounded-md border">
        <summary className="cursor-pointer px-3 py-2 text-[13px] font-medium">
          Bleed reduction
          <span className="ml-2 text-[11px] font-normal text-muted-foreground">
            Phase-fixer and debleed passes
          </span>
        </summary>
        <div className="space-y-3 border-t p-3">
          <ToggleField
            label="Bleed reduction"
            description="Phase-fixer during separation, on by default for diffuse/surround-oriented stems (not affected by later 3D placement). Debleed is opt-in per stem. Off by default."
            checked={asset.bleedReduction}
            onChange={(bleedReduction) => onChange({ bleedReduction })}
          />
          {asset.bleedReduction && (
            <>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2">
                <SelectField
                  label="Phase-fix reference model"
                  value={asset.phaseFixReferenceModel}
                  onChange={(phaseFixReferenceModel) => onChange({ phaseFixReferenceModel })}
                  options={referenceModels.map((model) => ({ value: model, label: model }))}
                />
                <SelectField
                  label="Debleed model"
                  value={asset.debleedModel}
                  hint="Enable per stem below; one inference per stem."
                  onChange={(debleedModel) => onChange({ debleedModel })}
                  options={debleedModels.map((model) => ({ value: model, label: model }))}
                />
              </div>
              <div className="space-y-1.5">
                <p className="text-[11px] font-medium uppercase tracking-[.08em] text-muted-foreground">
                  Debleed stems
                </p>
                <div className="grid gap-x-3 sm:grid-cols-2">
                  {asset.stems.map((stem) => (
                    <SwitchRow
                      key={stem}
                      label={stem}
                      checked={Boolean(asset.debleed[stem])}
                      onChange={(on) => {
                        const next = { ...asset.debleed };
                        if (on) next[stem] = true;
                        else delete next[stem];
                        onChange({ debleed: next });
                      }}
                    />
                  ))}
                </div>
              </div>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2">
                <NumberField
                  label="Phase-fix low cutoff"
                  value={asset.phaseFixLowHz}
                  min={1}
                  step={50}
                  suffix="Hz"
                  onChange={(value) => {
                    if (value != null) onChange({ phaseFixLowHz: value });
                  }}
                />
                <NumberField
                  label="Phase-fix high cutoff"
                  value={asset.phaseFixHighHz}
                  min={1}
                  step={100}
                  suffix="Hz"
                  onChange={(value) => {
                    if (value != null) onChange({ phaseFixHighHz: value });
                  }}
                />
                <SliderField
                  label="Phase-fix scale"
                  value={asset.phaseFixScale}
                  min={0.05}
                  max={1}
                  step={0.05}
                  onChange={(phaseFixScale) => onChange({ phaseFixScale })}
                />
              </div>
            </>
          )}
        </div>
      </details>
    </div>
  );
}
