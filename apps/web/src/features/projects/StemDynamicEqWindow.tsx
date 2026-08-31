import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import type { StemDynamicEqBand, StemDynamicEqSettings } from "@/lib/manifest";
import { getStemColor, getStemIcon } from "@/lib/stems";
import { FloatingWindow } from "./FloatingWindow";
import { StemEffectHeader } from "./StemEffectHeader";
import { GainReductionMeter } from "./StemDynamicsWindow";
import { StemEffectTrigger } from "./StemEffectTrigger";

const neutralBand = (): StemDynamicEqBand => ({ enabled: false, freq_hz: 1000, q: 1, threshold_db: -24, ratio: 1, max_cut_db: 0, attack_ms: 20, release_ms: 200 });
export const defaultStemDynamicEq: StemDynamicEqSettings = { enabled: false, profile: null, bands: [], mix: 100 };

export function StemDynamicEqWindow({ stemName, value, profiles, onChange, meterSource }: {
  stemName: string; value?: StemDynamicEqSettings; profiles?: Record<string, StemDynamicEqBand[]>;
  onChange: (value: StemDynamicEqSettings | null) => void; meterSource?: () => number;
}) {
  const settings = value ?? defaultStemDynamicEq;
  const StemIcon = getStemIcon(stemName);
  const stemColor = getStemColor(stemName);
  const bands = [settings.bands[0] ?? neutralBand(), settings.bands[1] ?? neutralBand()];
  const edit = (patch: Partial<StemDynamicEqSettings>) => onChange({ ...settings, ...patch });
  const editBand = (index: number, patch: Partial<StemDynamicEqBand>) => edit({ profile: null, bands: bands.map((band, i) => i === index ? { ...band, ...patch } : band) });
  const number = (index: number, label: string, key: Exclude<keyof StemDynamicEqBand, "enabled">, min: number, max: number, step: number, suffix: string, logarithmic = false) => {
    const value = bands[index][key] as number;
    const sliderValue = logarithmic ? Math.log(value / min) / Math.log(max / min) : value;
    return <label className="block text-[11px] text-muted-foreground"><span>{label}<span className="float-right tabular-nums">{key === "freq_hz" ? Math.round(value) : value}{suffix}</span></span><Slider aria-label={`${label} ${index + 1}`} className="mt-1.5" min={logarithmic ? 0 : min} max={logarithmic ? 1 : max} step={logarithmic ? .001 : step} value={[sliderValue]} onValueChange={([next]) => editBand(index, { [key]: logarithmic ? min * (max / min) ** next : next })} /></label>;
  };
  return <FloatingWindow title={`${stemName} Tame`} ariaLabel="Tame" borderColor={stemColor} icon={<StemIcon className="mr-1.5 h-3.5 w-3.5 shrink-0" style={{ color: stemColor }} aria-hidden="true" />} trigger={(open, expanded) => <StemEffectTrigger active={settings.enabled} label="Tame" ariaLabel="Open tame dynamic EQ" expanded={expanded} onOpen={open} onToggle={() => edit({ enabled: !settings.enabled })} />}><div className="space-y-3"><StemEffectHeader label="Tame" enabled={settings.enabled} onEnabledChange={(enabled) => edit({ enabled })} preset={settings.profile ?? null} presets={Object.keys(profiles ?? {})} onPresetChange={(name) => edit(name === "custom" ? { profile: null } : { enabled: true, profile: name, bands: (profiles?.[name] ?? []).map((band) => ({ ...band })) })} onReset={() => onChange(null)} />{bands.map((band, index) => <details key={index} open={index === 0} className="border-t pt-2"><summary className="flex cursor-pointer list-none items-center text-[11px] font-medium"><span>Band {index + 1}</span><Switch className="ml-auto" aria-label={`Tame band ${index + 1} enabled`} checked={band.enabled} onClick={(event) => event.stopPropagation()} onCheckedChange={(enabled) => editBand(index, { enabled })} /></summary><div className="mt-2 space-y-2.5">{number(index, "Frequency", "freq_hz", 20, 20000, 1, " Hz", true)}{number(index, "Q", "q", .5, 8, .1, "")}{number(index, "Threshold", "threshold_db", -48, -6, 1, " dBFS")}{number(index, "Ratio", "ratio", 1, 6, .1, ":1")}{number(index, "Maximum cut", "max_cut_db", 0, 6, .1, " dB")}{number(index, "Attack", "attack_ms", 1, 100, 1, " ms")}{number(index, "Release", "release_ms", 30, 600, 5, " ms")}</div></details>)}<label className="block text-[11px] text-muted-foreground">Mix <span className="float-right tabular-nums">{settings.mix}%</span><Slider aria-label="Tame mix" className="mt-1.5" min={0} max={100} step={1} value={[settings.mix]} onValueChange={([mix]) => edit({ mix })} /></label><GainReductionMeter source={meterSource} /></div></FloatingWindow>;
}
