// Ported constants from the backend mastering chain (upmixer/mastering/*.py) and
// stem router (upmixer/separation/stem_router.py) so the preview's Web Audio graph
// can approximate the same tone/dynamics/loudness shaping as the delivered mix.
// Keep these numbers in sync with the Python source — they are not derived at runtime.

export type EqProfileName =
  | "spatial-transparent"
  | "spatial-air"
  | "spatial-warm"
  | "spatial-present"
  | "atmos-streaming";

// (frequency Hz, gain dB) breakpoints — upmixer/mastering/eq.py EQ_PROFILES.
export const EQ_PROFILES: Record<EqProfileName, [number, number][]> = {
  "spatial-transparent": [
    [20, 0.0], [20000, 0.0],
  ],
  "spatial-air": [
    [20, 0.0], [1000, 0.0], [5000, 0.5], [10000, 1.5], [15000, 2.5], [20000, 2.5],
  ],
  "spatial-warm": [
    [20, 0.0], [100, 1.0], [300, 1.5], [1000, 0.5],
    [3000, -0.5], [8000, 0.0], [20000, 0.0],
  ],
  "spatial-present": [
    [20, 0.0], [500, 0.0], [2000, 1.0], [4000, 2.0],
    [6000, 1.5], [10000, 1.0], [20000, 1.5],
  ],
  "atmos-streaming": [
    [20, 0.0], [60, 1.0], [100, 0.8], [500, 0.0],
    [2000, 0.5], [5000, 1.0], [12000, 1.5], [18000, 2.0], [20000, 2.0],
  ],
};

export type CompProfileName = "transparent" | "glue" | "warm";

export type CompProfile = {
  threshold_db: number;
  ratio: number;
  attack_ms: number;
  release_ms: number;
  knee_db: number;
  makeup_db: number;
};

// upmixer/mastering/compressor.py COMP_PROFILES.
export const COMP_PROFILES: Record<CompProfileName, CompProfile> = {
  transparent: { threshold_db: -22.0, ratio: 1.5, attack_ms: 30.0, release_ms: 300.0, knee_db: 9.0, makeup_db: 0.0 },
  glue: { threshold_db: -18.0, ratio: 2.0, attack_ms: 20.0, release_ms: 200.0, knee_db: 6.0, makeup_db: 0.0 },
  warm: { threshold_db: -15.0, ratio: 2.0, attack_ms: 40.0, release_ms: 400.0, knee_db: 12.0, makeup_db: 0.0 },
};

export type BassProfileName = "boost" | "cut" | "mono" | "enhance";

export type BassProfile = {
  sub_gain_db: number;
  mid_gain_db: number;
  mono_cutoff_hz: number | null;
  excite: boolean;
  lfe_gain_db: number;
};

// upmixer/mastering/bass.py BASS_PROFILES.
export const BASS_PROFILES: Record<BassProfileName, BassProfile> = {
  boost: { sub_gain_db: 2.0, mid_gain_db: 1.0, mono_cutoff_hz: null, excite: false, lfe_gain_db: 1.5 },
  cut: { sub_gain_db: -2.5, mid_gain_db: -1.5, mono_cutoff_hz: null, excite: false, lfe_gain_db: -1.0 },
  mono: { sub_gain_db: 0.0, mid_gain_db: 0.0, mono_cutoff_hz: 100.0, excite: false, lfe_gain_db: 0.0 },
  enhance: { sub_gain_db: 1.5, mid_gain_db: 0.5, mono_cutoff_hz: 80.0, excite: true, lfe_gain_db: 1.0 },
};

export const SUB_CUTOFF_HZ = 80.0;
export const MID_CUTOFF_HZ = 200.0;
export const EXCITE_BLEND = 0.15;
export const EXCITE_DRIVE = 3.0;

// upmixer/config.py peak_limit_threshold — not manifest-editable, fixed default.
export const SOFT_LIMIT_THRESHOLD = 0.95;

// upmixer/config.py lfe_gain (-10 dB) and stem_router LFE lowpass.
export const LFE_GAIN = 0.31622776601683794;
export const LFE_LOWPASS_HZ = 120;

// upmixer/config.py loudness_max_gain_db.
export const LOUDNESS_MAX_GAIN_DB = 30.0;

/** WaveShaper curve for the backend's tanh soft-limit: identity below
 * `threshold`, tanh saturation above it. Mirrors upmixer/utils.py soft_limit. */
export function buildSoftLimitCurve(threshold: number = SOFT_LIMIT_THRESHOLD, samples = 4096): Float32Array {
  const curve = new Float32Array(samples);
  const margin = 1.0 - threshold;
  for (let i = 0; i < samples; i++) {
    const x = (i / (samples - 1)) * 2 - 1;
    const ax = Math.abs(x);
    curve[i] = ax <= threshold
      ? x
      : Math.sign(x) * (threshold + margin * Math.tanh((ax - threshold) / margin));
  }
  return curve;
}

/** WaveShaper curve for the bass exciter: tanh(x * drive). Mirrors the
 * harmonic-exciter stage in upmixer/mastering/bass.py. */
export function buildExciteCurve(drive: number = EXCITE_DRIVE, samples = 4096): Float32Array {
  const curve = new Float32Array(samples);
  for (let i = 0; i < samples; i++) {
    const x = (i / (samples - 1)) * 2 - 1;
    curve[i] = Math.tanh(x * drive);
  }
  return curve;
}

