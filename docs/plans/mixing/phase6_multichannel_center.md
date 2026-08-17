# Phase 6 — Coherence-based derived center in MultichannelUpmixer

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 3 merged (this module's sends were converted there).
Affects only the non-stem `UpmixPipeline` path for multichannel input
missing a center channel.

## Goal

`packages/core/src/upmix/multichannel.py` derives a missing center as
`C = (0.707 * 0.5) * (FL + FR)` while leaving FL/FR untouched: the
same centered content then plays correlated from three speakers — +3 dB
center build-up, comb at the listening position, and double-counted again
by any later downmix. Replace with subtractive, coherence-based center
extraction: what moves to C is removed from FL/FR.

## Approach

The codebase already owns the right tool: the STFT coherence analysis and
center extraction used by the stereo pipeline
(`packages/core/src/decomposition/` — read `direct_ambient.py` /
`SoftMatrixDecomposer` and how `ChannelRouter` consumes `d.center`,
`d.front_L/R`). Reuse that machinery on the FL/FR pair when C is absent:

- extracted center → C (scaled by the existing `ITU_CENTER_COEFF`
  convention and `center_gain`),
- FL/FR replaced by the residual fronts from the same decomposition,
- all other derivations in `MultichannelUpmixer.process` (LFE source,
  SL/SR/BL/BR, heights) read the **original** FL/FR as today unless a
  derivation explicitly wants the residual — keep today's topology, note
  the choice in the module docstring.

Config: reuse existing knobs (`center_extraction_gain`,
`center_attenuation`) if their semantics fit; do not invent new ones
without need. `MultichannelUpmixer` is offline/file-based here — using
the batch decomposition path is acceptable; check what `UpmixPipeline`
already instantiates and share rather than re-implement.

Also fix the phase 0 report's related note if present: derived C is the
source for derived LFE (`src = C if C is not None`), so the new center
feeds LFE — verify the LFE derivation still receives full-bandwidth
front content (decide: original mid vs extracted center; original mid
`0.5*(FL+FR)` is the safer LFE source — keep it and say why in one
line).

## Relation to roadmap 2.3

Knowledge-base roadmap 2.3 (CenterWide-class *model* center extraction)
outranks DSP extraction in measured quality but requires an arch port.
This phase is the cheap DSP correction of an outright defect; 2.3 can
later replace the extractor behind the same seam. State this in the PR;
do not start 2.3 here.

## Tests

- Correlated mono content (identical FL=FR sine): C carries it, FL/FR
  residual near-silent, total energy preserved within 0.5 dB — no more
  +3 dB build-up (add the build-up measurement to the phase 0 kit and
  cite numbers).
- Uncorrelated FL/FR (independent noises): C near-silent, FL/FR
  essentially unchanged.
- Hard-panned content stays panned (no center bleed above a stated
  threshold).
- Existing-C passthrough inputs bit-identical (derivation only runs when
  C is absent — regression-anchor test).
- Existing `MultichannelUpmixer` tests updated; full suite green.

## Out of scope

- The stem pipeline (its center handling is routing-table-driven and
  intentional).
- Web preview: confirm whether the preview ever exercises multichannel
  channel derivation; if not (stem preview only), record that in the PR
  and skip parity work; if yes, parity contract applies as in phase 3.
- Roadmap 2.3 model work.

## Done when

- Tests above green, full suites green.
- Module docstring updated (derivation described honestly, one-line
  pointer to decomposition docs).
- A/B listening note: stereo-ish 5.1 source upmixed to 7.1.4 — center
  image stable, no hollow phantom/real-center comb, downmix of the
  result no longer over-weights centered content.
