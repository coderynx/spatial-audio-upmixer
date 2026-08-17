# Phase 11 — Content-aware routing: revive or delete

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phases 8–10 (stable sends, loudness-consistent renorm, final
panner — a time-varying layer on top of moving foundations would be
unreviewable). This is knowledge-base roadmap 4.1; its archaeology gate
comes first and can end the phase early. Both outcomes — revive or
delete — close the phase.

## Background

The stem router is fully static: per-track constant gains per stem. The
non-stem pipeline already adapts per frame (`ChannelRouter`: transient
gate, coherence masks), and human Atmos mixes adapt per section — the
classic criticisms of upmixers ("reverb tails glued to the dry signal",
"chorus doesn't open up") are exactly what static routing cannot do.
Stems make adaptation *easier* than the coherence path: content identity
is already known per stem.

Dead code exists from a removed prior attempt:
`packages/core/src/analysis/stem_analyzer.py` (unreferenced) and
`packages/core/src/separation/stem_analyzer.py` (test-only, referenced
from `tests/test_stem_router.py`).

## Step 1 — Archaeology (gate)

`git log --follow` both stem_analyzer modules and the commits that
removed their call sites. Answer in writing: what did the feature do,
why was it removed (quality? complexity? performance?), and does that
reason still hold after phases 0–10 rebuilt the send/level foundations?
Consult `~/Projects/upmixer-knowledge/roadmap.md` 4.1 and
`techniques/evaluation.md` §6.

Decision gate, one of:
- **Delete**: the removal reason stands → delete both modules and their
  test references per the repo dead-code rule; update roadmap 4.1 in the
  knowledge base to "resolved: deleted"; phase ends here.
- **Revive**: the reason is obsolete → step 2, and the archaeology
  writeup defines what NOT to repeat.

## Step 2 — Minimal revival (only if the gate says revive)

Scope discipline: ship the smallest audible win first, behind default-off
config, judged by listening. Candidate ladder — take the first rung, not
all three:

1. **Transient/sustain send split.** Per stem, split the surround/height
   send input into transient and sustain components (the codebase has
   spectral-flux transient scoring in the decomposition path — reuse the
   approach, not new analysis machinery); route sustain to the diffuse
   sends, keep transients front-anchored. This is the stem-domain
   equivalent of `ChannelRouter`'s transient gate and directly targets
   "drums smeared into surrounds".
2. **Per-section envelope on existing sends** (chorus opens heights) —
   only if 1 ships and listening asks for more.
3. Anything analyzer-driven beyond that goes back to the roadmap, not
   this phase.

Constraints:
- Runs post-separation, pre-routing, in core; web stays delivery-only.
- The preview must match: time-varying sends are DSP, so the streaming
  engine needs the same stage — `dsp-core` once, both bindings, parity
  contract re-hash, `npm run bench:engine` green (D33's lesson: budget
  the stage *before* wiring it into the worklet; a stage that cannot fit
  the quantum budget cannot ship, per `feedback_audio_thread_budget`).
- Default off (`config.stem_content_routing` or similar, manifest +
  CLI plumbing per existing patterns). Static behavior bit-identical
  when off — regression anchor test.

## Tests

- Archaeology writeup committed as `docs/plans/mixing/phase11_report.md`
  §1 regardless of outcome.
- Delete outcome: full suites green after removal (baseline 1107/31,
  count will drop with removed tests — report it), no dangling imports
  across all five packages.
- Revive outcome: off = bit-identical; on = deterministic; synthetic
  case (click train + reverb tail stem) shows transients' surround send
  attenuated vs sustain by a stated dB figure; measurement-kit energy
  tables with the feature on; bench green.

## Out of scope

- Genre/ML classification, per-section structure detection beyond rung 2.
- Any change to separation, stem naming, or the analyzer *models*.
- Making it default-on (that needs its own listening campaign later).

## Done when

- Gate answered in writing; roadmap 4.1 updated in the knowledge base
  either way.
- Delete: modules gone, suites green. Revive: rung-1 feature shipped
  default-off with the validation above and an A/B listening note
  (dense rock track: dry kit stays front, tails wrap; sparse track: no
  pumping or spatial wander).
