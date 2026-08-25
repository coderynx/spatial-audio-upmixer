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
import {
  applyTruePeakCeiling,
  bypassMatchDb,
  correctionGain,
  type DeliveryTarget,
} from "./audioAnalysis";
import type {
  EngineConstants,
  SpatialProfile,
  TransauralProfile,
} from "./masteringProfiles";
import {
  loudnessWeight,
  resolveBassParams,
  resolveCompParams,
  resolveDyneqBands,
  resolveDeliveryTarget,
} from "./masteringProfiles";
import type { MasterPreview } from "./masterPreview";
import { DspEngineClient } from "./wasmEngine/engineClient";
import { buildEngineParams } from "./wasmEngine/engineParams";
import { LoudnessCalibration } from "./wasmEngine/calibration";
import { FilterTapCache } from "./wasmEngine/filterTaps";
import {
  SILENT_MASTER_METERS,
  SILENT_METER_LEVEL,
  decodeMeterFrame,
  type MasterMeters,
  type MeterFrame,
  type MeterLevel,
  type StemSpectrum,
} from "./wasmEngine/meters";
import { loadStemsInto } from "./wasmEngine/stemLoader";
import { resolveStemMixes } from "./wasmEngine/stemMix";
import {
  POSITIONAL_CHANNELS,
  SILENT_LOUDNESS,
  engineRef,
  type EngineCallbacks,
  type EngineRef,
  type LoudnessSummary,
  type MixPreview,
  type OutputMode,
} from "./wasmEngine/engineTypes";

export { applyTruePeakCeiling } from "./audioAnalysis";
export {
  POSITIONAL_CHANNELS,
  SILENT_LOUDNESS,
  engineRef,
  type EngineCallbacks,
  type EngineRef,
  type LoudnessSummary,
  type MixPreview,
  type OutputMode,
} from "./wasmEngine/engineTypes";
export { withReferenceMatchParams } from "./wasmEngine/filterTaps";
export type { MasterMeters, MeterLevel, StemSpectrum } from "./wasmEngine/meters";

const CONTEXT_SAMPLE_RATE = 48000;

export class PreviewAudioEngine {
  readonly supported = Boolean(
    window.AudioContext ||
      (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext,
  );

  stems: ProjectStem[] = [];
  scene: { stems?: StemScene } = {};
  mix?: MixPreview;
  sourcePreviewUrl: string | null = null;
  mastering?: MasterPreview;
  /** The manifest's `routing` block, in its manifest shape — the per-track
      send values the served constants only supply defaults for. */
  routing?: { height_directional_band_gain?: number };
  layoutChannels: string[] = POSITIONAL_CHANNELS;
  outputMode: OutputMode = "binaural";
  spatialProfile: SpatialProfile = "studio";
  transauralProfile: TransauralProfile = "stereo";
  constants!: EngineConstants;
  positionalChannels: string[] = [];
  speakerEnabled: Record<string, boolean> = {};
  /** Transport A/B. The bypassed programme is measured on its own and
   * monitored at the mastered side's loudness — see `matchDb`. */
  masteringBypassed = false;
  /** Stage-scoped A/B for the reference matcher alone, on the same
   * measure-then-match machinery. Ignored while the whole chain is bypassed,
   * which already strips the matcher. */
  matchBypassed = false;

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
  private appliedDownmixLock = false;

  private readonly taps = new FilterTapCache();
  private readonly calibration = new LoudnessCalibration({
    measure: (weights) => this.client?.measure(weights) ?? Promise.resolve(null),
    apply: () => this.apply(),
    onMeasuring: (measuring) => this.callbacks.onMeasuring(measuring),
    onProgress: (progress) => this.callbacks.onMeasureProgress(progress),
    pause: () => {
      const wasPlaying = this.playing;
      if (wasPlaying) this.pause();
      return wasPlaying;
    },
    resume: () => void this.playFrom(this.currentTimeRef.current),
  });
  private loudness: LoudnessSummary = SILENT_LOUDNESS;
  /** Stems and filter sets are all in the engine. A measurement before this
      would calibrate against a half-built engine — and stamp the mode as
      measured, so the real one never runs. */
  private loaded = false;
  private resumeOnGesture: (() => void) | null = null;
  private stemOrder: string[] = [];
  /** Parallel to `stemOrder` — how many bars each stem's meter shows. */
  private stemChannelCounts: number[] = [];

  readonly stemSpectrum: EngineRef<Map<string, StemSpectrum>> = engineRef(new Map());
  readonly channelLevels: EngineRef<Map<string, MeterLevel>> = engineRef(new Map());
  readonly stemLevels: EngineRef<Map<string, MeterLevel[]>> = engineRef(new Map());
  readonly headphoneLevels: EngineRef<{ left: MeterLevel; right: MeterLevel }> = engineRef({
    left: SILENT_METER_LEVEL,
    right: SILENT_METER_LEVEL,
  });
  readonly masterMeters: EngineRef<MasterMeters> = engineRef(SILENT_MASTER_METERS);
  readonly currentTimeRef: EngineRef<number> = engineRef(0);

  constructor(private readonly callbacks: EngineCallbacks) {}

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
    if (!this.calibration.covers(this.measureKey())) return false;
    if (this.context.state === "suspended") await this.context.resume();
    const frame = Math.max(0, Math.min(time, this.duration)) * CONTEXT_SAMPLE_RATE;
    this.currentTimeRef.current = frame / CONTEXT_SAMPLE_RATE;
    this.callbacks.onCurrentTime(this.currentTimeRef.current);
    this.playing = true;
    this.client.start(frame, this.loop);
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
    this.masterMeters.current = SILENT_MASTER_METERS;
    for (const [key, spectrum] of this.stemSpectrum.current) {
      this.stemSpectrum.current.set(key, { ...spectrum, level: 0 });
    }
  }

