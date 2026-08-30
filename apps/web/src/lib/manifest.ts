/** One dynamic-EQ bell, exactly as `mastering.dynamic_eq.bands[]` carries it. */
export type DynamicEqBand = {
  freq_hz: number;
  q: number;
  threshold_db: number;
  ratio: number;
  attack_ms: number;
  release_ms: number;
};

export type Manifest = {
  version: string;
  metadata: { name: string; author?: string; description?: string };
  engine: {
    mode: string;
    stems: string[];
    stem_silence_skip: boolean;
    stem_batch_size: number | null;
    stem_silence_threshold_db: number;
    stem_silence_min_duration_s: number;
    stem_silence_crossfade_ms: number;
    stem_silence_pad_ms: number;
    stem_bleed_reduction: boolean;
  };
  mixing: {
    channel_layout: string;
    stem_rebalance: Record<string, number>;
    stem_eq: Record<string, string>;
    stem_ambient_rear: Record<string, number>;
    stem_ambient_height: Record<string, number>;
    stem_ambient_height_crossover_hz: Record<string, number>;
    spatial_downmix_lock: boolean;
    stem_object_mode: Record<string, "linked-stereo" | "mono">;
    stem_routing: Record<string, Record<string, number>>;
    stem_placement: Record<string, { azimuth_deg: number; elevation_deg: number; width_deg: number; spread_deg: number }>;
    stem_enabled: Record<string, boolean>;
    stem_solo: string[];
    stem_source_anchor_strength: number;
  };
  mastering: {
    loudness: {
      normalize: boolean;
      target_preset: string | null;
      // Null defers to the named target, and with none named to the Atmos
      // Music pair — the same precedence `mastering/delivery.py` applies.
      target: number | null;
      max_tp: number | null;
    };
    highpass: { enabled: boolean; cutoff_hz: number };
    clip: { enabled: boolean; clip_db: number; knee: number };
    eq: { profile: string | null; strength: number };
    dynamic_eq: { profile: string | null; bands: DynamicEqBand[] };
    match_reference: {
      strength: number;
      spectrum: boolean;
      rms: boolean;
      max_db: number;
      // Curve-realization controls. Null means the served default (smoothing)
      // or no bound at all (the two masks) — never a number the web authors.
      smooth_octaves: number | null;
      low_hz: number | null;
      high_hz: number | null;
    };
    compressor: {
      profile: string | null;
      threshold_db: number | null;
      ratio: number | null;
      attack_ms: number | null;
      release_ms: number | null;
      knee_db: number | null;
      makeup_db: number | null;
      sidechain_hpf_hz: number | null;
    };
    bass: {
      profile: string | null;
      sub_gain_db: number | null;
      mid_gain_db: number | null;
      unify_hz: number | null;
      spread: string | null;
      punch: number | null;
      excite: boolean | null;
      lfe_mode: string | null;
      lfe_send: number | null;
      lfe_gain_db: number | null;
      decorrelate: number | null;
    };
  };
  routing: {
    center_gain: number;
    surround_gain: number;
    back_gain: number;
    height_gain: number;
    lfe_gain: number;
    lfe_cutoff: number;
    height_low_rolloff_gain: number;
    height_high_shelf_gain: number;
    height_directional_band_gain: number;
  };
  processing: {
    preview: boolean;
    preview_duration: number;
    preview_start: number | null;
    fft_size: number;
    normalize_output: boolean;
  };
  format: {
    type: string;
    codec: string;
    subtype: string;
    sample_rate: number;
    downmix?: { enabled: boolean; output?: string | null; surround_coeff: number };
    // Spatial Audio Engine binaural render — see docs/standards/
    // spatial_audio_engine.md. Only meaningful when type is "binaural";
    // a delivery format alongside "multichannel"/"adm-bwf", not a channel layout.
    binaural: { profile: string };
    // Spatial Audio Engine crosstalk-cancellation (transaural) render — see
    // docs/standards/transaural_speakers.md. Only meaningful when type is
    // "transaural"; a delivery format alongside "multichannel"/"adm-bwf"/"binaural".
    transaural: { profile: string };
  };
};

export const fallbackStems = [
  "Vocals",
  "Bass",
  "Drums",
  "Guitar",
  "Piano",
  "Other",
  "Kick",
  "Snare",
  "Toms",
  "Hi-Hat",
  "Ride",
  "Crash",
  "Crowd",
  "Lead Vocals",
  "Backing Vocals",
  "Vocals Reverb",
];

