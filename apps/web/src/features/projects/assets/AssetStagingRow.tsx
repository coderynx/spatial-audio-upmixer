import { X } from "lucide-react";
import { SelectField } from "@/components/forms/fields";
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
  onChange,
  onRemove,
}: {
  asset: StagedAsset;
  availableStems: string[];
  sampleRates: number[];
  subtypes: string[];
  channelLayouts: string[];
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
    </div>
  );
}
