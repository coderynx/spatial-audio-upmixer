import type {
  EngineConstants,
  EqProfileName,
  SpatialProfile,
  StemEqProfileName,
  TransauralProfile,
} from "../masteringProfiles";
import type { MasterPreview } from "../masterPreview";
import { loadDecodeTaps, loadFirTaps, loadXtcTaps } from "./filterAssets";

/** Appends the live realization knobs to a reference-match `fir_url` base, so
 * the FIR endpoint designs the filter for exactly this config. Unset controls
 * are left off the URL rather than sent as a number, so the server's own
 * defaults stay the single source (the delivery-target precedence rule). */
export function withReferenceMatchParams(
  firUrl: string,
  strength: number,
  maxDb: number,
  smoothOct?: number | null,
  lowHz?: number | null,
  highHz?: number | null,
): string {
  const separator = firUrl.includes("?") ? "&" : "?";
  const extra = ([["smooth_oct", smoothOct], ["low_hz", lowHz], ["high_hz", highHz]] as const)
    .filter(([, value]) => value != null)
    .map(([name, value]) => `&${name}=${value}`)
    .join("");
  return `${firUrl}${separator}strength=${strength}&max_db=${maxDb}${extra}`;
}

/**
 * Decoded FIR tap sets for the preview engine, cached by the thing that
 * identifies them (profile, asset name, or full URL) so an unrelated
 * mastering change doesn't re-fetch and re-decode the same WAV.
 */
export class FilterTapCache {
  decodeTaps: Float64Array | null = null;
  xtcTaps: Float64Array | null = null;
  stemEqTaps: Map<string, Float64Array> = new Map();
  masterEqTaps: Float64Array | null = null;
  referenceTaps: Float64Array | null = null;

  loadedDecodeProfile: SpatialProfile | null = null;
  loadedXtcProfile: TransauralProfile | null = null;
  private masterEqAsset: string | null = null;
  private referenceFirUrl: string | null = null;

  /**
   * @param isCurrent Re-checked after the fetch: two switches in quick
   *   succession race here, and the slower fetch must not be the one that
   *   lands in the engine.
   */
  async loadDecode(
    context: AudioContext,
    constants: EngineConstants,
    profile: SpatialProfile,
    isCurrent: () => boolean,
  ): Promise<boolean> {
    if (this.loadedDecodeProfile === profile && this.decodeTaps) return true;
    const taps = await loadDecodeTaps(context, constants.decodeFilterSet[profile]);
    if (!isCurrent()) return false;
    this.decodeTaps = taps;
    this.loadedDecodeProfile = profile;
    return true;
  }

  async loadXtc(
    context: AudioContext,
    constants: EngineConstants,
    profile: TransauralProfile,
    isCurrent: () => boolean,
  ): Promise<boolean> {
    if (this.loadedXtcProfile === profile && this.xtcTaps) return true;
    const taps = await loadXtcTaps(context, constants.xtcFilterSet[profile]);
    if (!isCurrent()) return false;
    this.xtcTaps = taps;
    this.loadedXtcProfile = profile;
    return true;
  }

  async loadMastering(
    context: AudioContext,
    constants: EngineConstants,
    mastering: MasterPreview | undefined,
  ): Promise<void> {
    const profile = mastering?.eq?.profile as EqProfileName | null | undefined;
    const asset = profile ? constants.eqFirAssets[profile] : null;
    if (asset === this.masterEqAsset && (asset === null || this.masterEqTaps)) return;
    this.masterEqAsset = asset ?? null;
    this.masterEqTaps = asset
      ? await loadFirTaps(`/eq_fir/${asset}.wav`, context).catch(() => null)
      : null;
  }

  /**
   * The server serves one correction curve per project as a base `fir_url`
   * and designs the actual filter on demand from the live `strength`/`max_db`
   * query params, so the URL itself is the cache key.
   */
  async loadReferenceMatch(
    context: AudioContext,
    mastering: MasterPreview | undefined,
  ): Promise<void> {
    const refCfg = mastering?.match_reference;
    const strength = refCfg?.strength ?? 1;
    const maxDb = refCfg?.max_db ?? 6;
    const active = Boolean(refCfg?.spectrum && refCfg.fir_url && strength > 0);
    const url = active
      ? withReferenceMatchParams(
          refCfg!.fir_url as string, strength, maxDb,
          refCfg!.smooth_octaves, refCfg!.low_hz, refCfg!.high_hz,
        )
      : null;
    if (url === this.referenceFirUrl && (url === null || this.referenceTaps)) return;
    this.referenceFirUrl = url;
    this.referenceTaps = url ? await loadFirTaps(url, context).catch(() => null) : null;
  }

  async loadStemEq(
    context: AudioContext,
    constants: EngineConstants,
    wanted: Record<string, string>,
  ): Promise<void> {
    const entries = await Promise.all(
      Object.entries(wanted).map(async ([stemKey, profile]) => {
        const asset = constants.stemEqFirAssets[profile as StemEqProfileName];
        if (!asset) return null;
        const taps =
          this.stemEqTaps.get(stemKey) ??
          (await loadFirTaps(`/eq_fir/${asset}.wav`, context).catch(() => null));
        return taps ? ([stemKey, taps] as const) : null;
      }),
    );
    this.stemEqTaps = new Map(
      entries.filter((entry): entry is readonly [string, Float64Array] => entry !== null),
    );
  }

  resetPerProject(): void {
    this.stemEqTaps = new Map();
    this.referenceFirUrl = null;
    this.referenceTaps = null;
  }
}
