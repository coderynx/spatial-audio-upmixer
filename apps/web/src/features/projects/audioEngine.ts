// The DAW audio layer for the project preview.
//
// All DSP now lives in the shared Rust core (packages/dsp), running as
// WebAssembly inside `dsp.worklet.js` — the preview and the export execute
// the same code rather than two implementations kept in step. This file owns
// the AudioContext, transport, and the monitor path around that node, and
// translates the project's mix into the core's parameter block.
//
// Framework-free by design, so it stays testable headless and
// `useStemPreview.ts` can remain a thin React binding: sync the latest
// props/state onto the public fields each render, then call the matching
// method from the appropriate effect.

import type { ProjectStem, StemScene } from "@/api";
import { speakerCoordinates } from "@/lib/spatial";
import { routingFromAzimuthElevation } from "@/lib/spatial";
import { applyTruePeakCeiling, loudnessGainFor } from "./audioAnalysis";
import type {
  BassProfileName,
  CompProfileName,
  EngineConstants,
  EqProfileName,
  SpatialProfile,
  StemEqProfileName,
  TransauralProfile,
} from "./masteringProfiles";
import { estimateRouteScale } from "./masteringProfiles";
import type { MasterPreview } from "./masterPreview";
import { loadBuffer } from "./audioLoaders";
import { DspEngineClient } from "./wasmEngine/engineClient";
import { buildEngineParams, type StemMix } from "./wasmEngine/engineParams";
import { loadDecodeTaps, loadFirTaps, loadXtcTaps } from "./wasmEngine/filterAssets";

/**
 * Stems decode concurrently but must reach the engine in `this.stems` order
 * (its stem index is push order — see `push_stem` in stream/engine.rs), so a
 * stem that finishes decoding out of turn has to be held in memory until its
 * turn comes. Bounding the batch caps that retained set: a 5-minute 48 kHz
 * stereo stem is ~115 MB decoded, so unbounded parallelism risks holding
 * every stem of a long project at once.
 */
const STEM_DECODE_CONCURRENCY = 3;

export { applyTruePeakCeiling } from "./audioAnalysis";

export type EngineRef<T> = { current: T };
function engineRef<T>(value: T): EngineRef<T> {
  return { current: value };
}

export type OutputMode = "binaural" | "transaural" | "stereo" | "native";

export const POSITIONAL_CHANNELS = Object.keys(speakerCoordinates);

export type MeterLevel = { rms: number; peak: number; clipped: boolean };

const SILENT_METER_LEVEL: MeterLevel = { rms: 0, peak: 0, clipped: false };

// A sample clearing unity by a hairline still counts as clipped.
const CLIP_TOLERANCE = 1.0;

export type MixPreview = {
  stem_routing?: Record<string, Record<string, number>>;
  stem_rebalance?: Record<string, number>;
  stem_eq?: Record<string, string>;
  stem_enabled?: Record<string, boolean>;
  stem_solo?: string[];
  stem_source_anchor_strength?: number;
};

export type EngineCallbacks = {
  onReady(ready: boolean): void;
  onLoadProgress(progress: number): void;
  onError(message: string | null): void;
  onPlaying(playing: boolean): void;
  onCurrentTime(time: number): void;
  onDuration(duration: number): void;
  onMeasuring(measuring: boolean): void;
  /** Fraction of the current measurement stage measured, for a progress bar. */
  onMeasureProgress(progress: number): void;
  onMaxChannels(maxChannels: number): void;
  onVolume(volume: number): void;
  onMuted(muted: boolean): void;
  onLoop(loop: boolean): void;
};

/** The core's FIR assets are designed at 48 kHz; run the graph there too. */
const CONTEXT_SAMPLE_RATE = 48000;

function level(rms: number, peak: number): MeterLevel {
  return { rms, peak, clipped: peak > CLIP_TOLERANCE };
}

/** Appends the live `strength`/`max_db` knobs to a reference-match `fir_url`
 * base, so the FIR endpoint designs the filter for exactly this config. */
export function withReferenceMatchParams(firUrl: string, strength: number, maxDb: number): string {
  const separator = firUrl.includes("?") ? "&" : "?";
  return `${firUrl}${separator}strength=${strength}&max_db=${maxDb}`;
}

