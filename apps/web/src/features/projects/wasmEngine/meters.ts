export type MeterLevel = { rms: number; peak: number; clipped: boolean };

export const SILENT_METER_LEVEL: MeterLevel = { rms: 0, peak: 0, clipped: false };

// A sample clearing unity by a hairline still counts as clipped.
const CLIP_TOLERANCE = 1.0;

function level(rms: number, peak: number): MeterLevel {
  return { rms, peak, clipped: peak > CLIP_TOLERANCE };
}

export type MeterFrame = { position: number; meters: number[]; spectrum: number[] };

export type StemSpectrum = { level: number; centroid: number };

/** The master strip's readouts — see `MasterMeters` in the core. LKFS over
 * the delivered programme; the core's gain-reduction floats follow these two
 * on the wire and are not read. */
export type MasterMeters = {
  momentaryLkfs: number;
  shortTermLkfs: number;
};

export const SILENT_MASTER_METERS: MasterMeters = {
  momentaryLkfs: -70,
  shortTermLkfs: -70,
};

export type DecodedMeters = {
  stemLevels: Map<string, MeterLevel[]>;
  stemDynamics: Map<string, number>;
  stemDynamicEq: Map<string, number>;
  stemSpectrum: Map<string, StemSpectrum>;
  channelLevels: Map<string, MeterLevel>;
  headphoneLevels: { left: MeterLevel; right: MeterLevel };
  master: MasterMeters;
};

/**
 * Unpack one render-callback frame. The meter array is laid out as
 * `[stems…][channels…][headphone L/R][master]`, four values per stem (two per
 * channel of the stem), two per output channel, and five for the master.
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
  const stemDynamics = new Map<string, number>();
  const stemDynamicEq = new Map<string, number>();
  const stemSpectrum = new Map<string, StemSpectrum>();
  for (let i = 0; i < stemCount; i += 1) {
    const o = i * 4;
    const bars = [level(meters[o] ?? 0, meters[o + 1] ?? 0)];
    if ((stemChannelCounts[i] ?? 1) >= 2) bars.push(level(meters[o + 2] ?? 0, meters[o + 3] ?? 0));
    stemLevels.set(stemOrder[i], bars);
    const s = i * 2;
    stemSpectrum.set(stemOrder[i], {
      level: frame.spectrum[s] ?? 0,
      centroid: frame.spectrum[s + 1] ?? 0,
    });
  }

  const dynamicsBase = stemCount * 4;
  for (let i = 0; i < stemCount; i += 1) stemDynamics.set(stemOrder[i], meters[dynamicsBase + i] ?? 0);
  const channelLevels = new Map<string, MeterLevel>();
  const base = dynamicsBase + stemCount;
  for (let i = 0; i < channels.length; i += 1) {
    channelLevels.set(channels[i], level(meters[base + i * 2] ?? 0, meters[base + i * 2 + 1] ?? 0));
  }

  const outBase = base + channels.length * 2;
  const masterBase = outBase + 4;
  const dynamicEqBase = masterBase + 5;
  for (let i = 0; i < stemCount; i += 1) stemDynamicEq.set(stemOrder[i], meters[dynamicEqBase + i] ?? 0);
  return {
    stemLevels,
    stemDynamics,
    stemDynamicEq,
    stemSpectrum,
    channelLevels,
    headphoneLevels: {
      left: level(meters[outBase] ?? 0, meters[outBase + 1] ?? 0),
      right: level(meters[outBase + 2] ?? 0, meters[outBase + 3] ?? 0),
    },
    master: {
      momentaryLkfs: meters[masterBase] ?? -70,
      shortTermLkfs: meters[masterBase + 1] ?? -70,
    },
  };
}
