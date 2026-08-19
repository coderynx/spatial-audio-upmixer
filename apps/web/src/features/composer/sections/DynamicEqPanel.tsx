import * as React from "react";
import { Plus, X } from "lucide-react";
import { FIELD_GRID, SliderField } from "@/components/forms/fields";
import { Button } from "@/components/ui/button";
import type { DynamicEqBand } from "@/lib/manifest";
import { EffectPanel, FieldGroup } from "./EffectPanel";

/** A new band starts on the move this stage exists for: the 3-4 kHz region
 * a static curve can only cut all the time. */
const NEW_BAND: DynamicEqBand = {
  freq_hz: 3800,
  q: 2,
  threshold_db: -30,
  ratio: 3,
  attack_ms: 10,
  release_ms: 150,
};

/** Ranges mirror `manifest/validate.py`'s `_DYNEQ_BOUNDS` — a control that
 * could author a value the export rejects is a broken control. */
const CONTROLS = [
  ["freq_hz", "Frequency", " Hz", 1, 20, 20000, "log"],
  ["q", "Width", "", 0.1, 0.3, 12, "linear"],
  ["threshold_db", "Threshold", " dB", 0.5, -80, 0, "linear"],
  ["ratio", "Ratio", "", 0.1, 1, 20, "linear"],
  ["attack_ms", "Attack", " ms", 0.5, 0.1, 200, "linear"],
  ["release_ms", "Release", " ms", 5, 1, 2000, "linear"],
] as const;

export function DynamicEqPanel({
  bands,
  maxBands,
  onChange,
}: {
  bands: DynamicEqBand[];
  /** Served by the engine-constants endpoint, never assumed here. */
  maxBands: number | undefined;
  onChange: (bands: DynamicEqBand[]) => void;
}) {
  // Switching the effect off empties the list, which is what the manifest
  // means by "off"; remembering it means switching back on restores the bands
  // instead of silently discarding them.
  const remembered = React.useRef<DynamicEqBand[]>([]);
  const enabled = bands.length > 0;
  const cap = maxBands ?? 0;

  const setBand = (index: number, patch: Partial<DynamicEqBand>) =>
    onChange(bands.map((band, i) => (i === index ? { ...band, ...patch } : band)));

  return (
    <EffectPanel
      title="Dynamic EQ"
      enabled={enabled}
      toggleDisabled={!enabled && cap < 1}
      onEnabledChange={(next) => {
        if (!next) {
          remembered.current = bands;
          onChange([]);
          return;
        }
        onChange(remembered.current.length ? remembered.current : [{ ...NEW_BAND }]);
      }}
    >
      {bands.map((band, index) => (
        <FieldGroup key={index} title={`Band ${index + 1}`}>
          <div className={FIELD_GRID}>
            {CONTROLS.map(([key, label, suffix, step, min, max, scale]) => (
              <SliderField
                key={key}
                label={label}
                value={band[key]}
                min={min}
                max={max}
                step={step}
                scale={scale}
                suffix={suffix || undefined}
                onChange={(value) => setBand(index, { [key]: value })}
              />
            ))}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onChange(bands.filter((_, i) => i !== index))}
          >
            <X /> Remove band {index + 1}
          </Button>
        </FieldGroup>
      ))}
      {enabled && bands.length < cap && (
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => onChange([...bands, { ...NEW_BAND }])}
        >
          <Plus /> Add band
        </Button>
      )}
    </EffectPanel>
  );
}