export const defaultManifest: Manifest = {
  version: "1.0.0",
  metadata: { name: "Spatial master", description: "Created in Upmixer" },
  engine: {
    mode: "stem",
    stems: ["Vocals", "Bass", "Drums", "Guitar", "Piano", "Other"],
    stem_silence_skip: true,
    stem_batch_size: null,
    stem_silence_threshold_db: -90,
    stem_silence_min_duration_s: 2,
    stem_silence_crossfade_ms: 10,
    stem_silence_pad_ms: 200,
    stem_bleed_reduction: false,
  },
  mixing: {
    channel_layout: "7.1.4",
    stem_rebalance: {},
    stem_eq: {},
    stem_ambient_rear: {},
    stem_ambient_height: {},
    stem_ambient_height_crossover_hz: {},
    spatial_downmix_lock: false,
    stem_object_mode: {},
    stem_placement: {},
    stem_routing: {},
    stem_enabled: {},
    stem_solo: [],
    stem_source_anchor_strength: 0.5,
  },
  mastering: {
    loudness: { normalize: true, target_preset: null, target: null, max_tp: null },
    highpass: { enabled: false, cutoff_hz: 20 },
    clip: { enabled: false, clip_db: 0.5, knee: 1 },
    eq: { profile: null, strength: 1 },
    dynamic_eq: { profile: null, bands: [] },
    match_reference: {
      strength: 0.7, spectrum: true, rms: true, max_db: 6,
      smooth_octaves: null, low_hz: null, high_hz: null,
    },
    compressor: {
      profile: "transparent",
      threshold_db: null,
      ratio: null,
      attack_ms: null,
      release_ms: null,
      knee_db: null,
      makeup_db: null,
      sidechain_hpf_hz: null,
    },
    bass: {
      profile: null,
      sub_gain_db: null,
      mid_gain_db: null,
      unify_hz: null,
      spread: null,
      punch: null,
      excite: null,
      lfe_mode: null,
      lfe_send: null,
      lfe_gain_db: null,
      decorrelate: null,
    },
  },
  routing: {
    center_gain: 0.85,
    surround_gain: 0.6,
    back_gain: 0.55,
    height_gain: 0.55,
    lfe_gain: 0.3162,
    lfe_cutoff: 120,
    height_low_rolloff_gain: 0.15,
    height_high_shelf_gain: 1.5,
    height_directional_band_gain: 1,
  },
  processing: {
    preview: false,
    preview_duration: 30,
    preview_start: null,
    fft_size: 4096,
    normalize_output: true,
  },
  format: {
    type: "multichannel", codec: "wav_pcm", subtype: "PCM_24", sample_rate: 48000,
    downmix: { enabled: false, output: null, surround_coeff: 0.7071 },
    binaural: { profile: "studio" },
    transaural: { profile: "stereo" },
  },
};

export function normalizeManifest(source: Record<string, unknown>): Manifest {
  const value = source as Partial<Manifest>;
  return {
    ...defaultManifest,
    ...value,
    metadata: { ...defaultManifest.metadata, ...value.metadata },
    engine: { ...defaultManifest.engine, ...value.engine },
    mixing: {
      ...defaultManifest.mixing,
      ...value.mixing,
      stem_rebalance: {
        ...defaultManifest.mixing.stem_rebalance,
        ...value.mixing?.stem_rebalance,
      },
      stem_eq: { ...defaultManifest.mixing.stem_eq, ...value.mixing?.stem_eq },
      stem_ambient_rear: {
        ...defaultManifest.mixing.stem_ambient_rear,
        ...value.mixing?.stem_ambient_rear,
      },
      stem_ambient_height: {
        ...defaultManifest.mixing.stem_ambient_height,
        ...value.mixing?.stem_ambient_height,
      },
      stem_ambient_height_crossover_hz: {
        ...defaultManifest.mixing.stem_ambient_height_crossover_hz,
        ...value.mixing?.stem_ambient_height_crossover_hz,
      },
      stem_routing: { ...defaultManifest.mixing.stem_routing, ...value.mixing?.stem_routing },
      stem_enabled: { ...defaultManifest.mixing.stem_enabled, ...value.mixing?.stem_enabled },
      stem_solo: Array.isArray(value.mixing?.stem_solo) ? value.mixing.stem_solo : [],
    },
    mastering: {
      ...defaultManifest.mastering,
      ...value.mastering,
      loudness: {
        ...defaultManifest.mastering.loudness,
        ...value.mastering?.loudness,
      },
      highpass: { ...defaultManifest.mastering.highpass, ...value.mastering?.highpass },
      clip: { ...defaultManifest.mastering.clip, ...value.mastering?.clip },
      eq: { ...defaultManifest.mastering.eq, ...value.mastering?.eq },
      dynamic_eq: {
        profile: value.mastering?.dynamic_eq?.profile ?? null,
        bands: Array.isArray(value.mastering?.dynamic_eq?.bands)
          ? value.mastering.dynamic_eq.bands
          : [],
      },
      match_reference: {
        ...defaultManifest.mastering.match_reference,
        ...value.mastering?.match_reference,
      },
      compressor: {
        ...defaultManifest.mastering.compressor,
        ...value.mastering?.compressor,
      },
      bass: { ...defaultManifest.mastering.bass, ...value.mastering?.bass },
    },
    routing: { ...defaultManifest.routing, ...value.routing },
    processing: { ...defaultManifest.processing, ...value.processing },
    format: {
      ...defaultManifest.format,
      ...value.format,
      // "wav" used to mean both "a multichannel bed" and "a WAV container";
      // those are now format.type and format.codec.
      type: value.format?.type === "wav" ? "multichannel" : value.format?.type ?? defaultManifest.format.type,
      downmix: {
        enabled: false,
        surround_coeff: 0.7071,
        ...defaultManifest.format.downmix,
        ...value.format?.downmix,
      },
      binaural: { ...defaultManifest.format.binaural, ...value.format?.binaural },
      transaural: { ...defaultManifest.format.transaural, ...value.format?.transaural },
    },
  };
}

export const defaultProjectManifest: Manifest = {
  ...defaultManifest,
  mixing: {
    ...defaultManifest.mixing,
    stem_source_anchor_strength: 0,
  },
  routing: { ...defaultManifest.routing },
};