  stop() {
    this.pause();
    void this.moveTo(0);
  }

  async moveTo(time: number): Promise<number> {
    const clamped = Math.max(0, Math.min(time, this.duration));
    this.currentTimeRef.current = clamped;
    this.callbacks.onCurrentTime(clamped);
    await this.client?.seek(clamped * CONTEXT_SAMPLE_RATE);
    return clamped;
  }

  async seek(time: number) {
    const wasPlaying = this.playing;
    const clamped = Math.max(0, Math.min(time, this.duration));
    this.currentTimeRef.current = clamped;
    this.callbacks.onCurrentTime(clamped);
    if (wasPlaying) {
      this.client?.start(clamped * CONTEXT_SAMPLE_RATE, this.loop);
      return;
    }
    await this.client?.seek(clamped * CONTEXT_SAMPLE_RATE);
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
    await this.seek(time);
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

  /** A/B the master chain. The two sides are different programmes, so this
   * measures the new one before it plays — the same calibration gate a mode
   * switch goes through — and matches their loudness once it has both. */
  applyMasteringBypass(bypassed: boolean) {
    this.masteringBypassed = bypassed;
    this.apply();
    void this.measureIfNeeded();
  }

  /** A/B the reference matcher on its own, at matched loudness. */
  applyMatchBypass(bypassed: boolean) {
    this.matchBypassed = bypassed;
    this.apply();
    void this.measureIfNeeded();
  }

  /**
   * The filter set has to reach the engine *before* the measurement starts:
   * a pass forks the engine as it is when the message lands, so measuring
   * across a still-pending tap fetch calibrates the new profile's gain
   * against the previous profile's decode bank — and how much it is off by
   * depends on which profile was loaded before, and on whether the fetch won
   * the race.
   */
  async retuneVoicing(profile: SpatialProfile) {
    this.spatialProfile = profile;
    this.apply();
    await this.loadDecodeFilterSet(profile);
    await this.measureIfNeeded();
  }

  async retuneCrosstalkVoicing(profile: TransauralProfile) {
    this.transauralProfile = profile;
    this.apply();
    await this.loadXtcFilterSet(profile);
    await this.measureIfNeeded();
  }

  async loadDecodeFilterSet(profile: SpatialProfile): Promise<boolean> {
    if (!this.context || !this.constants) return false;
    const ok = await this.taps.loadDecode(
      this.context, this.constants, profile, () => profile === this.spatialProfile,
    );
    // A slice, not the cached field itself: `setDecodeTaps` transfers its
    // argument's buffer, which would detach the cache entry.
    if (ok && this.taps.decodeTaps) this.client?.setDecodeTaps(this.taps.decodeTaps.slice());
    return ok;
  }

  async loadXtcFilterSet(profile: TransauralProfile): Promise<boolean> {
    if (!this.context || !this.constants) return false;
    const ok = await this.taps.loadXtc(
      this.context, this.constants, profile, () => profile === this.transauralProfile,
    );
    if (ok && this.taps.xtcTaps) this.client?.setXtcTaps(this.taps.xtcTaps.slice());
    return ok;
  }

  private async loadMasteringFirs() {
    if (!this.context || !this.constants) return;
    await this.taps.loadMastering(this.context, this.constants, this.mastering);
  }

  private async loadReferenceMatchFir() {
    if (!this.context) return;
    await this.taps.loadReferenceMatch(this.context, this.mastering);
  }

  private async loadStemEqFirs() {
    if (!this.context || !this.constants) return;
    await this.taps.loadStemEq(this.context, this.constants, this.mix?.stem_eq ?? {});
  }

  private previewableStems(): ProjectStem[] {
    return this.stems.filter((stem) => stem.preview_url || stem.audio_url);
  }

  apply() {
    if (!this.client || !this.constants) return;
    this.client.updateParams(this.buildParams());
  }

  applyMix() {
    const downmixLock = this.mix?.spatial_downmix_lock ?? false;
    const changed = downmixLock !== this.appliedDownmixLock;
    this.appliedDownmixLock = downmixLock;
    this.apply();
    if (changed) void this.measureIfNeeded();
  }

  private buildParams() {
    // Resolve the profile against the project's per-field overrides here, so
    // a moved pot reaches the worklet instead of being replaced by the bare
    // preset on the way (ledger D30).
    const bass = resolveBassParams(this.mastering?.bass, this.constants.bassProfiles);
    const comp = resolveCompParams(this.mastering?.compressor, this.constants.compProfiles);
    // Resolve the delivery target the way the export does, so a manifest that
    // names a preset without spelling out its numbers calibrates the preview
    // to the same loudness the bounce lands on.
    const delivery = resolveDeliveryTarget(
      this.mastering?.loudness,
      this.constants.deliveryTargets,
      this.constants.deliveryDefault,
    );
    const target = delivery.target_lkfs;
    const normalize = this.mastering?.loudness?.normalize ?? true;
    // One gain stage covers the whole job here, unlike the export chain's two,
    // so every mode gets the full budget — see
    // docs/contracts/preview_export_parity.md.
    const measured = this.calibration.measured;
    const correction = correctionGain(
      measured,
      delivery,
      this.constants.loudnessMaxGainDb,
      normalize,
    );
    // MONITOR domain, folded into the same ramp: the A/B match never reaches
    // an export or the manifest, unlike `master.output_gain`'s own meaning.
    const matchDb = this.matchDb(delivery, normalize);
    const matched = correction * 10 ** (matchDb / 20);
    // A match that boosts is still bound by the ceiling: the bypassed side
    // has no limiter of its own, so the compensation must not clip the DAC.
    const outputGain = this.calibration.raw
      ? 1
      : matchDb > 0
        ? applyTruePeakCeiling(measured.dbtp, matched, delivery.max_tp_dbtp)
        : matched;
    this.publishLoudness({
      integratedLkfs: measured.lkfs + 20 * Math.log10(correction),
      truePeakDbtp: measured.dbtp + 20 * Math.log10(correction),
      targetLkfs: target,
      ceilingDbtp: delivery.max_tp_dbtp,
      bypassMatchDb: matchDb,
    });

    const previewable = this.previewableStems();
    this.stemOrder = previewable.map((stem) => stem.stem_key.split("@", 1)[0]);
    this.stemChannelCounts = previewable.map((stem) => stem.channels);
    return buildEngineParams({
      constants: this.constants,
      layoutChannels: this.layoutChannels,
      sendOverrides: {
        heightDirectionalBandGain: this.routing?.height_directional_band_gain,
      },
      speakerEnabled: this.speakerEnabled,
      spatialDownmixLock: this.mix?.spatial_downmix_lock ?? false,
      stems: resolveStemMixes({
        stems: this.previewableStems(),
        scene: this.scene,
        mix: this.mix,
        stemEqTaps: this.taps.stemEqTaps,
        constants: this.constants,
      }),
      master: {
        comp,
        bass,
        highpassHz: this.mastering?.highpass?.enabled
          ? this.mastering.highpass.cutoff_hz ?? 20
          : null,
        clip: this.mastering?.clip?.enabled
          ? {
              clip_db: this.mastering.clip.clip_db ?? 0.5,
              knee: this.mastering.clip.knee ?? 1,
            }
          : null,
        eqFir: this.taps.masterEqTaps ?? undefined,
        eqStrength: this.mastering?.eq?.strength ?? 1,
        dynamicEq: resolveDyneqBands(
          this.mastering?.dynamic_eq,
          this.constants.dyneqProfiles,
        ),
        referenceFir: this.taps.referenceTaps ?? undefined,
        referenceGain: this.mastering?.match_reference?.rms
          ? 10 ** ((this.mastering.match_reference.rms_gain_db ?? 0) / 20)
          : 1,
        outputGain,
        limiterCeilingDbtp: delivery.max_tp_dbtp,
      },
      outputMode: this.outputMode,
      spatialProfile: this.spatialProfile,
      transauralProfile: this.transauralProfile,
      meterWeights: this.measureWeights(),
    });
  }

  /** The A/B's compensating monitor gain, once both sides are measured. */
  private matchDb(delivery: DeliveryTarget, normalize: boolean): number {
    if (!this.masteringBypassed && !this.matchBypassed) return 0;
    return bypassMatchDb(
      this.calibration.get(this.measureKey(false, false)),
      this.calibration.get(this.measureKey()),
      delivery,
      this.constants.loudnessMaxGainDb,
      normalize,
    );
  }

  private publishLoudness(summary: LoudnessSummary) {
    const changed = (Object.keys(summary) as (keyof LoudnessSummary)[]).some(
      (field) => Math.abs(summary[field] - this.loudness[field]) > 1e-6,
    );
    if (!changed) return;
    this.loudness = summary;
    this.callbacks.onLoudness(summary);
  }

  /**
   * Measure the programme once per output mode/profile/bypass combination, so
   * a mode switch — or either A/B toggle, whose sides are different
   * programmes — re-measures rather than reusing a stale correction. The pass
   * walks the whole programme, so it resolves minutes later on a long track
   * and pauses transport meanwhile (playback is mandatory-calibrated, see
   * `playFrom`).
   */
  private measureKey(
    bypassed = this.masteringBypassed,
    matchBypassed = this.matchBypassed,
  ): string {
    // A whole-chain bypass already strips the matcher, so the stage flag adds
    // no programme of its own there — and must not cost a second pass.
    const chain = bypassed ? "bypassed" : matchBypassed ? "match-bypassed" : "mastered";
    const downmixLock = this.mix?.spatial_downmix_lock ? "locked" : "unlocked";
    return `${this.outputMode}:${this.spatialProfile}:${this.transauralProfile}:${chain}:${downmixLock}`;
  }

  /** BS.1770 weights for the channels the measurement sees. Every collapse
   * mode delivers a unity-weighted pair; a native bed is weighted per
   * channel, matching what the export measures. The core overrides these on
   * a native bed wider than 5.1, where the 5.1 re-render it measures instead
   * fixes its own weights (`docs/standards/loudness_dsp_bs1770.md`). */
  private measureWeights(): number[] {
    if (this.outputMode !== "native") return [1, 1];
    return this.layoutChannels.map(loudnessWeight);
  }

  private async measureIfNeeded() {
    if (!this.client || !this.loaded) return;
    await this.calibration.ensure(this.measureKey(), this.measureWeights());
  }

  async initialize(): Promise<void> {
    if (!this.supported || !this.constants) return;
    this.reset();
    const token = ++this.loadToken;
    this.loaded = false;
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
          if (this.calibration.refine(result)) this.apply();
        },
        onError: (message) => this.callbacks.onError(message),
      });
      if (token !== this.loadToken) {
        client.dispose();
        void context.close();
        return;
      }
      this.client = client;
      await client.ready;

      this.monitorGain = context.createGain();
      this.monitorGain.gain.value = this.muted ? 0 : this.volume;
      client.node.connect(this.monitorGain).connect(context.destination);

      // `setParams` always builds a fresh engine with no taps of its own, so a
      // profile already cached from a prior init has to be re-pushed here —
      // the loaders' cache-hit path would otherwise find nothing to fetch.
      client.setParams(this.buildParams());
      if (this.taps.loadedDecodeProfile === this.spatialProfile && this.taps.decodeTaps) {
        client.setDecodeTaps(this.taps.decodeTaps.slice());
      }
      if (this.taps.loadedXtcProfile === this.transauralProfile && this.taps.xtcTaps) {
        client.setXtcTaps(this.taps.xtcTaps.slice());
      }
      await Promise.all([
        this.loadDecodeFilterSet(this.spatialProfile),
        this.loadXtcFilterSet(this.transauralProfile),
        Promise.all([this.loadMasteringFirs(), this.loadReferenceMatchFir()]).then(() => this.apply()),
        this.loadStemEqFirs().then(() => this.apply()),
        this.loadStems(token, context, client),
      ]);
      if (token !== this.loadToken) {
        client.dispose();
        void context.close();
        return;
      }

      // A profile switch during the loads above was dropped by the gate in
      // `measureIfNeeded`, so re-resolve both against the current fields: the
      // cached sets only need re-pushing into the fresh engine.
      await Promise.all([
        this.loadDecodeFilterSet(this.spatialProfile),
        this.loadXtcFilterSet(this.transauralProfile),
      ]);
      if (token !== this.loadToken) {
        client.dispose();
        void context.close();
        return;
      }

      this.loaded = true;
      this.callbacks.onReady(true);
      await this.measureIfNeeded();
    } catch (error) {
      if (token === this.loadToken) {
        this.callbacks.onError(error instanceof Error ? error.message : "Preview failed to load");
      }
    }
  }

  private async loadStems(token: number, context: AudioContext, client: DspEngineClient) {
    this.duration = await loadStemsInto(context, client, this.previewableStems(), {
      isCurrent: () => token === this.loadToken,
      onProgress: (fraction) => this.callbacks.onLoadProgress(fraction),
      onDuration: (seconds) => {
        this.duration = seconds;
        this.callbacks.onDuration(seconds);
      },
    });
  }

  private onFrame(frame: MeterFrame) {
    if (!this.scrubbing) {
      this.currentTimeRef.current = frame.position / CONTEXT_SAMPLE_RATE;
      this.callbacks.onCurrentTime(this.currentTimeRef.current);
    }
    const decoded = decodeMeterFrame(frame, this.stemOrder, this.stemChannelCounts, this.layoutChannels);
    this.stemLevels.current = decoded.stemLevels;
    this.stemSpectrum.current = decoded.stemSpectrum;
    this.channelLevels.current = decoded.channelLevels;
    this.headphoneLevels.current = decoded.headphoneLevels;
    this.masterMeters.current = decoded.master;
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
    void this.moveTo(0);
    this.silenceLevels();
  }

  reset() {
    this.loadToken += 1;
    this.loaded = false;
    this.playing = false;
    this.duration = 0;
    this.currentTimeRef.current = 0;
    this.calibration.reset();
    this.taps.resetPerProject();
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