/** Build a chain of peaking/high-shelf BiquadFilterNodes approximating the
 * backend's minimum-phase FIR spectral shaper for a given breakpoint curve.
 * Non-zero breakpoints become peaking filters; the final breakpoint (the
 * top of the audible band) becomes a high-shelf, since these curves are
 * used for air/presence tilts. Zero-gain points and the profile's baseline
 * anchor are skipped. */
export function buildEqFilters(
  ctx: AudioContext | { createBiquadFilter(): BiquadFilterNode },
  breakpoints: [number, number][],
  strength: number,
): BiquadFilterNode[] {
  const nodes: BiquadFilterNode[] = [];
  breakpoints.forEach(([freq, gainDb], index) => {
    if (gainDb === 0) return;
    const filter = ctx.createBiquadFilter();
    const isLast = index === breakpoints.length - 1;
    filter.type = isLast ? "highshelf" : "peaking";
    filter.frequency.value = freq;
    filter.gain.value = gainDb * strength;
    if (!isLast && "Q" in filter) filter.Q.value = 1;
    nodes.push(filter);
  });
  return nodes;
}

/** Connect `start -> nodes[0] -> nodes[1] -> ... -> nodes[n-1]` in series and
 * return the last node in the chain (or `start` when `nodes` is empty). */
export function connectSeries(start: AudioNode, nodes: AudioNode[]): AudioNode {
  let previous = start;
  for (const node of nodes) {
    previous.connect(node);
    previous = node;
  }
  return previous;
}

// --- HRTF clarity restoration -----------------------------------------
//
// Chrome/Firefox's built-in HRTF PannerNode convolves every source with one
// generic, non-personalized IRCAM Listen HRIR set. That set has a
// diffuse-field high-frequency rolloff, so HRTF-panned sources read as
// duller than the dry final master — worse than a single EQ shelf can fix.
// Real binaural renderers counter the tonal part with a fixed diffuse-field
// compensation curve on the binaural bus (`HRTF_COMPENSATION_BANDS` below).
//
// An earlier version of this fix also blended in some dry (non-HRTF) signal
// for front/ear-level sources, summed in parallel with their HRTF copy. That
// reintroduced comb filtering: the HRTF panner always adds *some* delay
// (an interaural time delay that varies with position and is not exposed by
// the API), so summing an undelayed dry copy against the delayed HRTF copy
// of the *same* source comb-filters it. There's no reliable way to
// delay-compensate the dry path since the exact ITD isn't queryable, so the
// two paths must never be active at once for the same source — hence a hard
// per-source choice, not a blend, in `isDryRouted` below.
//
// These constants/functions are preview-only; they have no backend/manifest
// meaning.

/** Elevation (Web Audio `y`) at which a source is considered "full height"
 * for the purposes of the front/ear-level test below — matches the
 * height-layer `y` coordinate used for TFL/TFR/TBL/TBR in
 * `speakerCoordinates`. */
export const DRY_ROUTING_HEIGHT_NORM = 0.6;

/** Minimum forward*levelness product (both in [0, 1]) for a source to route
 * fully dry instead of through HRTF. Tuned so dead-front/near-front,
 * ear-level content (FL/FR/C) qualifies, while anything with meaningful
 * side, rear, or height component does not. */
export const DRY_ROUTING_THRESHOLD = 0.5;

/** Whether a source at this normalized Web Audio position should bypass the
 * HRTF panner entirely and play through the plain stereo dry path instead.
 * A hard, non-blended choice — see the comment above for why a parallel
 * dry/HRTF blend is unsafe. Front, ear-level positions (weak HRTF cues,
 * worst HRTF coloration) route dry; anything with real side/rear/height
 * content stays on HRTF so it still localizes. */
export function isDryRouted(position: { x: number; y: number; z: number }): boolean {
  const forward = Math.min(Math.max(-position.z, 0), 1);
  const levelness = Math.min(Math.max(1 - Math.abs(position.y) / DRY_ROUTING_HEIGHT_NORM, 0), 1);
  return forward * levelness >= DRY_ROUTING_THRESHOLD;
}

type HrtfCompensationBand = { type: BiquadFilterType; frequency: number; gain: number; q?: number };

/** Fixed diffuse-field compensation cascade for the HRTF sub-bus only
 * (applied before the dry bus joins it, so the dry path stays flat and
 * can't be over-brightened). Approximates the inverse of the generic HRIR
 * set's coloration: a modest dip to tame the boxy lower-mid buildup HRTF
 * convolution adds, a presence bump where the diffuse-field average dips
 * most, and an air shelf to restore the top-end the single-shelf fix
 * couldn't reach. */
export const HRTF_COMPENSATION_BANDS: HrtfCompensationBand[] = [
  { type: "peaking", frequency: 250, gain: -1.5, q: 1 },
  { type: "peaking", frequency: 3500, gain: 3, q: 1 },
  { type: "highshelf", frequency: 9000, gain: 5 },
];

export function buildHrtfCompensation(
  ctx: AudioContext | { createBiquadFilter(): BiquadFilterNode },
): BiquadFilterNode[] {
  return HRTF_COMPENSATION_BANDS.map(({ type, frequency, gain, q }) => {
    const filter = ctx.createBiquadFilter();
    filter.type = type;
    filter.frequency.value = frequency;
    filter.gain.value = gain;
    if (q != null && "Q" in filter) filter.Q.value = q;
    return filter;
  });
}
