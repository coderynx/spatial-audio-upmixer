import {
  AudioWaveform,
  Drum,
  Guitar,
  MicVocal,
  Music2,
  Piano,
  UsersRound,
  Waves,
  Speech,
  type LucideIcon,
} from "lucide-react";

export const stemColors: Record<string, string> = {
  Vocals: "#f43f5e",
  Bass: "#14b8a6",
  Drums: "#f97316",
  Guitar: "#10b981",
  Piano: "#8b5cf6",
  Other: "#64748b",
  Kick: "#ef4444",
  Snare: "#ec4899",
  Toms: "#84cc16",
  "Hi-Hat": "#eab308",
  Ride: "#06b6d4",
  Crash: "#0ea5e9",
  Crowd: "#3b82f6",
  "Lead Vocals": "#f43f5e",
  "Backing Vocals": "#d946ef",
  "Vocals Reverb": "#fb7185",
};

const bedStemNames = new Set([
  "Bass", "Kick", "Snare", "Other", "Crowd", "Backing Vocals", "Vocals Reverb",
]);

const stemIcons: Record<string, LucideIcon> = {
  vocals: MicVocal,
  bass: Waves,
  drums: Drum,
  kick: Drum,
  snare: Drum,
  toms: Drum,
  guitar: Guitar,
  piano: Piano,
  "hi-hat": Drum,
  ride: Drum,
  crash: Drum,
  crowd: UsersRound,
  "lead vocals": MicVocal,
  "backing vocals": Speech,
  "vocals reverb": AudioWaveform,
  other: Music2,
};

function stemKey(stem: string) {
  return stem.toLowerCase();
}

export function getStemColor(stem: string) {
  return stemColors[stem] || "#94a3b8";
}

export function getStemIcon(stem: string): LucideIcon {
  return stemIcons[stemKey(stem)] || Music2;
}

export function isBedStem(stem: string) {
  return bedStemNames.has(stem.split("@", 1)[0]);
}
