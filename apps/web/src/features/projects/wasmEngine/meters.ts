export type MeterLevel = { rms: number; peak: number; clipped: boolean };

export const SILENT_METER_LEVEL: MeterLevel = { rms: 0, peak: 0, clipped: false };

// A sample clearing unity by a hairline still counts as clipped.
const CLIP_TOLERANCE = 1.0;

function level(rms: number, peak: number): MeterLevel {
  return { rms, peak, clipped: peak > CLIP_TOLERANCE };
}

export type MeterFrame = { position: number; meters: number[]; spectrum: number[] };

/** `duck` is the transient ducker's mean gain over the meter window, 1 for
 * no reduction — see `PreviewEngine::stem_spectrum`. */
export type StemSpectrum = { level: number; centroid: number; duck: number };

export type DecodedMeters = {
  stemLevels: Map<string, MeterLevel[]>;
  stemSpectrum: Map<string, StemSpectrum>;
  channelLevels: Map<string, MeterLevel>;
  headphoneLevels: { left: MeterLevel; right: MeterLevel };
};

/**
 * Unpack one render-callback frame. The meter array is laid out as
 * `[stems…][channels…][headphone L/R]`, four values per stem (two per
 * channel of the stem) and two per output channel.
 */
export function decodeMeterFrame(
  frame: MeterFrame,
  stemOrder: string[],
  stemChannelCounts: number[],
  channels: string[],
): DecodedMeters {
  const meters = frame.meters;
  const stemCount = stemOrder.length;
  const stemLevels = new Map<string, MeterLevel[]>();
  const stemSpectrum = new Map<string, StemSpectrum>();
  for (let i = 0; i < stemCount; i += 1) {
    const o = i * 4;
    const bars = [level(meters[o] ?? 0, meters[o + 1] ?? 0)];
    if ((stemChannelCounts[i] ?? 1) >= 2) bars.push(level(meters[o + 2] ?? 0, meters[o + 3] ?? 0));
    stemLevels.set(stemOrder[i], bars);
    const s = i * 3;
    stemSpectrum.set(stemOrder[i], {
      level: frame.spectrum[s] ?? 0,
      centroid: frame.spectrum[s + 1] ?? 0,
      duck: frame.spectrum[s + 2] ?? 1,
    });
  }

  const channelLevels = new Map<string, MeterLevel>();
  const base = stemCount * 4;
  for (let i = 0; i < channels.length; i += 1) {
    channelLevels.set(channels[i], level(meters[base + i * 2] ?? 0, meters[base + i * 2 + 1] ?? 0));
  }

  const outBase = base + channels.length * 2;
  return {
    stemLevels,
    stemSpectrum,
    channelLevels,
    headphoneLevels: {
      left: level(meters[outBase] ?? 0, meters[outBase + 1] ?? 0),
      right: level(meters[outBase + 2] ?? 0, meters[outBase + 3] ?? 0),
    },
  };
}
