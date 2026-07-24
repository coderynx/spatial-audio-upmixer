import type { StemRouting } from "@/api";

type Speaker = { channel: string; label: string; x: number; y: number; height?: boolean };

const speakers: Record<string, Speaker> = {
  FL: { channel: "FL", label: "L", x: 30, y: 76 },
  FR: { channel: "FR", label: "R", x: 70, y: 76 },
  C: { channel: "C", label: "C", x: 50, y: 82 },
  SL: { channel: "SL", label: "Ls", x: 8, y: 49 },
  SR: { channel: "SR", label: "Rs", x: 92, y: 49 },
  BL: { channel: "BL", label: "Lrs", x: 16, y: 20 },
  BR: { channel: "BR", label: "Rrs", x: 84, y: 20 },
  TFL: { channel: "TFL", label: "Ltf", x: 31, y: 66, height: true },
  TFR: { channel: "TFR", label: "Rtf", x: 69, y: 66, height: true },
  TBL: { channel: "TBL", label: "Ltb", x: 22, y: 31, height: true },
  TBR: { channel: "TBR", label: "Rtb", x: 78, y: 31, height: true },
};

function maxWeight(route: Record<string, number>) {
  return Math.max(0.001, ...Object.values(route));
}

function stemMarker(route: Record<string, number>) {
  const weights = {
    front: ["FL", "FR", "C", "TFL", "TFR"],
    middle: ["SL", "SR"],
    back: ["BL", "BR", "TBL", "TBR"],
    floor: ["FL", "FR", "C", "SL", "SR", "BL", "BR"],
    height: ["TFL", "TFR", "TBL", "TBR"],
  };
  const sum = (names: string[]) => names.reduce((total, name) => total + (route[name] || 0), 0);
  const front = sum(weights.front);
  const middle = sum(weights.middle);
  const back = sum(weights.back);
  const total = front + middle + back || 1;
  const height = sum(weights.height) / (sum(weights.height) + sum(weights.floor) || 1);
  return { x: 50, y: 74 - 44 * ((middle * 0.5 + back) / total), height };
}

export function SpatialScene({
  channels,
  routing,
  selectedStem,
  colors,
  onSelectStem,
  className,
}: {
  channels: string[];
  routing: StemRouting;
  selectedStem: string | null;
  colors: Record<string, string>;
  onSelectStem: (stem: string | null) => void;
  className?: string;
}) {
  const route = selectedStem ? routing[selectedStem] || {} : {};
  const max = maxWeight(route);
  const marker = stemMarker(route);
  return <div className={`flex min-h-[280px] flex-col overflow-hidden rounded-lg border bg-slate-950 p-3 text-slate-100 ${className || ""}`}>
    <div className="mb-2 flex flex-none items-center justify-between text-xs text-slate-300"><span>Speaker routing</span><button className="hover:text-white" onClick={() => onSelectStem(null)}>{selectedStem || "Aggregate output"}</button></div>
    <div className="flex min-h-0 flex-1 items-center justify-center">
      <svg className="aspect-square h-full max-h-[560px] w-full max-w-[560px]" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Speaker routing map">
        <circle cx="50" cy="52" r="35" fill="none" stroke="#334155" strokeDasharray="2 2" />
        <circle cx="50" cy="52" r="24" fill="none" stroke="#475569" />
        <text x="50" y="96" textAnchor="middle" fontSize="3" fill="#94a3b8">FRONT</text>
        <text x="50" y="8" textAnchor="middle" fontSize="3" fill="#94a3b8">BACK</text>
        {channels.filter((channel) => channel !== "LFE").map((channel) => {
          const speaker = speakers[channel];
          if (!speaker) return null;
          const weight = route[channel] || 0;
          return <g key={channel}>
            {selectedStem && weight > 0 && <line x1="50" y1="52" x2={speaker.x} y2={speaker.y} stroke={colors[selectedStem] || "#60a5fa"} strokeWidth={0.5 + 2.8 * weight / max} opacity={0.35 + 0.65 * weight / max} />}
            <circle cx={speaker.x} cy={speaker.y} r={speaker.height ? 4.3 : 3.7} fill={speaker.height ? "#334155" : "#1e293b"} stroke={weight > 0 ? colors[selectedStem || ""] || "#60a5fa" : "#94a3b8"} strokeWidth={weight > 0 ? 1.2 : 0.5} />
            <text x={speaker.x} y={speaker.y + 1} textAnchor="middle" fontSize="3" fill="white">{speaker.label}</text>
          </g>;
        })}
        <circle cx="50" cy="52" r="4" fill="#e2e8f0" />
        <text x="50" y="53" textAnchor="middle" fontSize="2.6" fill="#0f172a">YOU</text>
        {selectedStem && <g><circle cx={marker.x} cy={marker.y} r={4 + marker.height * 2} fill={colors[selectedStem] || "#60a5fa"} opacity="0.9" /><text x={marker.x} y={marker.y + 1} textAnchor="middle" fontSize="2.3" fill="white">{Math.round(marker.height * 100)}↑</text></g>}
      </svg>
    </div>
    {channels.includes("LFE") && <div className="mt-1 flex-none rounded bg-slate-800 px-2 py-1 text-xs">LFE {selectedStem ? `${(route.LFE || 0).toFixed(2)} send` : "bus"}</div>}
  </div>;
}
