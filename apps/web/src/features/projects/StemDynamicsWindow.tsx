import * as React from "react";
import { Slider } from "@/components/ui/slider";
import type { StemDynamicsSettings } from "@/lib/manifest";
import { getStemColor, getStemIcon } from "@/lib/stems";
import { FloatingWindow } from "./FloatingWindow";
import { StemEffectHeader } from "./StemEffectHeader";
import { StemEffectTrigger } from "./StemEffectTrigger";

export const defaultStemDynamics: StemDynamicsSettings = {
  enabled: false, profile: null, threshold_db: -18, ratio: 1.5, attack_ms: 30, release_ms: 250, mix: 100,
};

export function GainReductionMeter({ source }: { source?: () => number }) {
  const bar = React.useRef<HTMLDivElement>(null);
  const value = React.useRef<HTMLSpanElement>(null);
  React.useEffect(() => {
    let frame = 0;
    const draw = () => {
      const gr = Math.min(6, Math.max(0, source?.() ?? 0));
      if (bar.current) bar.current.style.width = `${gr / 6 * 100}%`;
      if (value.current) value.current.textContent = `${gr.toFixed(1)} dB`;
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [source]);
  return <div className="border-t pt-2 text-[11px] text-muted-foreground"><div className="mb-1 flex"><span>Gain reduction</span><span ref={value} className="ml-auto tabular-nums">0.0 dB</span></div><div className="h-1.5 overflow-hidden rounded-full bg-secondary"><div ref={bar} className="h-full bg-primary" /></div></div>;
}

export function StemDynamicsWindow({ stemName, value, profiles, onChange, meterSource }: {
  stemName: string;
  value?: StemDynamicsSettings;
  profiles?: Record<string, StemDynamicsSettings>;
  onChange: (value: StemDynamicsSettings | null) => void;
  meterSource?: () => number;
}) {
  const settings = value ?? defaultStemDynamics;
  const StemIcon = getStemIcon(stemName);
  const stemColor = getStemColor(stemName);
  const edit = (patch: Partial<StemDynamicsSettings>) => onChange({ ...settings, ...patch, profile: null });
  const control = (label: string, key: keyof Omit<StemDynamicsSettings, "enabled">, min: number, max: number, step: number, suffix: string) => <label className="block text-[11px] text-muted-foreground"><span>{label}<span className="float-right tabular-nums">{settings[key]}{suffix}</span></span><Slider aria-label={label} className="mt-1.5" min={min} max={max} step={step} value={[settings[key] as number]} onValueChange={([next]) => edit({ [key]: next })} /></label>;
  return <FloatingWindow title={`${stemName} Gentle Dynamics`} ariaLabel="Gentle Dynamics" borderColor={stemColor} icon={<StemIcon className="mr-1.5 h-3.5 w-3.5 shrink-0" style={{ color: stemColor }} aria-hidden="true" />} trigger={(open, expanded) => <StemEffectTrigger active={settings.enabled} label="Dynamics" ariaLabel="Open gentle dynamics" expanded={expanded} onOpen={open} onToggle={() => onChange({ ...settings, enabled: !settings.enabled })} />}><div className="space-y-3"><StemEffectHeader label="Dynamics" enabled={settings.enabled} onEnabledChange={(enabled) => onChange({ ...settings, enabled })} preset={settings.profile ?? null} presets={Object.keys(profiles ?? {})} onPresetChange={(name) => onChange(name === "custom" ? { ...settings, profile: null } : { ...settings, ...profiles?.[name], enabled: true, profile: name })} onReset={() => onChange(null)} />{control("Threshold", "threshold_db", -36, -6, 1, " dBFS")}{control("Ratio", "ratio", 1, 3, .1, ":1")}{control("Attack", "attack_ms", 5, 80, 1, " ms")}{control("Release", "release_ms", 80, 600, 5, " ms")}{control("Mix", "mix", 0, 100, 1, "%")}<GainReductionMeter source={meterSource} /></div></FloatingWindow>;
}
