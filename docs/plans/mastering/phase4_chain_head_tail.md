# Phase 4 — Chain head (subsonic/DC) and pre-limiter soft clip

Read `docs/plans/mastering/README.md` first for context and ground rules.
Requires phase 0 (its GR/PSR stats are how the clipper's benefit is
demonstrated); phase 3's matched A/B makes the listening checks honest.

## Goal

Two missing chain elements, both standard in every modern mastering
signal path, both default-off:

1. **Chain head — subsonic high-pass + DC removal.** Nothing in the
   chain removes DC offset or sub-20 Hz energy; both waste limiter
   headroom and skew the loudness measurement's low end. Add an optional
   first stage (before reference matching): a gentle high-pass
   (12 dB/oct Butterworth, corner configurable 10–30 Hz, default 20) on
   every non-LFE channel, and the same filter minus the resonant corner
   on LFE is **not** applied — LFE is band-limited upstream and its
   sub content is the point; DC alone is removed there (first-order
   pole-zero DC blocker). One shared filter design for all non-LFE
   channels: identical LTI filtering commutes with the LF sum, so the
   invariant in parity contract §1 holds by the same argument as the EQ
   stage.
2. **Pre-limiter soft clip.** On transient material the look-ahead
   limiter currently does all peak control alone. Add an optional
   linked soft clipper between loudness normalization and the limiter:
   one shared transfer curve (tanh-family with adjustable knee), driven
   by a `clip_db` amount (how far below the limiter ceiling the knee
   sits, typical 0.5–1 dB of shave), applied identically to every
   non-LFE channel sample-by-sample. Linked in the only sense a
   memoryless nonlinearity can be: same curve, same parameters, no
   envelope — and therefore *not* gain-commuting. This is the first
   stage in the chain that deliberately breaks the shared-curve
   commutation invariant, which is why it sits after bass management
   (the LF sum is already distributed) and directly before the limiter.
   Record the deviation explicitly in the parity contract §1 note.

## Design decisions (make, document, implement)

- Order: head stage runs before reference matching (so the matcher never
  matches DC/rumble); clipper runs after loudness normalization, before
  the limiter — the loudness scalar must not be applied to an already
  clipped signal or the clip depth becomes target-dependent in the wrong
  direction. Update `chain.py`'s docstring order list and contract §1.
- The clipper is **off by default and stays off in every existing
  profile**; it is an explicit user choice (`mastering.clip` manifest
  block: `enabled`, `clip_db`, `knee`). Same for the head stage
  (`mastering.highpass`: `enabled`, `cutoff_hz`).
- Nonlinearity + downstream true-peak limiter: the clipper generates
  harmonics but no new peaks above its own ceiling; the limiter still
  guarantees dBTP compliance. No oversampling in the clipper for v1 —
  measure aliasing in validation; if audible on the synthetic fixtures,
  scope an oversampled variant as a follow-up rather than growing this
  phase.

## Deliverables

1. `dsp-core`: DC blocker + subsonic HPF kernel and the soft-clip stage,
   offline + streaming (`stream/master.rs` — both are causal, so they
   join `CausalChain`; no new look-ahead, no horizon change).
2. Chain wiring in `mastering/chain.py`, config/manifest keys via
   `register_block_keys`, served constants for the web
   (engine-constants endpoint + `engineParams.ts`), UI panels in
   `MasteringSection.tsx` (two new EffectPanels, following the existing
   toggle-remembers-profile pattern).
3. Parity contract §1 (order + commutation note), §2 (new constants);
   wasm rebuild; `npm run bench:engine`.

## Validation

- Golden tests: DC fixture (offset removed, signal preserved), 15 Hz
  rumble fixture (headroom recovered — compare limiter GR with/without
  head stage), clip fixture (PSR improves ≥ the shave amount on a
  transient train while limiter GR duty drops; THD reported).
- `stream_equivalence.rs` for both stages.
- Aliasing measurement for the clipper (single-tone fixtures at high
  drive); numbers in the phase report either way.
- Full suite; A/B listening note (matched loudness, phase 3) on the
  dense programme: clipper on vs off at equal integrated loudness.
