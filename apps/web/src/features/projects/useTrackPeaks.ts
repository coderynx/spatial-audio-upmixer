import * as React from "react";
import type { ProjectTrack } from "@/api";

/** One stem's waveform envelope: paired per-bin minima and maxima, signed
 * bytes as written by upmixer_web/project_storage.py::_compute_peaks. */
export type StemPeaks = { min: Int8Array; max: Int8Array };

export type TrackPeaks = {
  bins: number;
  duration: number;
  stems: Map<string, StemPeaks>;
};

// One in-flight/settled promise per URL. The server versions `peaks_url` with
// the stem generation the envelopes were built from, so a re-prepare produces
// a different key and this cache never serves a stale waveform.
const cache = new Map<string, Promise<TrackPeaks>>();

export function parseTrackPeaks(
  buffer: ArrayBuffer, stemKeys: string[], bins: number, duration: number,
): TrackPeaks {
  const samples = new Int8Array(buffer);
  const stems = new Map<string, StemPeaks>();
  for (let index = 0; index < stemKeys.length; index += 1) {
    const start = index * bins * 2;
    if (start + bins * 2 > samples.length) break;
    const min = new Int8Array(bins);
    const max = new Int8Array(bins);
    for (let bin = 0; bin < bins; bin += 1) {
      min[bin] = samples[start + bin * 2];
      max[bin] = samples[start + bin * 2 + 1];
    }
    stems.set(stemKeys[index].split("@", 1)[0], { min, max });
  }
  return { bins, duration, stems };
}

function loadTrackPeaks(
  url: string, stemKeys: string[], bins: number, duration: number,
): Promise<TrackPeaks> {
  const existing = cache.get(url);
  if (existing) return existing;
  const promise = fetch(url)
    .then((response) => {
      if (!response.ok) throw new Error(`Waveform peaks unavailable (${response.status})`);
      return response.arrayBuffer();
    })
    .then((buffer) => parseTrackPeaks(buffer, stemKeys, bins, duration))
    .catch((reason) => {
      cache.delete(url);
      throw reason;
    });
  cache.set(url, promise);
  return promise;
}

/** Fetches a track's precomputed waveform envelopes exactly once per URL.
 *
 * Deliberately independent of `useStemPreview`: the envelope and the track's
 * duration arrive in one small binary, so the timeline ruler and lanes can
 * draw while the browser is still decoding stems for playback. */
export function useTrackPeaks(track: ProjectTrack | null): {
  peaks: TrackPeaks | null;
  loading: boolean;
} {
  const url = track?.peaks_url || null;
  const bins = track?.peaks_bins || 0;
  const duration = track?.peaks_duration_seconds || 0;
  // Serialized rather than passed by identity: the array is freshly parsed
  // from JSON on every poll, so its reference changes even when its contents
  // do not.
  const stemKeysJson = JSON.stringify(track?.peaks_stem_keys || []);
  const [peaks, setPeaks] = React.useState<TrackPeaks | null>(null);
  const [loading, setLoading] = React.useState(false);
  React.useEffect(() => {
    if (!url || !bins) {
      setPeaks(null);
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    loadTrackPeaks(url, JSON.parse(stemKeysJson) as string[], bins, duration)
      .then((next) => { if (active) setPeaks(next); })
      .catch(() => { if (active) setPeaks(null); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [url, bins, duration, stemKeysJson]);
  return { peaks, loading };
}
