import * as React from "react";
import { Album, Pause, Play } from "lucide-react";
import type { ImportPreview } from "@/api";
import { Button } from "@/components/ui/button";
import { formatBytes, formatDuration } from "@/lib/format";

export function AlbumOverview({ preview }: { preview: ImportPreview }) {
  const audio = React.useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = React.useState<string | null>(null);
  const [playbackError, setPlaybackError] = React.useState<string | null>(null);
  React.useEffect(() => () => audio.current?.pause(), []);
  React.useEffect(() => {
    audio.current?.pause();
    setPlaying(null);
    setPlaybackError(null);
  }, [preview.id]);
  const toggle = async (assetId: string, audioUrl: string | null) => {
    const player = audio.current;
    if (!player || !audioUrl) return;
    setPlaybackError(null);
    if (playing === assetId && !player.paused) {
      player.pause();
      setPlaying(null);
      return;
    }
    player.src = audioUrl;
    try {
      await player.play();
      setPlaying(assetId);
    } catch {
      setPlaying(null);
      setPlaybackError("Browser could not play this source file.");
    }
  };
  return (
    <div className="rounded-md border bg-card p-4 sm:p-5">
      <audio
        ref={audio}
        className="hidden"
        onEnded={() => setPlaying(null)}
        onPause={() => setPlaying(null)}
      />
      <div className="grid gap-5 lg:grid-cols-[160px_1fr]">
        <div className="aspect-square overflow-hidden rounded-md border bg-muted">
          {preview.cover_url ? (
            <img
              src={preview.cover_url}
              alt={`${preview.title || "Album"} cover`}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <Album className="h-12 w-12 text-muted-foreground" />
            </div>
          )}
        </div>
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {preview.kind}
          </p>
          <h3 className="mt-1 truncate text-xl font-semibold">
            {preview.title || "Untitled import"}
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {[
              preview.artist,
              preview.release_date?.slice(0, 4),
              `${preview.assets.length} track${preview.assets.length === 1 ? "" : "s"}`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <div className="mt-4 max-h-56 overflow-y-auto rounded-md border">
            {preview.assets.map((asset, index) => (
              <div
                key={asset.id}
                className="grid grid-cols-[2rem_2rem_1fr_auto] items-center gap-2 border-b px-3 py-2 last:border-0"
              >
                <span className="font-mono text-xs text-muted-foreground">
                  {String(asset.track_number || index + 1).padStart(2, "0")}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  disabled={!asset.audio_url}
                  aria-label={`${playing === asset.id ? "Pause" : "Preview"} ${asset.title || asset.filename}`}
                  onClick={() => void toggle(asset.id, asset.audio_url)}
                >
                  {playing === asset.id ? <Pause /> : <Play />}
                </Button>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {asset.title || asset.filename}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {[
                      asset.artist || asset.relative_path,
                      asset.sample_rate
                        ? `${asset.sample_rate / 1000} kHz`
                        : null,
                      asset.channels ? `${asset.channels} ch` : null,
                      formatBytes(asset.size_bytes),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {formatDuration(asset.duration_seconds)}
                </span>
              </div>
            ))}
          </div>
          {playbackError && (
            <p className="mt-2 text-xs text-destructive">{playbackError}</p>
          )}
        </div>
      </div>
    </div>
  );
}
