import { Switch } from "@/components/ui/switch";

export function StemEffectHeader({ label, enabled, onEnabledChange, preset, presets, onPresetChange, onReset }: {
  label: string;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  preset: string | null;
  presets: string[];
  onPresetChange: (preset: string) => void;
  onReset: () => void;
}) {
  return <div className="flex items-center gap-2">
    <Switch aria-label={`${label} enabled`} checked={enabled} onCheckedChange={onEnabledChange} />
    <select aria-label={`${label} preset`} className="flex h-7 min-w-0 flex-1 rounded-md border bg-secondary px-2 text-[13px] text-foreground" value={preset ?? "custom"} onChange={(event) => onPresetChange(event.target.value)}>
      <option value="custom">Custom</option>
      {presets.map((name) => <option key={name} value={name}>{name}</option>)}
    </select>
    <button type="button" className="text-[11px] text-primary" onClick={onReset}>Reset</button>
  </div>;
}
