// Loads the shipped FIR assets and flattens them into the tap layout the
// shared core expects.
//
// The assets themselves are backend-owned and byte-identical to the ones the
// export convolves — see docs/contracts/preview_export_parity.md §4. Nothing
// here designs a filter; it only rearranges what the server built.

import { fetchDecodeFilterPart, fetchXtcFilterSet } from "../audioLoaders";

/** The decode bank ships as four 8-channel WAVs, capped by browser decoding. */
const DECODE_PARTS = ["01-08ch", "09-16ch", "17-24ch", "25-32ch"];
const ACN_CHANNELS = 16;

function concatChannels(buffers: AudioBuffer[]): Float32Array[] {
  const channels: Float32Array[] = [];
  for (const buffer of buffers) {
    for (let i = 0; i < buffer.numberOfChannels; i += 1) {
      channels.push(buffer.getChannelData(i));
    }
  }
  return channels;
}

/**
 * Flatten interleaved `[left, right]` channel pairs into the core's
 * `[group][ear][tap]` order.
 */
function flattenPairs(channels: Float32Array[], groups: number): Float64Array {
  const taps = channels[0]?.length ?? 0;
  const out = new Float64Array(groups * 2 * taps);
  for (let group = 0; group < groups; group += 1) {
    for (let ear = 0; ear < 2; ear += 1) {
      const source = channels[group * 2 + ear];
      const base = (group * 2 + ear) * taps;
      for (let i = 0; i < taps; i += 1) out[base + i] = source ? source[i] : 0;
    }
  }
  return out;
}

/** Load an ambisonic decode bank as `[acn][ear][tap]`. */
export async function loadDecodeTaps(
  ctx: BaseAudioContext,
  assetName: string,
): Promise<Float64Array> {
  const parts = await Promise.all(
    DECODE_PARTS.map((part) => fetchDecodeFilterPart(ctx, `${assetName}_${part}`)),
  );
  const channels = concatChannels(parts);
  if (channels.length !== ACN_CHANNELS * 2) {
    throw new Error(
      `Decode filter set '${assetName}' has ${channels.length} channels, expected ${ACN_CHANNELS * 2}`,
    );
  }
  return flattenPairs(channels, ACN_CHANNELS);
}

/**
 * Load a crosstalk matrix as `[speaker][ear][tap]`.
 *
 * The WAV carries H_LL, H_LR, H_RL, H_RR in that order, which is already the
 * layout the core reads.
 */
export async function loadXtcTaps(
  ctx: BaseAudioContext,
  assetName: string,
): Promise<Float64Array> {
  const buffer = await fetchXtcFilterSet(ctx, assetName);
  if (buffer.numberOfChannels !== 4) {
    throw new Error(
      `Crosstalk filter set '${assetName}' has ${buffer.numberOfChannels} channels, expected 4`,
    );
  }
  const channels = Array.from({ length: 4 }, (_, i) => buffer.getChannelData(i));
  return flattenPairs(channels, 2);
}

/** Load a mono minimum-phase FIR (mastering or stem EQ, reference match). */
export async function loadFirTaps(url: string, ctx: BaseAudioContext): Promise<Float64Array> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Failed to fetch ${url}: ${response.status}`);
  const buffer = await ctx.decodeAudioData(await response.arrayBuffer());
  return Float64Array.from(buffer.getChannelData(0));
}
