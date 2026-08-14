import * as React from "react";
import { ArrowLeftRight, ArrowUpDown, AudioWaveform, Waves } from "lucide-react";
import { Slider } from "@/components/ui/slider";
import { panWeights, stemPan } from "@/lib/spatial";

export const StemControls = React.memo(function StemControls({ route, channels, eq, onRoute, onEq, stemEqProfiles }: { route: Record<string, number>; channels: string[]; eq: string; onRoute: (patch: Record<string, number>) => void; onEq: (eq: string) => void; stemEqProfiles?: string[] }) {
  const position = routePosition(route, channels);
  const setPosition = (patch: Partial<typeof position>) => onRoute(routeForPosition(channels, { ...position, ...patch }, route.LFE || 0));
  const stereo = channels.length === 2;
  const hasHeight = channels.includes("TFL") || channels.includes("TFR") || channels.includes("TBL") || channels.includes("TBR");
  const hasLfe = channels.includes("LFE");
  const stemEqSelect = <label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><AudioWaveform className="h-3 w-3" />EQ</span><select className="mt-1.5 flex h-7 w-full rounded-md border bg-secondary px-2 text-[13px] text-foreground" value={eq} onChange={(event) => onEq(event.target.value)}><option value="">None</option>{(stemEqProfiles ?? []).filter((name) => name !== "flat").map((name) => <option key={name} value={name}>{name}</option>)}</select></label>;
  if (stereo) return <div className="space-y-3"><label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><ArrowLeftRight className="h-3 w-3" />Left <span className="ml-auto">Right</span></span><Slider aria-label="Left to right" className="mt-1.5" min={0} max={1} step={0.01} value={[stemPan(route)]} onValueChange={([pan]) => onRoute(panWeights(route, pan))} /></label>{stemEqSelect}</div>;
  return <div className="space-y-3"><label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><ArrowLeftRight className="h-3 w-3" />Front <span className="ml-auto">Back</span></span><Slider aria-label="Front to back" className="mt-1.5" min={0} max={1} step={0.01} value={[position.depth]} onValueChange={([depth]) => setPosition({ depth })} /></label>{hasHeight &&<label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><ArrowUpDown className="h-3 w-3" />Floor <span className="ml-auto">Height</span></span><Slider aria-label="Floor to height" className="mt-1.5" min={0} max={1} step={0.01} value={[position.height]} onValueChange={([height]) => setPosition({ height })} /></label>}{hasLfe && <label className="block text-[11px] text-muted-foreground"><span className="flex items-center gap-1"><Waves className="h-3 w-3" />LFE send</span><Slider aria-label="LFE send" className="mt-1.5" min={0} max={1} step={0.01} value={[route.LFE ?? 0]} onValueChange={([lfe]) => onRoute({ LFE: lfe })} /></label>}{stemEqSelect}</div>;
});

function routePosition(route: Record<string, number>, channels: string[]) {
  const weight = (names: string[]) => names.reduce((total, name) => total + (route[name] || 0), 0);
  const top = weight(["TFL", "TFR", "TBL", "TBR"]);
  const floor = weight(["FL", "FR", "C", "SL", "SR", "BL", "BR"]);
  const front = weight(["FL", "FR", "C", "TFL", "TFR"]);
  const hasBack = channels.includes("BL") || channels.includes("BR");
  const side = weight(["SL", "SR"]);
  const back = weight(["BL", "BR", "TBL", "TBR"]);
  const middle = hasBack ? side : 0;
  const rear = hasBack ? back : side;
  const total = front + middle + rear || 1;
  return { depth: Math.min(1, Math.max(0, (middle * 0.5 + rear) / total)), height: Math.min(1, Math.max(0, top / (top + floor || 1))) };
}

function routeForPosition(channels: string[], position: { depth: number; height: number }, lfe: number) {
  const present = new Set(channels);
  const hasBack = present.has("BL") || present.has("BR");
  const front = hasBack ? Math.max(0, 1 - position.depth * 2) : 1 - position.depth;
  const middle = hasBack ? 1 - Math.abs(position.depth * 2 - 1) : 0;
  const back = hasBack ? Math.max(0, position.depth * 2 - 1) : position.depth;
  const floor = 1 - position.height;
  const route: Record<string, number> = Object.fromEntries(channels.map((channel) => [channel, 0]));
  const send = (names: string[], total: number) => {
    const available = names.filter((channel) => present.has(channel));
    for (const channel of available) route[channel] = total / available.length;
  };
  send(["FL", "FR", "C"], floor * front);
  send(["SL", "SR"], floor * (middle + (hasBack ? 0 : back)));
  send(["BL", "BR"], floor * back);
  send(["TFL", "TFR"], position.height * (1 - position.depth));
  send(["TBL", "TBR"], position.height * position.depth);
  if (present.has("LFE")) route.LFE = lfe;
  return route;
}