export class PreviewAudioEngine {
  readonly supported = Boolean(
    window.AudioContext ||
      (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext,
  );

  // ---- Inputs, synced from the hook every render ----
  stems: ProjectStem[] = [];
  scene: { stems?: StemScene } = {};
  mix?: MixPreview;
  sourcePreviewUrl: string | null = null;
  mastering?: MasterPreview;
  layoutChannels: string[] = POSITIONAL_CHANNELS;
  outputMode: OutputMode = "binaural";
  spatialProfile: SpatialProfile = "studio";
  transauralProfile: TransauralProfile = "stereo";
  constants!: EngineConstants;
  positionalChannels: string[] = [];
  speakerEnabled: Record<string, boolean> = {};

  // ---- State the engine owns, mirrored out via callbacks ----
  volume = 1;
  muted = false;
  loop = false;

  private context: AudioContext | null = null;
  private client: DspEngineClient | null = null;
  /** MONITOR domain: volume and mute, strictly after the rendered program. */
  private monitorGain: GainNode | null = null;
  private playing = false;
  private duration = 0;
  private scrubbing = false;
  private loadToken = 0;

  private decodeTaps: Float64Array | null = null;
  private xtcTaps: Float64Array | null = null;
  private loadedDecodeProfile: SpatialProfile | null = null;
  private loadedXtcProfile: TransauralProfile | null = null;
  private stemEqTaps: Map<string, Float64Array> = new Map();
  private masterEqAsset: string | null = null;
  private masterEqTaps: Float64Array | null = null;
  private referenceFirUrl: string | null = null;
  private referenceTaps: Float64Array | null = null;
  private measuredLkfs = -70;
  private measuredTpDbtp = -70;
  private measuredForMode: string | null = null;
  /** Mode key the in-flight exact measurement's refinement belongs to. */
  private exactMeasureKey: string | null = null;
  /** While set, render uncorrected so the measurement sees the raw program. */
  private measuringRaw = false;
  private measureToken = 0;
  private resumeOnGesture: (() => void) | null = null;
  private stemOrder: string[] = [];
  /** Parallel to `stemOrder` — how many bars each stem's meter shows. */
  private stemChannelCounts: number[] = [];

  readonly stemSpectrum: EngineRef<Map<string, { level: number; centroid: number }>> = engineRef(
    new Map(),
  );
  readonly channelLevels: EngineRef<Map<string, MeterLevel>> = engineRef(new Map());
  readonly stemLevels: EngineRef<Map<string, MeterLevel[]>> = engineRef(new Map());
  readonly headphoneLevels: EngineRef<{ left: MeterLevel; right: MeterLevel }> = engineRef({
    left: SILENT_METER_LEVEL,
    right: SILENT_METER_LEVEL,
  });
  readonly currentTimeRef: EngineRef<number> = engineRef(0);

  constructor(private readonly callbacks: EngineCallbacks) {}

  // ---- Transport ----

  setVolume(volume: number) {
    this.volume = volume;
    this.callbacks.onVolume(volume);
    this.applyMonitorGain();
  }

  toggleMute() {
    this.muted = !this.muted;
    this.callbacks.onMuted(this.muted);
    this.applyMonitorGain();
  }

  toggleLoop() {
    this.loop = !this.loop;
    this.callbacks.onLoop(this.loop);
    this.client?.setTransport({ loop: this.loop });
  }

  private applyMonitorGain() {
    if (!this.monitorGain || !this.context) return;
    const target = this.muted ? 0 : this.volume;
    this.monitorGain.gain.setTargetAtTime(target, this.context.currentTime, 0.008);
  }

  async playPause(currentTime: number) {
    if (this.playing) {
      this.pause();
      return;
    }
    await this.playFrom(currentTime);
  }

  async playFrom(time: number): Promise<boolean> {
    if (!this.client || !this.context) return false;
    // Loudness calibration is mandatory: a mode/profile combination that
    // hasn't been measured yet has no valid `outputGain`, so refuse to start
    // rather than play at whatever gain happened to be left over from a
    // previous mode. `measureIfNeeded()` always re-fires on the state changes
    // that invalidate this (see its callers), so the gate lifts on its own.
    if (this.measuredForMode !== this.measureKey()) return false;
    if (this.context.state === "suspended") await this.context.resume();
    if (time !== this.currentTimeRef.current) this.moveTo(time);
    this.playing = true;
    this.client.setTransport({ playing: true, loop: this.loop });
    this.callbacks.onPlaying(true);
    return true;
  }

  pause() {
    this.playing = false;
    this.client?.setTransport({ playing: false });
    this.callbacks.onPlaying(false);
    this.silenceLevels();
  }

  /** The worklet stops posting `frame` messages once paused, so the meter
   * refs would otherwise freeze at their last value. Zero each in place —
   * keeping every array's shape — so the existing decay ballistics ease
   * every bar down instead of snapping it away. */
  private silenceLevels() {
    for (const [key, levels] of this.stemLevels.current) {
      this.stemLevels.current.set(key, levels.map(() => SILENT_METER_LEVEL));
    }
    for (const key of this.channelLevels.current.keys()) {
      this.channelLevels.current.set(key, SILENT_METER_LEVEL);
    }
    this.headphoneLevels.current = { left: SILENT_METER_LEVEL, right: SILENT_METER_LEVEL };
    for (const [key, spectrum] of this.stemSpectrum.current) {
      this.stemSpectrum.current.set(key, { ...spectrum, level: 0 });
    }
  }

  stop() {
    this.pause();
    this.moveTo(0);
  }

  moveTo(time: number): number {
    const clamped = Math.max(0, Math.min(time, this.duration));
    this.currentTimeRef.current = clamped;
    this.callbacks.onCurrentTime(clamped);
    this.client?.seek(clamped * CONTEXT_SAMPLE_RATE);
    return clamped;
  }

  async seek(time: number) {
    this.moveTo(time);
  }

  beginScrub() {
    this.scrubbing = true;
  }

  scrubTo(time: number) {
    const clamped = Math.max(0, Math.min(time, this.duration));
    this.currentTimeRef.current = clamped;
    this.callbacks.onCurrentTime(clamped);
  }

  async commitScrub(time: number) {
    this.scrubbing = false;
    this.moveTo(time);
  }

  async setOutputSink(deviceId: string) {
    const context = this.context as (AudioContext & { setSinkId?: (id: string) => Promise<void> }) | null;
    if (!context?.setSinkId) return;
    try {
      await context.setSinkId(deviceId);
    } catch {
      // The browser refused the device; playback continues on the default.
    }
  }

  // ---- Parameter application ----
  //
  // Every "the mix changed" path funnels into `apply()`: the core takes a
  // whole parameter block and keeps its stems and playhead, so there is no
  // per-control rewiring left to do.

  buildMasteringTopology() {
    void Promise.all([this.loadMasteringFirs(), this.loadReferenceMatchFir()]).then(() => this.apply());
  }

  buildStemEqChains() {
    void this.loadStemEqFirs().then(() => this.apply());
  }

  applySpeakerMute() {
    this.apply();
  }

  applyOutputMode(mode: OutputMode) {
    this.outputMode = mode;
    this.apply();
    void this.measureIfNeeded();
  }

  retuneVoicing(profile: SpatialProfile) {
    this.spatialProfile = profile;
    this.apply();
    void this.measureIfNeeded();
  }

  retuneCrosstalkVoicing(profile: TransauralProfile) {
    this.transauralProfile = profile;
    this.apply();
    void this.measureIfNeeded();
  }

  async loadDecodeFilterSet(profile: SpatialProfile): Promise<boolean> {
    if (!this.context || !this.constants) return false;
    if (this.loadedDecodeProfile === profile && this.decodeTaps) return true;
    try {
      this.decodeTaps = await loadDecodeTaps(this.context, this.constants.decodeFilterSet[profile]);
      this.loadedDecodeProfile = profile;
      // A slice, not the cached field itself: `setDecodeTaps` transfers its
      // argument's buffer, which would detach `this.decodeTaps` and break
      // the cache-hit path above the next time this profile is requested.
      this.client?.setDecodeTaps(this.decodeTaps.slice());
      return true;
    } catch {
      return false;
    }
  }

  async loadXtcFilterSet(profile: TransauralProfile): Promise<boolean> {
    if (!this.context || !this.constants) return false;
    if (this.loadedXtcProfile === profile && this.xtcTaps) return true;
    try {
      this.xtcTaps = await loadXtcTaps(this.context, this.constants.xtcFilterSet[profile]);
      this.loadedXtcProfile = profile;
      this.client?.setXtcTaps(this.xtcTaps.slice());
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Cached by asset name, like `loadStemEqFirs`'s per-stem cache: mastering
   * changes unrelated to the EQ profile (a compressor threshold, a loudness
   * target) still bump `masteringKey` and re-run this, so without a cache
   * every one of them would re-fetch and re-decode the same WAV.
   */
  private async loadMasteringFirs() {
    if (!this.context || !this.constants) return;
    const profile = this.mastering?.eq?.profile as EqProfileName | null | undefined;
    const asset = profile ? this.constants.eqFirAssets[profile] : null;
    if (asset === this.masterEqAsset && (asset === null || this.masterEqTaps)) return;
    this.masterEqAsset = asset ?? null;
    this.masterEqTaps = asset
      ? await loadFirTaps(`/eq_fir/${asset}.wav`, this.context).catch(() => null)
      : null;
  }

  /**
   * The server serves one correction curve per project as a base `fir_url`
   * and designs the actual filter on demand from the live `strength`/
   * `max_db` query params (see `MasterPreview.match_reference`'s doc
   * comment) — so unlike `loadMasteringFirs`'s fixed asset names, the URL
   * itself changes with the sliders and is the cache key.
   */
  private async loadReferenceMatchFir() {
    if (!this.context) return;
    const refCfg = this.mastering?.match_reference;
    const strength = refCfg?.strength ?? 1;
    const maxDb = refCfg?.max_db ?? 6;
    const active = Boolean(refCfg?.spectrum && refCfg.fir_url && strength > 0);
    const url = active ? withReferenceMatchParams(refCfg!.fir_url as string, strength, maxDb) : null;
    if (url === this.referenceFirUrl && (url === null || this.referenceTaps)) return;
    this.referenceFirUrl = url;
    this.referenceTaps = url ? await loadFirTaps(url, this.context).catch(() => null) : null;
  }

  private async loadStemEqFirs() {
    if (!this.context || !this.constants) return;
    const wanted = this.mix?.stem_eq ?? {};
    const entries = await Promise.all(
      Object.entries(wanted).map(async ([stemKey, profile]) => {
        const asset = this.constants.stemEqFirAssets[profile as StemEqProfileName];
        if (!asset) return null;
        const taps =
          this.stemEqTaps.get(stemKey) ??
          (await loadFirTaps(`/eq_fir/${asset}.wav`, this.context!).catch(() => null));
        return taps ? ([stemKey, taps] as const) : null;
      }),
    );
    this.stemEqTaps = new Map(entries.filter((entry): entry is readonly [string, Float64Array] => entry !== null));
  }

  /**
   * Stems the engine actually loads. `resolveStems`, `stemOrder` and
   * `loadStems` must all iterate this same filtered list — the engine's stem
   * index is push order, so a stem dropped by one of them but not the others
   * shifts every later index and desyncs routing, rebalance and meters.
   */
  private previewableStems(): ProjectStem[] {
    return this.stems.filter((stem) => stem.preview_url || stem.audio_url);
  }

  /** Resolve the project's mix into the core's per-stem parameters. */
  private resolveStems(): StemMix[] {
    const anchor = this.mix?.stem_source_anchor_strength || 0;
    return this.previewableStems().map((stem) => {
      const base = stem.stem_key.split("@", 1)[0];
      const scene = this.scene.stems?.[stem.stem_key] || this.scene.stems?.[base] || {};
      let routing = this.mix?.stem_routing?.[stem.stem_key] || this.mix?.stem_routing?.[base];
      // No resolved routing yet (a freshly dropped stem, say) — fall back to
      // the same nearest-3-speakers weighting `routing_for_scene` uses.
      if (!routing || Object.keys(routing).length === 0) {
        routing =
          scene.azimuth_deg != null || scene.elevation_deg != null
            ? routingFromAzimuthElevation(scene.azimuth_deg || 0, scene.elevation_deg || 0)
            : {};
      }

      let total = 0;
      let frontWeight = 0;
      for (const [channel, weight] of Object.entries(routing)) {
        if (weight <= 0) continue;
        total += weight;
        if (channel === "FL" || channel === "FR") frontWeight += weight;
      }
      // Only the FL/FR portion crossfades toward the dry source, matching
      // source_anchor.py's front-zone-only blend.
      const frontFraction = total > 0 ? frontWeight / total : 0;

      const soloed = this.mix?.stem_solo?.length
        ? this.mix.stem_solo.includes(stem.stem_key) || this.mix.stem_solo.includes(base)
        : true;
      const enabled =
        soloed && this.mix?.stem_enabled?.[base] !== false && scene.enabled !== false;

      const anchorDb = 20 * Math.log10(Math.max(1 - anchor * frontFraction, 1e-6));
      return {
        id: stem.id,
        routing,
        rebalanceDb: (this.mix?.stem_rebalance?.[base] || 0) + anchorDb,
        enabled,
        eqFir: this.stemEqTaps.get(stem.stem_key),
        routeScale: estimateRouteScale(routing, this.constants.channelGains),
      };
    });
  }

  apply() {
    if (!this.client || !this.constants) return;
    this.client.updateParams(this.buildParams());
  }

  private buildParams() {
    const bass = (this.mastering?.bass?.profile ?? null) as BassProfileName | null;
    const target = this.mastering?.loudness?.target ?? -18;
    const normalize = this.mastering?.loudness?.normalize ?? true;
    // Unlike the offline export chain, this engine has no first-stage
    // normalize on the discrete bed before the spatial collapse — one gain
    // stage does the whole job for every mode, so every mode needs the same
    // full budget. A collapse-only cap here (correct for the *second* of the
    // offline chain's two stages) would leave binaural/transaural under-
    // corrected relative to native whenever the raw mix sits far from target.
    const loudnessGain = normalize
      ? loudnessGainFor(this.measuredLkfs, target, this.constants.loudnessMaxGainDb)
      : 1;
    const outputGain =
      this.measuringRaw || !normalize
        ? 1
        : applyTruePeakCeiling(
            this.measuredTpDbtp,
            loudnessGain,
            this.mastering?.loudness?.max_tp ?? -1,
          );

    const previewable = this.previewableStems();
    this.stemOrder = previewable.map((stem) => stem.stem_key.split("@", 1)[0]);
    this.stemChannelCounts = previewable.map((stem) => stem.channels);
    return buildEngineParams({
      constants: this.constants,
      layoutChannels: this.layoutChannels,
      speakerEnabled: this.speakerEnabled,
      stems: this.resolveStems(),
      master: {
        compProfile: (this.mastering?.compressor?.profile ?? null) as CompProfileName | null,
        bassProfile: bass,
        eqFir: this.masterEqTaps ?? undefined,
        eqStrength: this.mastering?.eq?.strength ?? 1,
        referenceFir: this.referenceTaps ?? undefined,
        referenceGain: this.mastering?.match_reference?.rms
          ? 10 ** ((this.mastering.match_reference.rms_gain_db ?? 0) / 20)
          : 1,
        outputGain,
        limiterCeilingDbtp: this.mastering?.loudness?.max_tp ?? -1,
      },
      outputMode: this.outputMode,
      spatialProfile: this.spatialProfile,
      transauralProfile: this.transauralProfile,
    });
  }

  /**
   * Measure the programme once per output mode/profile combination, so a
   * mode switch re-measures rather than reusing a stale correction.
   *
   * The pass walks the whole programme in slices taken from the render
   * callback, so this resolves minutes later on a long track. Playback is
   * mandatory-calibrated (see `playFrom`): a mode/profile switch that
   * invalidates the current measurement pauses transport here rather than
   * leaving it running uncorrected until the user notices.
   */
  private measureKey(): string {
    return `${this.outputMode}:${this.spatialProfile}:${this.transauralProfile}`;
  }

  private async measureIfNeeded() {
    if (!this.client) return;
    const key = this.measureKey();
    if (this.measuredForMode === key) return;

    if (this.playing) this.pause();
    const token = ++this.measureToken;
    this.exactMeasureKey = key;
    this.callbacks.onMeasuring(true);
    this.callbacks.onMeasureProgress(0);
    this.measuringRaw = true;
    this.apply();
    const result = await this.client.measure([1, 1]);
    // A mode switch mid-measurement supersedes this pass; the newer one owns
    // the measuring state from here.
    if (token !== this.measureToken) return;

    if (result) {
      this.measuredLkfs = result.lkfs;
      this.measuredTpDbtp = result.dbtp;
      this.measuredForMode = key;
    }
    this.measuringRaw = false;
    this.callbacks.onMeasuring(false);
    this.apply();
  }

  // ---- Lifecycle ----

  async initialize(): Promise<void> {
    if (!this.supported || !this.constants) return;
    const token = ++this.loadToken;
    this.callbacks.onError(null);
    this.callbacks.onReady(false);
    this.callbacks.onLoadProgress(0);

    try {
      const Ctor =
        window.AudioContext ||
        (window as typeof window & { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      // Pin the rate: every shipped FIR is designed at 48 kHz, and letting
      // the device rate through would reinterpret those taps.
      const context = new Ctor({ sampleRate: CONTEXT_SAMPLE_RATE });
      this.context = context;
      this.callbacks.onMaxChannels(context.destination.maxChannelCount);

      // A fresh context starts suspended; without a resume the worklet's
      // `process` never runs, so a paused measurement never advances either.
      // Autoplay policy can refuse this outside a user gesture, so also arm a
      // one-shot fallback that resumes on the next pointer interaction.
      await context.resume().catch(() => {});
      if (context.state === "suspended") this.armResumeOnGesture(context);

      const channelCount = Math.max(this.layoutChannels.length, 2);
      const client = await DspEngineClient.create(context, channelCount, {
        onFrame: (frame) => this.onFrame(frame),
        onEnded: () => this.onEnded(),
        onMeasureProgress: (progress) => this.callbacks.onMeasureProgress(progress),
        onMeasured: (result) => {
          if (!this.exactMeasureKey) return;
          this.measuredLkfs = result.lkfs;
          this.measuredTpDbtp = result.dbtp;
          this.measuredForMode = this.exactMeasureKey;
          this.apply();
        },
        onError: (message) => this.callbacks.onError(message),
      });
      if (token !== this.loadToken) {
        client.dispose();
        return;
      }
      this.client = client;
      await client.ready;

      this.monitorGain = context.createGain();
      this.monitorGain.gain.value = this.muted ? 0 : this.volume;
      client.node.connect(this.monitorGain).connect(context.destination);

      // The engine is created before its convolvers have taps — they render
      // silence until `apply()` lands, same as the old graph's `void`-fired
      // filter loads — so stems can start fetching immediately instead of
      // waiting behind ~800 KB of HRIR/XTC/EQ WAV. `setParams` always builds
      // a fresh engine with no taps of its own (they ride their own binary
      // channel, not the JSON block), so a profile already cached from a
      // prior init has to be re-pushed onto it explicitly here — otherwise
      // `loadDecodeFilterSet`'s cache-hit path finds nothing to fetch and
      // never sends the taps this new engine actually needs.
      client.setParams(this.buildParams());
      if (this.loadedDecodeProfile === this.spatialProfile && this.decodeTaps) {
        client.setDecodeTaps(this.decodeTaps.slice());
      }
      if (this.loadedXtcProfile === this.transauralProfile && this.xtcTaps) {
        client.setXtcTaps(this.xtcTaps.slice());
      }
      await Promise.all([
        this.loadDecodeFilterSet(this.spatialProfile),
        this.loadXtcFilterSet(this.transauralProfile),
        Promise.all([this.loadMasteringFirs(), this.loadReferenceMatchFir()]).then(() => this.apply()),
        this.loadStemEqFirs().then(() => this.apply()),
        this.loadStems(token, context, client),
      ]);
      if (token !== this.loadToken) return;

      this.callbacks.onReady(true);
      await this.measureIfNeeded();
    } catch (error) {
      if (token === this.loadToken) {
        this.callbacks.onError(error instanceof Error ? error.message : "Preview failed to load");
      }
    }
  }

  /**
   * Fetch and decode the stems concurrently, but hand them to the engine
   * strictly in `previewableStems()` order — the engine's stem index is push
   * order (`push_stem` appends), and `stemOrder`/`buildEngineParams` both
   * address stems by position, so the network is not allowed to decide it.
   */
  private async loadStems(token: number, context: AudioContext, client: DspEngineClient) {
    const sources = this.previewableStems();
    if (!sources.length) {
      this.callbacks.onLoadProgress(1);
      return;
    }

    // Stems finish decoding in tight clusters, not evenly spaced — flushing
    // progress straight from each completion would fire a full page
    // re-render per stem in that cluster, right when the main thread is
    // busiest with decode work. Coalesce same-frame completions instead.
    let decoded = 0;
    let progressFlushScheduled = false;
    const scheduleProgressFlush = () => {
      if (progressFlushScheduled) return;
      progressFlushScheduled = true;
      window.requestAnimationFrame(() => {
        progressFlushScheduled = false;
        if (token !== this.loadToken) return;
        this.callbacks.onLoadProgress(decoded / sources.length);
      });
    };

    for (let start = 0; start < sources.length; start += STEM_DECODE_CONCURRENCY) {
      const chunk = sources.slice(start, start + STEM_DECODE_CONCURRENCY);
      const buffers = await Promise.all(
        chunk.map((stem) => loadBuffer(context, (stem.preview_url || stem.audio_url)!)),
      );
      if (token !== this.loadToken) return;

      for (const buffer of buffers) {
        // `.slice()` is a memcpy of the channel view; `Float32Array.from`
        // takes V8's generic per-element iterator path over the same bytes.
        // The copies are transferred, so they leave the main thread with the
        // call below rather than sitting alongside the AudioBuffer.
        const left = buffer.getChannelData(0).slice();
        const right = buffer.getChannelData(Math.min(1, buffer.numberOfChannels - 1)).slice();
        client.addStem(left, right);
        this.duration = Math.max(this.duration, buffer.duration);
        this.callbacks.onDuration(this.duration);
      }

      decoded += chunk.length;
      scheduleProgressFlush();
    }
    this.callbacks.onLoadProgress(1);
  }

  private onFrame(frame: { position: number; meters: number[]; spectrum: number[] }) {
    if (!this.scrubbing) {
      this.currentTimeRef.current = frame.position / CONTEXT_SAMPLE_RATE;
      this.callbacks.onCurrentTime(this.currentTimeRef.current);
    }

    const meters = frame.meters;
    const stemCount = this.stemOrder.length;
    const channels = this.layoutChannels;
    const stemLevels = new Map<string, MeterLevel[]>();
    const stemSpectrum = new Map<string, { level: number; centroid: number }>();
    for (let i = 0; i < stemCount; i += 1) {
      const o = i * 4;
      const bars = [level(meters[o] ?? 0, meters[o + 1] ?? 0)];
      if ((this.stemChannelCounts[i] ?? 1) >= 2) bars.push(level(meters[o + 2] ?? 0, meters[o + 3] ?? 0));
      stemLevels.set(this.stemOrder[i], bars);
      const s = i * 2;
      stemSpectrum.set(this.stemOrder[i], { level: frame.spectrum[s] ?? 0, centroid: frame.spectrum[s + 1] ?? 0 });
    }
    this.stemLevels.current = stemLevels;
    this.stemSpectrum.current = stemSpectrum;

    const channelLevels = new Map<string, MeterLevel>();
    const base = stemCount * 4;
    for (let i = 0; i < channels.length; i += 1) {
      channelLevels.set(channels[i], level(meters[base + i * 2] ?? 0, meters[base + i * 2 + 1] ?? 0));
    }
    this.channelLevels.current = channelLevels;

    const outBase = base + channels.length * 2;
    this.headphoneLevels.current = {
      left: level(meters[outBase] ?? 0, meters[outBase + 1] ?? 0),
      right: level(meters[outBase + 2] ?? 0, meters[outBase + 3] ?? 0),
    };
  }

  /**
   * Autoplay policy can leave a freshly created context suspended outside a
   * user gesture; catch the next pointer interaction and resume it then, so
   * a paused measurement started on load isn't stuck forever waiting for one.
   */
  private armResumeOnGesture(context: AudioContext) {
    this.disarmResumeOnGesture();
    const resume = () => {
      this.disarmResumeOnGesture();
      if (context.state === "suspended") void context.resume();
    };
    this.resumeOnGesture = resume;
    window.addEventListener("pointerdown", resume, { once: true });
  }

  private disarmResumeOnGesture() {
    if (this.resumeOnGesture) {
      window.removeEventListener("pointerdown", this.resumeOnGesture);
      this.resumeOnGesture = null;
    }
  }

  private onEnded() {
    this.playing = false;
    this.callbacks.onPlaying(false);
    this.moveTo(0);
    this.silenceLevels();
  }

  reset() {
    this.loadToken += 1;
    this.playing = false;
    this.duration = 0;
    this.currentTimeRef.current = 0;
    this.measuredForMode = null;
    this.exactMeasureKey = null;
    this.measureToken += 1;
    this.measuringRaw = false;
    this.stemEqTaps = new Map();
    this.referenceFirUrl = null;
    this.referenceTaps = null;
    this.client?.dispose();
    this.client = null;
    this.monitorGain?.disconnect();
    this.monitorGain = null;
    void this.context?.close();
    this.context = null;
    this.disarmResumeOnGesture();
    this.stemLevels.current = new Map();
    this.channelLevels.current = new Map();
    this.headphoneLevels.current = { left: SILENT_METER_LEVEL, right: SILENT_METER_LEVEL };
  }

  dispose() {
    this.reset();
  }
}
