# Phase 5 — Linked dynamic EQ (optional; highest risk)

Read `docs/plans/mastering/README.md` first for context and ground rules.
Requires phases 0 and 3. **Read `docs/plans/mixing/phase13_report.md` §9
before starting** — it is the closest prior art in this codebase for why
multi-detector processing of broadband material fails, and this phase's
design exists to avoid exactly that failure.

## Goal

The mastering EQ is a static profile curve; the bus compressor is
full-range. Neither can do the standard surgical mastering moves: tame a
harsh 3–4 kHz region only when it flares, de-ess a bright master, tuck a
resonant low-mid on loud passages. Current practice reaches for a
**dynamic EQ** here rather than a multiband compressor — bands act only
when triggered, and the signal path stays a single full-range path (no
crossover recombination) when they don't.

Add a dynamic EQ stage: up to N (suggest 4) bell bands, each with
frequency, Q, threshold, ratio-like depth, attack/release — **linked
detection**: one sidechain per band, computed from the weighted bed sum
(the same linked topology as `bus_compress`), producing one gain
trajectory per band applied identically to every non-LFE channel. That
keeps the stage a shared time-varying filter across channels — imaging
is preserved for the same reason the bus compressor preserves it.

## Why this avoids the phase 13 failure

Mixing phase 13 died because three *independent* detectors on one
broadband event diverged through its decay and morphed its timbre. Here:

- Detection is **per band but linked across channels** — no channel
  divergence by construction.
- A band's filter is a parametric bell riding between 0 dB and its set
  depth — the signal is **never split into bands and recombined**; at
  rest the stage is bit-transparent (gain 1.0 bypasses the section, the
  same skip-at-unity discipline as the elevation directional band).
- Bands are user-placed and few, not a crossover covering the spectrum —
  the cymbal-decay case that broke phase 13 (full-spectrum event crossing
  all detectors) hits one band here only if the user put one there.

Still: a single band on broadband material can breathe. The validation
section's decaying-crash fixture is mandatory, and the phase closes as
rejected — like phase 13 did — if spectral tilt through a decay moves
audibly more than an equivalent static dip.

## Design decisions (make, document, implement)

- Placement: between the static EQ and the bus compressor (surgical
  correction before glue — and before bass management so the invariant
  argument in contract §1 §"Bass never has to compensate" continues to
  hold: a shared time-varying filter commutes with the LF sum the same
  way the linked compressor gain does).
- Implementation: per-band envelope follower (reuse
  `compressor.rs::alpha` + the fast/slow max-envelope idiom) on a
  band-filtered copy of the linked detector signal; gain realized as a
  time-varying biquad (recompute coefficients per block at the streaming
  block rate, interpolated — not per sample) shared by all channels.
- Surface: manifest block `mastering.dynamic_eq.bands[]`; no profiles in
  v1 (explicit-control contract — presets can come once usage shows
  which moves recur). UI: one EffectPanel with band rows, following
  `MasteringSection.tsx` patterns and `web_ui_design.md`.
- Default: no bands, stage absent from the params block — zero cost on
  the wire and in the worklet when unused.

## Deliverables

1. `dsp-core` offline + streaming stage, PyO3 + wasm, chain wiring,
   manifest/config/API/UI surface, served constants for any structural
   values.
2. Parity contract §1 row + §2 additions; wasm rebuild; bench (benched
   with 4 active bands per the default-off-is-benched-on rule).
3. Standards note: none required (no delivery standard governs this),
   but the commutation argument goes in the contract.

## Validation

- Golden: sine-burst fixture per band parameter (threshold/depth/attack/
  release behave as specified); null test at rest (bit-transparent).
- The decaying-crash fixture: broadband decay through an active 3.8 kHz
  band — measure spectral-tilt trajectory vs a static dip of equal
  average depth; listening A/B at matched loudness. Rejection criteria
  stated up front, phase 13-style.
- `stream_equivalence.rs`; full suite; bench with 4 bands active.
