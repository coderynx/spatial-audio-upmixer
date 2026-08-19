import * as React from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pot } from "@/components/ui/pot";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";

type Option = { value: string; label: string; disabled?: boolean };

/** Field pairs reflow on available width rather than viewport width, so a
 * narrow inspector pane gets one column and a wide dialog gets several. */
export const FIELD_GRID = "grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3";

/** Compact label/switch row for a parameter that happens to be boolean — no
 * description block, because the label already says it. Use `ToggleField`
 * only where the description carries something the label cannot. */
export function SwitchRow({
  label,
  hint,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex min-h-7 items-center justify-between gap-3">
      <span className="min-w-0">
        <span className="block text-[13px]">{label}</span>
        {hint && <span className="block text-[11px] text-muted-foreground">{hint}</span>}
      </span>
      <Switch aria-label={label} checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </div>
  );
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  hint,
  disabled = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <select
        value={value}
        aria-label={label}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="flex h-7 w-full rounded-md border border-input bg-secondary px-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-ring/60 disabled:opacity-40"
      >
        {options.map((option) => (
          <option
            key={option.value}
            value={option.value}
            disabled={option.disabled}
          >
            {option.label}
          </option>
        ))}
      </select>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function SliderField({
  label,
  value,
  min,
  max,
  step,
  onChange,
  suffix = "",
  disabled = false,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  suffix?: string;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label>{label}</Label>
        <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[11px] tabular-nums">
          {value.toFixed(step < 0.1 ? 2 : 1)}
          {suffix}
        </span>
      </div>
      <Slider
        aria-label={label}
        value={[value]}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onValueChange={([next]) => onChange(next)}
      />
    </div>
  );
}

export function NullablePotField({
  label,
  value,
  defaultValue,
  min,
  max,
  step,
  suffix = "",
  disabled = false,
  onChange,
}: {
  label: string;
  value: number | null;
  defaultValue: number;
  min: number;
  max: number;
  step: number;
  suffix?: string;
  disabled?: boolean;
  onChange: (value: number | null) => void;
}) {
  return (
    <Pot
      label={label}
      value={value ?? defaultValue}
      min={min}
      max={max}
      step={step}
      suffix={suffix}
      disabled={disabled}
      inherited={value == null}
      inheritedHint="from profile, double-click to restore"
      onChange={onChange}
      onReset={() => onChange(null)}
    />
  );
}

export function NumberField({
  label,
  value,
  onChange,
  step = 0.1,
  min,
  suffix,
  hint,
  disabled = false,
}: {
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  step?: number;
  min?: number;
  suffix?: string;
  hint?: string;
  disabled?: boolean;
}) {
  const [draft, setDraft] = React.useState(value == null ? "" : String(value));
  React.useEffect(() => setDraft(value == null ? "" : String(value)), [value]);
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <div className="relative">
        <Input
          type="number"
          value={draft}
          min={min}
          step={step}
          disabled={disabled}
          onChange={(event) => {
            setDraft(event.target.value);
            onChange(
              event.target.value === "" ? null : Number(event.target.value),
            );
          }}
          className={suffix ? "pr-14" : undefined}
        />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
            {suffix}
          </span>
        )}
      </div>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function ToggleField({
  label,
  description,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-md border bg-muted/40 p-2.5">
      <div>
        <Label>{label}</Label>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          {description}
        </p>
      </div>
      <Switch aria-label={label} checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </div>
  );
}
