import { AudioLines } from "lucide-react";
import type { ProjectTrack } from "@/api";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

/** Persistent per-track switcher for Mixing/Mastering/Delivery — the device
 * that makes "these three stages are per-track, Assets is project-wide"
 * explicit. Replaces the old Mixing-only `<select>` that only appeared with
 * more than one track; this one is always visible whenever a track stage is
 * active, so switching tracks no longer requires stepping back into Mixing
 * first. */
export function TrackSwitcher({
  tracks,
  value,
  onChange,
}: {
  tracks: ProjectTrack[];
  value: string | null;
  onChange: (trackId: string) => void;
}) {
  if (tracks.length === 0) return null;
  const selected = tracks.find((track) => track.id === value);
  const layout = (selected?.manifest_overrides as { mixing?: { channel_layout?: string } } | undefined)?.mixing
    ?.channel_layout;
  return (
    <Select value={value ?? undefined} onValueChange={onChange}>
      <SelectTrigger aria-label="Track" className="h-6 w-auto min-w-0 max-w-40 shrink gap-1.5 px-2 text-[11px]">
        <span className="flex min-w-0 items-center gap-1.5">
          <AudioLines className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
          <SelectValue>
            <span className="truncate">{selected?.asset.title || selected?.asset.filename || "Select track"}</span>
          </SelectValue>
          {layout && <span className="hidden shrink-0 text-muted-foreground sm:inline">· {layout}</span>}
        </span>
      </SelectTrigger>
      <SelectContent>
        {tracks.map((track) => (
          <SelectItem key={track.id} value={track.id} className="text-[12px]">
            <span className="min-w-0 flex-1 truncate">{track.asset.title || track.asset.filename}</span>
            <span className="shrink-0 text-[11px] text-muted-foreground">{track.stems.length} stems</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
