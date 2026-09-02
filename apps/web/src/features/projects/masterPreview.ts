// The mastering block as the preview sees it, plus the transport's A/B
// monitor switch.
//
// This is the manifest's shape, not a DSP surface — the stages themselves
// live in the shared Rust core (packages/dsp).

import type { DynamicEqBand } from "@/lib/manifest";

export type MasterPreview = {
  loudness?: {
    normalize?: boolean;
    target_preset?: string | null;
    target?: number | null;
    max_tp?: number | null;
  };
  highpass?: { enabled?: boolean; cutoff_hz?: number };
  clip?: { enabled?: boolean; clip_db?: number; knee?: number };
  eq?: { profile?: string | null; strength?: number };
  dynamic_eq?: { profile?: string | null; bands?: DynamicEqBand[] };
  // Server-precomputed correction curve, realized into a FIR on demand at
  // this config's strength/max_db — see docs/contracts/preview_export_parity.md
  // ledgers D12/D21. One shared FIR for every non-LFE channel, not a
  // per-channel bank.
  match_reference?: {
    fir_url?: string | null;
    rms_gain_db?: number;
    strength?: number;
    spectrum?: boolean;
    rms?: boolean;
    max_db?: number;
    smooth_octaves?: number | null;
    low_hz?: number | null;
    high_hz?: number | null;
  };
  compressor?: {
    profile?: string | null;
    threshold_db?: number | null;
    ratio?: number | null;
    attack_ms?: number | null;
    release_ms?: number | null;
    knee_db?: number | null;
    makeup_db?: number | null;
    sidechain_hpf_hz?: number | null;
  };
  bass?: {
    enabled?: boolean | null;
    profile?: string | null;
    sub_gain_db?: number | null;
    mid_gain_db?: number | null;
    unify_hz?: number | null;
    spread?: string | null;
    punch?: number | null;
    harmonics?: number | null;
    excite?: boolean | null;
    lfe_mode?: string | null;
    lfe_send?: number | null;
    decorrelate?: number | null;
    lfe_gain_db?: number | null;
  };
};

/**
 * The transport's bypass buttons. `bypassed` renders the bed with every tone
 * and dynamics stage stripped, keeping only loudness; `matchBypassed` strips
 * the reference matcher alone — both its spectral curve and its level gain,
 * since the level gain is what made the old stage A/B loudness-biased.
 * Session-only monitoring choices, not manifest fields, so neither has a
 * counterpart in the export (parity contract §3, P4).
 */
export function monitorMastering(
  mastering: MasterPreview | undefined,
  bypassed: boolean,
  matchBypassed = false,
): MasterPreview | undefined {
  if (bypassed) return mastering?.loudness ? { loudness: mastering.loudness } : undefined;
  if (!matchBypassed || !mastering?.match_reference) return mastering;
  return {
    ...mastering,
    match_reference: { ...mastering.match_reference, spectrum: false, rms: false },
  };
}
