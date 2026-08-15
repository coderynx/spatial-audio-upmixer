/** Delivery codecs and the limits that gate them, mirroring core's
 * `upmixer/codecs.py`. The server is authoritative and ships the same table
 * as `GET /api/v1/configuration`'s `choices.output_codecs`; these are the
 * fallbacks and the shared "can this combination be delivered" rules. */

import { isStereoLayout } from "@/lib/layouts";

export type CodecChoice = {
  name: string;
  label: string;
  extension: string;
  subtypes: string[];
  max_channels: number | null;
  sample_rates: number[] | null;
};

export const DEFAULT_CODEC = "wav_pcm";

export const OUTPUT_CODECS: CodecChoice[] = [
  {
    name: "wav_pcm",
    label: "WAV (PCM)",
    extension: ".wav",
    subtypes: ["PCM_16", "PCM_24", "PCM_32", "FLOAT"],
    max_channels: null,
    sample_rates: null,
  },
  {
    name: "flac",
    label: "FLAC",
    extension: ".flac",
    subtypes: ["PCM_16", "PCM_24"],
    max_channels: 8,
    sample_rates: null,
  },
  {
    name: "ogg_vorbis",
    label: "OGG (Vorbis)",
    extension: ".ogg",
    subtypes: ["VORBIS"],
    max_channels: null,
    sample_rates: null,
  },
  {
    name: "ogg_opus",
    label: "OGG (Opus)",
    extension: ".opus",
    subtypes: ["OPUS"],
    max_channels: null,
    sample_rates: [8000, 12000, 16000, 24000, 48000],
  },
];

const LAYOUT_CHANNELS: Record<string, number> = {
  stereo: 2,
  "5.1": 6,
  "7.1": 8,
  "5.1.2": 8,
  "5.1.4": 10,
  "7.1.2": 10,
  "7.1.4": 12,
};

/** Channels the delivered file actually carries: binaural and transaural
 * collapse their bed to a stereo pair, so a 7.1.4 bed delivers 2. */
export function deliveredChannels(layout: string, type: string): number {
  if (type === "binaural" || type === "transaural") return 2;
  return LAYOUT_CHANNELS[layout] ?? (isStereoLayout(layout) ? 2 : 0);
}

/** Why this codec cannot carry this delivery, or undefined when it can. */
export function codecUnavailableReason(
  codec: CodecChoice,
  layout: string,
  type: string,
  sampleRate: number,
): string | undefined {
  if (type === "adm-bwf" && codec.name !== DEFAULT_CODEC) return "WAV only";
  const channels = deliveredChannels(layout, type);
  if (codec.max_channels !== null && channels > codec.max_channels) {
    return `Max ${codec.max_channels} ch`;
  }
  if (codec.sample_rates !== null && !codec.sample_rates.includes(sampleRate)) {
    return `${codec.sample_rates.map((rate) => rate / 1000).join(" / ")} kHz only`;
  }
  return undefined;
}

/** Retarget a codec the delivery cannot carry, so changing layout, format or
 * sample rate never leaves `format.codec` on a value the server would reject.
 * Mirrors `delivery_codec_for_layout` in `apps/api`. */
export function resolveCodec(
  codecs: CodecChoice[],
  codec: string,
  layout: string,
  type: string,
  sampleRate: number,
): string {
  const match = codecs.find((entry) => entry.name === codec);
  if (!match) return DEFAULT_CODEC;
  return codecUnavailableReason(match, layout, type, sampleRate) ? DEFAULT_CODEC : codec;
}

/** Bit depths a codec offers, or an empty list when it carries no bit depth
 * (lossy containers) and the field should be disabled. */
export function subtypesFor(codecs: CodecChoice[], codec: string): string[] {
  const match = codecs.find((entry) => entry.name === codec);
  if (!match || match.subtypes.length <= 1) return [];
  return match.subtypes;
}
