# Phase 7 — Reference matching usability

Read `docs/plans/mastering/README.md` first for context and ground rules.
Requires phase 3 (loudness-matched audition builds on the matched-bypass
machinery). Independent of phases 4–6.

## Goal

The reference matcher's analysis core is sound — BS.1770-weighted gated
power spectra, one shared minimum-phase FIR, strength scaling, soft-knee
max-correction clamp, ±2 dB sub-bass guard (`match_reference/`). What it
lacks are the three controls every serious match-EQ workflow depends on
(Ozone-style practice: smooth ~50%, match well under 100%, restrict the
range, judge at matched loudness):

1. **Smoothing amount.** The correction curve is smoothed at a fixed 1/3
   octave. Expose the smoothing bandwidth (1/12…1 octave, default the
   current 1/3) — coarse smoothing matches tonal balance, fine smoothing
   chases the reference's exact resonances; the user should choose.
2. **Frequency-range masks.** Match only a range (e.g. keep your own low
   end, match the top): low/high bound on where the curve applies, with
   raised-cosine easing into unity outside the range so the FIR stays
   smooth. Default: full range (current behavior).
3. **Loudness-matched audition.** Toggling the match on/off in the
   preview currently changes level whenever `match_rms` is on, so the
   comparison is loudness-biased. Reuse phase 3's matched-bypass
   machinery scoped to this stage: audition spectral matching at equal
   integrated loudness.

## Design decisions (make, document, implement)

- All controls act at **curve realization**, not analysis:
  `compute_curve` already separates the strength/max-independent curve
  from `build_curve_fir` realization precisely so the web's server-side
  precompute can re-realize per knob change (D13 discipline). Smoothing
  and masks join `build_curve_fir`'s parameters; the stored analysis
  curve stays raw so no knob forces re-analysis.
- Curve realization lives in `dsp-core`
  (`match_reference::curve`) — the preview realizes the same FIR from
  the same stored curve (contract §1 row unchanged, parameters extended).
- Manifest: `mastering.match_reference` gains `smooth_octaves`,
  `low_hz`, `high_hz` (nullable, null = current defaults). API schema +
  `MasteringSection.tsx` panel rows follow the existing nullable-pot
  pattern.
- The LFE exemption and the shared-curve (never per-channel) rule are
  load-bearing and unchanged — restate in the phase report, do not
  touch.

## Deliverables

1. `dsp-core` curve-realization parameters + tests; PyO3/wasm; Python
   wrapper (`build_curve_fir`) and processor pass-through.
2. Manifest/config/API/UI surface; served constants where structural.
3. Parity contract §2 (new served fields on the reference-match block);
   wasm rebuild; bench (FIR length unchanged — expect flat).
4. Stage-scoped matched audition in the preview (web-side state machine
   only; measurement reuse from phase 3).

## Validation

- Golden: mask fixture (curve is unity outside [low, high] with smooth
  easing), smoothing fixture (narrowband reference notch survives 1/12
  oct smoothing, vanishes at 1 oct).
- Parity: preview-realized FIR vs export-realized FIR bit-compare on the
  same stored curve + knobs (`stream_equivalence.rs` or the dsp golden
  suite, wherever the existing match FIR parity test lives).
- Full suite; web tests; A/B note: matching a bright reference with
  low_hz = 300 Hz keeps the mix's low end (the canonical use case).
