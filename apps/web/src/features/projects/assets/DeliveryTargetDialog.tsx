import * as React from "react";
import { FIELD_GRID, NumberField, SelectField, SwitchRow } from "@/components/forms/fields";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { Configuration } from "@/api";
import { CHANNEL_LAYOUTS, OUTPUT_TYPES } from "@/lib/layouts";
import type { Manifest } from "@/lib/manifest";

export type DeliveryTargetSettings = Pick<Manifest, "mixing" | "mastering" | "format">;

type Profile = { label: string; layout?: string; type?: string; target: number; maxTp: number; standard: string };
const PROFILES: Record<string, Profile> = {
  "atmos-music": { label: "Dolby Atmos Music ADM-BWF", layout: "7.1.2", type: "adm-bwf", target: -18, maxTp: -1, standard: "ITU-R BS.1770-4, 5.1 re-render" },
  "netflix-atmos-movie": { label: "Netflix Dolby Atmos Movie", target: -27, maxTp: -2, standard: "ITU-R BS.1770-1, speech gated" },
  "disney-plus-atmos-movie": { label: "Disney+ Dolby Atmos Movie", target: -27, maxTp: -2, standard: "ITU-R BS.1770-4, speech gated" },
  "amazon-prime-atmos-movie": { label: "Amazon Prime Video Dolby Atmos Movie", target: -27, maxTp: -2, standard: "ITU-R BS.1770-1, speech gated" },
  "apple-tv-plus-atmos-movie": { label: "Apple TV+ Dolby Atmos Movie", target: -27, maxTp: -2, standard: "ITU-R BS.1770-4, speech gated" },
  "max-atmos-movie": { label: "Max (HBO) Dolby Atmos Movie", target: -27, maxTp: -2, standard: "ITU-R BS.1770-4, speech gated" },
};

export function DeliveryTargetDialog({
  open, configuration, initial, onOpenChange, onCreate,
}: {
  open: boolean;
  configuration: Configuration | null;
  initial: Manifest;
  onOpenChange: (open: boolean) => void;
  onCreate: (settings: DeliveryTargetSettings) => Promise<void>;
}) {
  const [settings, setSettings] = React.useState<DeliveryTargetSettings>(() => pick(initial));
  const [profile, setProfile] = React.useState("unspecified");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => { if (open) { setSettings(pick(initial)); setProfile("unspecified"); setError(null); } }, [open]);
  const update = (patch: Partial<DeliveryTargetSettings>) => setSettings((current) => ({ ...current, ...patch }));
  const chooseProfile = (next: string) => {
    setProfile(next);
    const selected = PROFILES[next];
    if (selected) setSettings((current) => ({
      ...current,
      mixing: selected.layout ? { ...current.mixing, channel_layout: selected.layout } : current.mixing,
      format: { ...current.format, delivery_profile: next, ...(selected.type ? { type: selected.type, codec: "wav_pcm", subtype: "PCM_24", sample_rate: 48000 } : {}) },
      mastering: { ...current.mastering, loudness: { ...current.mastering.loudness, normalize: true, target_preset: null, target: selected.target, max_tp: selected.maxTp } },
    }));
    if (next === "unspecified") setSettings((current) => ({
      ...current,
      format: { ...current.format, delivery_profile: null },
      mastering: { ...current.mastering, loudness: { ...current.mastering.loudness, target_preset: null, target: null, max_tp: null } },
    }));
  };
  async function create() {
    setBusy(true); setError(null);
    try { await onCreate(settings); onOpenChange(false); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  const layouts = configuration?.choices.channel_layouts || CHANNEL_LAYOUTS;
  const formats = configuration?.choices.output_types || OUTPUT_TYPES;
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="w-[min(620px,92vw)]">
      <DialogHeader className="border-b px-4 py-3"><DialogTitle className="text-[16px]">Create delivery target</DialogTitle><DialogDescription>Choose a profile, then adjust any delivery setting.</DialogDescription></DialogHeader>
      <div className="space-y-3 overflow-y-auto p-4">
        <SelectField label="Delivery profile" value={profile} onChange={chooseProfile} options={[
          { value: "unspecified", label: "Unspecified" },
          ...Object.entries(PROFILES).map(([value, item]) => ({ value, label: item.label })),
        ]} />
        <div className={FIELD_GRID}>
          <SelectField label="Target channel layout" value={settings.mixing.channel_layout} onChange={(channel_layout) => update({ mixing: { ...settings.mixing, channel_layout } })} options={layouts.map((value) => ({ value, label: value }))} />
          <SelectField label="Format" value={settings.format.type} onChange={(type) => update({ format: { ...settings.format, type } })} options={formats.map((value) => ({ value, label: value === "adm-bwf" ? "ADM-BWF" : value }))} />
        </div>
        <div className="rounded-md border p-3"><p className="mb-3 text-[13px] font-medium">Loudness</p><div className={FIELD_GRID}>
          <NumberField label="Target loudness" value={settings.mastering.loudness.target} onChange={(target) => update({ mastering: { ...settings.mastering, loudness: { ...settings.mastering.loudness, target } } })} suffix="LKFS / LUFS" />
          <NumberField label="Maximum true peak" value={settings.mastering.loudness.max_tp} onChange={(max_tp) => update({ mastering: { ...settings.mastering, loudness: { ...settings.mastering.loudness, max_tp } } })} suffix="dBTP" />
        </div><div className="mt-3"><SwitchRow label="Normalize output" checked={settings.mastering.loudness.normalize} onChange={(normalize) => update({ mastering: { ...settings.mastering, loudness: { ...settings.mastering.loudness, normalize } } })} /></div></div>
        {profile !== "unspecified" && <p className="text-[11px] text-muted-foreground">{PROFILES[profile].standard}.</p>}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>
      <div className="flex justify-end gap-2 border-t p-3"><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button disabled={busy} onClick={() => void create()}>{busy ? "Creating…" : "Create target"}</Button></div>
    </DialogContent>
  </Dialog>;
}

export function DeleteDeliveryTargetDialog({
  open, target, onOpenChange, onConfirm,
}: {
  open: boolean;
  target: string | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => { if (open) setError(null); }, [open]);
  async function confirm() {
    setBusy(true); setError(null);
    try { await onConfirm(); onOpenChange(false); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="w-[min(420px,92vw)]">
      <DialogHeader className="border-b px-4 py-3"><DialogTitle className="text-[16px]">Delete delivery target?</DialogTitle><DialogDescription>{target} will be removed from this track.</DialogDescription></DialogHeader>
      {error && <p className="px-4 pt-3 text-xs text-destructive">{error}</p>}
      <div className="flex justify-end gap-2 p-3"><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="destructive" disabled={busy} onClick={() => void confirm()}>{busy ? "Deleting…" : "Delete target"}</Button></div>
    </DialogContent>
  </Dialog>;
}

function pick(manifest: Manifest): DeliveryTargetSettings {
  return { mixing: manifest.mixing, mastering: manifest.mastering, format: manifest.format };
}
