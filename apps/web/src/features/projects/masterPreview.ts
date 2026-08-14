// The mastering block as the preview sees it, plus the transport's A/B
// monitor switch.
//
// This is the manifest's shape, not a DSP surface — the stages themselves
// live in the shared Rust core (packages/dsp).

export type MasterPreview = {
  loudness?: { normalize?: boolean; target?: number; max_tp?: number };
  eq?: { profile?: string | null; strength?: number };
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
    profile?: string | null;
    sub_gain_db?: number | null;
    mid_gain_db?: number | null;
    unify_hz?: number | null;
    spread?: string | null;
    punch?: number | null;
    excite?: boolean | null;
    lfe_mode?: string | null;
    lfe_send?: number | null;
    decorrelate?: number | null;
    lfe_gain_db?: number | null;
  };
};

/**
 * The transport's bypass button: render the bed with every tone and dynamics
 * stage stripped, keeping only loudness. A session-only monitoring choice,
 * not a manifest field — so it has no counterpart in the export.
 */
export function monitorMastering(
  mastering: MasterPreview | undefined,
  bypassed: boolean,
): MasterPreview | undefined {
  if (!bypassed) return mastering;
  return mastering?.loudness ? { loudness: mastering.loudness } : undefined;
}
