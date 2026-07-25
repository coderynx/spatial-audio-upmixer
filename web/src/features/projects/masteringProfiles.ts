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

// --- Channel-bed router (ported from upmixer/separation/stem_router.py) --
//
// The preview used to binauralize each stem as a point object via a Web
// Audio HRTF PannerNode — that convolves every source with one generic,
// non-personalized HRIR with a diffuse-field high-frequency rolloff, so
// HRTF-panned sources read as duller than the dry final master (worse than
// a single EQ shelf could fix), and the API exposes no way to load a
// different HRTF. An earlier fix layered a fixed compensation EQ on the
// HRTF bus and hard-switched some sources to a dry stereo pan to dodge the
// comb filtering an undelayed dry copy causes against the panner's
// unqueryable ITD — both hacks are gone now.
//
// The preview now mirrors the backend exactly: stems are routed into the
// same 11-speaker channel bed `StemRouter.route` builds (this file's
// constants below), and *that* channel bed — not the individual stems — is
// what gets encoded to ambisonics and binauralized (see useStemPreview.ts).
// This is the "virtual loudspeaker" rendering model (each output channel is
// a fixed ambisonic point source), the same approach Apple's Spatial Audio
// renderer uses, and it lets a user mute a speaker as well as a stem.

/** Per-channel-group gains — upmixer/config.py `center_gain`/`surround_gain`/
 * `back_gain`/`height_gain`. FL/FR always 1.0 (no group). */
export const CENTER_GAIN = 0.85;
export const SURROUND_GAIN = 0.6;
export const BACK_GAIN = 0.55;
export const HEIGHT_GAIN = 0.55;

export function channelGroupGain(channel: string): number {
  if (channel === "C") return CENTER_GAIN;
  if (channel === "BL" || channel === "BR") return BACK_GAIN;
  if (channel === "SL" || channel === "SR") return SURROUND_GAIN;
  if (channel === "TFL" || channel === "TFR" || channel === "TBL" || channel === "TBR") return HEIGHT_GAIN;
  return 1.0;
}

/** upmixer/config.py `surround_bass_cutoff_hz` — highpass applied to a
 * stem's surround/back send before its Haas decorrelation delay. */
export const SURROUND_BASS_CUTOFF_HZ = 250.0;

/** upmixer/config.py height-send shaping (`_height_send` in
 * stem_router.py, same formula as `upmixer/utils.py` `elevation_eq`):
 * attenuate below `HEIGHT_LOW_ROLLOFF_HZ` to `HEIGHT_LOW_ROLLOFF_GAIN`,
 * then boost above `HEIGHT_CROSSOVER_HZ` by `HEIGHT_HIGH_SHELF_GAIN`. */
export const HEIGHT_LOW_ROLLOFF_HZ = 150.0;
export const HEIGHT_LOW_ROLLOFF_GAIN = 0.15;
export const HEIGHT_CROSSOVER_HZ = 3000.0;
export const HEIGHT_HIGH_SHELF_GAIN = 1.5;

/** upmixer/utils.py `diffuse_send` default wet blend. */
export const DIFFUSE_SEND_BLEND = 0.55;

/** stem_router.py `route()` — per-side Haas delays (ms) for surround/back
 * and height sends. Different per side so L/R don't comb-filter. */
export const SURROUND_HAAS_MS = { left: 31, right: 37 };
export const HEIGHT_HAAS_MS = { left: 23, right: 29 };

/** Web Audio version of `upmixer/utils.py` `diffuse_send`: blends a signal
 * with a delayed copy of itself for early-reflection decorrelation. */
export function buildDiffuseSend(
  ctx: AudioContext,
  input: AudioNode,
  delayMs: number,
  blend: number = DIFFUSE_SEND_BLEND,
): { output: AudioNode; nodes: AudioNode[] } {
  const delay = ctx.createDelay(1);
  delay.delayTime.value = delayMs / 1000;
  const dry = ctx.createGain();
  dry.gain.value = 1 - blend;
  const wet = ctx.createGain();
  wet.gain.value = blend;
  const output = ctx.createGain();
  input.connect(dry).connect(output);
  input.connect(delay).connect(wet).connect(output);
  return { output, nodes: [delay, dry, wet, output] };
}

/** Web Audio version of `stem_router.py` `_height_send` /
 * `upmixer/utils.py` `elevation_eq`: sub-bass rolloff (kept at
 * `HEIGHT_LOW_ROLLOFF_GAIN`, not fully removed) plus a top-end shelf boost
 * above the crossover. Implemented as the additive identity the Python
 * `sosfilt` version reduces to: `shaped = x - low·(1-g); out = shaped +
 * high(shaped)·(shelfGain-1)`. */
export function buildHeightSend(ctx: AudioContext, input: AudioNode): { output: AudioNode; nodes: AudioNode[] } {
  const lowpass = ctx.createBiquadFilter();
  lowpass.type = "lowpass";
  lowpass.frequency.value = HEIGHT_LOW_ROLLOFF_HZ;
  const lowComp = ctx.createGain();
  lowComp.gain.value = -(1 - HEIGHT_LOW_ROLLOFF_GAIN);
  const shaped = ctx.createGain();
  input.connect(shaped);
  input.connect(lowpass).connect(lowComp).connect(shaped);

  const highpass = ctx.createBiquadFilter();
  highpass.type = "highpass";
  highpass.frequency.value = HEIGHT_CROSSOVER_HZ;
  const highGain = ctx.createGain();
  highGain.gain.value = HEIGHT_HIGH_SHELF_GAIN - 1;
  const output = ctx.createGain();
  shaped.connect(output);
  shaped.connect(highpass).connect(highGain).connect(output);

  return { output, nodes: [lowpass, lowComp, shaped, highpass, highGain, output] };
}

/** Web Audio version of `stem_router.py`'s surround send: a highpass at
 * `SURROUND_BASS_CUTOFF_HZ` (keeps rhythmic low end out of the diffuse
 * surround/back layer) followed by the Haas diffuse send. */
export function buildSurroundSend(
  ctx: AudioContext,
  input: AudioNode,
  delayMs: number,
): { output: AudioNode; nodes: AudioNode[] } {
  const highpass = ctx.createBiquadFilter();
  highpass.type = "highpass";
  highpass.frequency.value = SURROUND_BASS_CUTOFF_HZ;
  input.connect(highpass);
  const diffuse = buildDiffuseSend(ctx, highpass, delayMs);
  return { output: diffuse.output, nodes: [highpass, ...diffuse.nodes] };
}

/** Approximates `stem_router.py`'s per-stem constant-power `route_scale`
 * (`sqrt(input_energy/routed_energy)`) from the route table alone, treating
 * every contributing send as comparable energy — good enough to keep a
 * widely-routed stem from reading louder than a narrowly-routed one, not an
 * exact energy match (the real value needs the decoded buffers' energy). */
export function estimateRouteScale(route: Record<string, number>): number {
  let sumSquares = 0;
  for (const [channel, weight] of Object.entries(route)) {
    if (channel === "LFE" || weight <= 0) continue;
    const scaled = weight * channelGroupGain(channel);
    sumSquares += scaled * scaled;
  }
  return sumSquares > 1e-10 ? 1 / Math.sqrt(sumSquares) : 1;
}
