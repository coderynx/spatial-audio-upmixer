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
import { applyTruePeakCeiling, loudnessGainFor } from "./audioAnalysis";
import type {
  BassProfileName,
  CompProfileName,
  EngineConstants,
  SpatialProfile,
  TransauralProfile,
} from "./masteringProfiles";
import type { MasterPreview } from "./masterPreview";
import { DspEngineClient } from "./wasmEngine/engineClient";
import { buildEngineParams } from "./wasmEngine/engineParams";
import { FilterTapCache } from "./wasmEngine/filterTaps";
import { SILENT_METER_LEVEL, decodeMeterFrame, type MeterFrame, type MeterLevel } from "./wasmEngine/meters";
import { loadStemsInto } from "./wasmEngine/stemLoader";
import { resolveStemMixes } from "./wasmEngine/stemMix";
import {
  POSITIONAL_CHANNELS,
  engineRef,
  type EngineCallbacks,
  type EngineRef,
  type MixPreview,
  type OutputMode,
} from "./wasmEngine/engineTypes";

export { applyTruePeakCeiling } from "./audioAnalysis";
export {
  POSITIONAL_CHANNELS,
  engineRef,
  type EngineCallbacks,
  type EngineRef,
  type MixPreview,
  type OutputMode,
} from "./wasmEngine/engineTypes";
export { withReferenceMatchParams } from "./wasmEngine/filterTaps";
export type { MeterLevel } from "./wasmEngine/meters";

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
  layoutChannels: string[] = POSITIONAL_CHANNELS;
  outputMode: OutputMode = "binaural";
  spatialProfile: SpatialProfile = "studio";
  transauralProfile: TransauralProfile = "stereo";
  constants!: EngineConstants;
  positionalChannels: string[] = [];
  speakerEnabled: Record<string, boolean> = {};

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

  private readonly taps = new FilterTapCache();
  private measuredLkfs = -70;
  private measuredTpDbtp = -70;
  private measuredForMode: string | null = null;
  /** Mode key the in-flight exact measurement's refinement belongs to. */
  private exactMeasureKey: string | null = null;
  /** While set, render uncorrected so the measurement sees the raw program. */
  private measuringRaw = false;
  private measureToken = 0;
  /** Transport was playing when a profile switch forced the pause below — resume once the winning pass lands. */
  private resumeAfterMeasure = false;
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

  private buildParams() {
    const bass = (this.mastering?.bass?.profile ?? null) as BassProfileName | null;
    const target = this.mastering?.loudness?.target ?? -18;
    const normalize = this.mastering?.loudness?.normalize ?? true;
    // One gain stage covers the whole job here, unlike the export chain's two,
    // so every mode gets the full budget — see
    // docs/contracts/preview_export_parity.md.
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
      stems: resolveStemMixes({
        stems: this.previewableStems(),
        scene: this.scene,
        mix: this.mix,
        stemEqTaps: this.taps.stemEqTaps,
        constants: this.constants,
      }),
      master: {
        compProfile: (this.mastering?.compressor?.profile ?? null) as CompProfileName | null,
        bassProfile: bass,
        eqFir: this.taps.masterEqTaps ?? undefined,
        eqStrength: this.mastering?.eq?.strength ?? 1,
        referenceFir: this.taps.referenceTaps ?? undefined,
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
   * Measure the programme once per output mode/profile combination, so a mode
   * switch re-measures rather than reusing a stale correction. The pass walks
   * the whole programme, so it resolves minutes later on a long track and
   * pauses transport meanwhile (playback is mandatory-calibrated, see
   * `playFrom`).
   */
  private measureKey(): string {
    return `${this.outputMode}:${this.spatialProfile}:${this.transauralProfile}`;
  }

  private async measureIfNeeded() {
    if (!this.client) return;
    const key = this.measureKey();
    // While a pass is in flight (`measuringRaw`), `measuredForMode` still
    // names the mode being replaced — switching back to it is not "already
    // calibrated", because the pass in flight is about to overwrite the one
    // measurement both modes share.
    const covered = this.measuringRaw ? this.exactMeasureKey : this.measuredForMode;
    if (covered === key) return;

    if (this.playing) {
      this.pause();
      this.resumeAfterMeasure = true;
    }
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
    if (this.resumeAfterMeasure) {
      this.resumeAfterMeasure = false;
      void this.playFrom(this.currentTimeRef.current);
    }
  }

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
          // `measuringRaw` means a newer fast pass is in flight, so this
          // refinement was posted for the mode/profile that pass replaced —
          // the worklet dropped it, but its result was already on the wire.
          if (!this.exactMeasureKey || this.measuringRaw) return;
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
      if (token !== this.loadToken) return;

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
