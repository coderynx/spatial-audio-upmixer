# Phase 2 report — Limiter channel-linking policy

Plan: `docs/plans/mastering/phase2_limiter_linking.md`.
Date: 2026-08-19. Suite: **1175 → 1176 passed / 44 deselected** (one new test).

## What shipped

- `dsp-core/src/mastering/limiter.rs` — `lookahead_limit` takes an
  `lfe: Option<usize>` and runs one gain computer (`gain_curve`) over two
  envelope feeds: the mains share a curve, LFE gets its own. Both are capped
  at the same ceiling with the same look-ahead, release and edge dilation.
  `LimiterInfo` grows `lfe_max_gr_db`.
- `dsp-core/src/stream/master.rs` — `StreamingLimiter` mirrors it: one
  `curve()` helper, two release states, one FIR history per channel as
  before. It now returns `LimiterInfo` instead of a bare peak-GR float, so the
  preview has the same telemetry the export does (phase 3's meters read it).
  `required_lookahead()` is unchanged — the emit horizon does not move (P1).
- PyO3 `lookahead_limit` takes `lfe_index` and returns
  `(bed, max_gr_db, duty, lfe_max_gr_db)`; the wasm `dsp_master_bed` passes
  the `lfe_index` it already parses.
- `mastering/limiter.py` — `process(channels, lfe_key="LFE")`, matching
  `BusCompressor.process`'s signature, and a new `gr_lfe_peak_db` attribute.
  `MasteringResult` grows `limiter_gr_lfe_peak_db`.
- `docs/standards/loudness_dsp_bs1770.md` § "LFE and true-peak" carries the
  policy, the phase 0 evidence and the FLUX/Pulsar/McDSP practice citation;
  parity contract §1 and ledger D41 carry the commutation note.

No new tunable. `limiter_link` was **not** shipped: phase 0's audit 2 says the
duck is entirely the LFE's doing (mains at 0.25 peak never limit on their own,
and every dB of reduction they took traced to the LFE), so unlinking LFE closes
the whole measured problem. A partial-link scalar would be a parameter with no
measurement behind it — the explicit-control contract says no.

## Validation

```
cd packages/dsp && cargo test                      # 200 passed, 0 failed
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
                                                   # 1176 passed / 44 deselected
cd apps/web && npm test && npm run build           # 262 passed, build ok
uv run pytest packages/core/tests/test_master_measurement.py -m perf -s
```

Golden and equivalence coverage:

- `unit_mastering.rs::an_lfe_only_peak_leaves_the_mains_alone` — mains
  bit-identical (`assert_eq` on the raw samples), LFE GR > 6 dB, LFE true peak
  under the ceiling.
- `unit_mastering.rs::a_quiet_lfe_leaves_the_mains_curve_untouched` — with the
  LFE well under the ceiling, the split limiter's mains output is
  bit-identical to the fully-linked one. This is the "mains-only fixture is
  unchanged" requirement, stated as an equivalence rather than a stored vector.
- `test_limiter.py::test_limiter_does_not_duck_the_mains_with_the_lfe` — the
  same property through the public Python API, which is also what pins the
  name→index mapping.
- Every existing limiter test passes `lfe=None`, which is the
  `render_binaural_delivery` / transaural case: no LFE index, identical output.
  (Both of those paths actually use `soft_limit`, not this limiter, so they
  were never exposed in the first place.)
- `stream_equivalence.rs::streaming_mastering_matches_the_offline_chain` runs
  the 4-channel bed with `lfe = Some(3)` on both sides and still matches to
  1e-6, so the second curve is streamed identically.

The `limiter_apply` golden fixture was regenerated — the stage's output
deliberately changed. Old vs new on that fixture (a hot 4-channel bed):

| channel | level change vs the old fixture |
|---|---|
| FL / FR / C | +0.00 to **+3.51** dB, mean +0.42 dB |
| LFE | +0.00 to **+1.96** dB, mean +0.04 dB |

Both directions of the old coupling show up: the mains recover up to 3.5 dB
they were losing to the LFE, and the LFE recovers up to 2.0 dB it was losing
to the mains. Nothing gets quieter anywhere, which is what a pure decoupling
should look like.

## Audit 2 re-run — the duck is gone

Same fixture as phase 0 (mains at 0.25 peak so they never limit on their own;
sparse 40 Hz swells at the level a `cinema` send produces), with the LFE's own
curve now reported next to the mains':

| LFE peak | GR peak dB | GR duty | worst mains gain dB | mains RMS change dB | LFE GR peak dB | LFE dBTP |
|---|---|---|---|---|---|---|
| none | 0.00 | 0.0% | +0.00 | +0.00 | 0.00 | — |
| −3 dBFS | 0.00 | 0.0% | +0.00 | +0.00 | 0.00 | −3.00 |
| +0 dBFS | 0.00 | 0.0% | +0.00 | +0.00 | 1.10 | −1.10 |
| +3 dBFS | 0.00 | 0.0% | +0.00 | +0.00 | 4.10 | −1.10 |
| +6 dBFS | 0.00 | 0.0% | +0.00 | +0.00 | 7.10 | −1.10 |

Phase 0 read −1.10 / −4.10 / −7.10 dB in the "worst mains gain" column for
those last three rows, at 7.8% / 16.4% / 20.9% duty. Every one is now +0.00.
The reduction did not move to the mains by another route and it did not
disappear — it went to the LFE curve, where the GR column now shows exactly the
1.10 / 4.10 / 7.10 dB the LFE itself needs, and the LFE lands at −1.10 dBTP
(the ceiling less the limiter's 0.1 dB internal safety margin) in all three
cases. Same true-peak guarantee, no coupling.

## Compliance baseline — unchanged

The full compliance kit was run on HEAD (`git stash`, wheel rebuilt, kit run,
restored) and on this branch. **Every row of all four tables is identical**,
including `lim GR pk` and `lim GR duty`. That is the expected result and worth
stating: the kit's beds carry an LFE built as a −10 dB lowpassed sum, which
never drives the shared curve, so decoupling it changes nothing there. The
audit-2 fixture is the one that exercises this phase, and it is where the
whole change shows.

Note for anyone diffing against `phase0_report.md`: its 7.1.4 rows differ from
the current ones (e.g. dense/−18 dBTP −10.30 → −9.98, dynamic/−18 LRA 3.4 →
4.0). That is **phase 1**, which made 7.1.4 normalization fold-referenced, not
this phase. The HEAD baseline above is the correct comparison point.

## Inherited ceiling overshoot — not closed, and why

Phase 0 handed phase 2 the +0.0636 dB ceiling overshoot found at the hot
target. It is **unchanged** by this phase (still +0.0636 dB, still asserted
under the limiter's 0.1 dB safety margin by `test_compliance_baseline`), and
it is not closed here. The reason is a direct conflict with this phase's own
constraints:

- **Widening the dilation** (`FIR_MARGIN_SAMPLES`) changes the gain curve on
  every limited sample, which breaks the "mains-only material is unchanged"
  requirement — and it does not address the mechanism anyway: the residual
  comes from the gain curve's *slope* inside the detector's support, not from
  an isolated dip a wider minimum filter would swallow. Same objection to
  simply raising `_SAFETY_MARGIN_DB`, which additionally spends 0.1 dB of
  loudness on every render to fix a case that occurs under 38% limiter duty.
- **Iterating the ceiling check once** (re-detect the limited signal, apply
  the residual) is the correct fix and is exact offline. In the streaming
  limiter it is not: the second pass needs look-ahead over *already-gained*
  samples, and gain is only applied to the emit region, so the queue would
  have to grow. Growing it moves the engine's emit horizon, which parity
  contract P1 forbids and this phase's plan calls out explicitly.

So the honest options are a longer emit horizon (a parity-contract change with
its own bench budget) or living with 0.064 dB. Recommendation: leave it, and
fold it into phase 3, where the meters make deep-GR operation visible to the
user in the first place — the overshoot only appears at 38% duty and 4.7 dB
peak GR, which is a mix the user should be warned off, not a number to shave.
Nothing in this phase makes it worse; the assertion pins that.

One row did flip PASS → FAIL in the hot-target table relative to
`phase0_report.md`: 7.1.4 dynamic, −1.08 → −0.94 dBTP. That is phase 1's
fold-referenced normalization landing that render at the same overshoot the
stereo and 5.1 rows already had — it is present on HEAD, before this phase's
first line of code.

## Audio-thread budget

`npm run bench:engine` after `npm run build:wasm`, three runs each side,
medians (the `worst` column is host noise — it spikes on cases this phase does
not touch):

| case | HEAD mean | this branch mean | HEAD p99 | this branch p99 |
|---|---|---|---|---|
| native 7.1.4 + limiter | 0.736 ms (0.28x) | 0.754 ms (0.28x) | 2.172 ms (0.81x) | 2.271 ms (0.85x) |
| binaural (order-3 decode) | 0.877 ms (0.33x) | 0.878 ms (0.33x) | 2.443 ms (0.92x) | 2.473 ms (0.93x) |

+0.018 ms mean on the limiter case — the second envelope pass, over one
channel rather than twelve, so it costs about what one extra channel costs.
Budget is mean ≤ 0.4x, p99 ≤ 1x; both hold with margin.

The two `measuring (…)` cases report FAIL on **both** sides, identically. That
is a pre-existing condition, unrelated to this phase.

## A/B listening note — owed, not done

**No listening pass was run: this was an agent session with no audio output.**
The plan asks for one on the LFE-heavy programme from phase 0, so it is
recorded here as outstanding rather than quietly skipped.

What the measurement says the listener should expect, so the pass has
something to confirm or contradict: on the audit-2 fixture at +3 dBFS LFE the
mains previously dipped 4.1 dB in time with each 40 Hz swell for 16% of the
programme, recovering over the 50 ms release — a level modulation that deep,
that fast and that strongly correlated with the bass is the textbook audible
pump, and it is what the old behaviour did on every `cinema`-profile render.
After this phase the mains are bit-identical to their input on that fixture,
so the only thing left to hear across a swell is the LFE losing its own
overshoot. The A/B should therefore be *obvious*, not marginal; if a listener
reports hearing no difference on LFE-heavy material, that is evidence the
fixture is unrepresentative, not that the change is inaudible.

## Notes

- Knowledge base (`~/Projects/upmixer-knowledge/techniques/
  mastering_restoration.md`) was consulted. It covers the look-ahead limiter
  at concept level ("delay-line look-ahead + smoothed gain + existing 4× TP
  detector") and carries no channel-linking or LFE guidance; nothing in it
  conflicts with the above.
- The regenerated `limiter_apply` fixture is a regression pin, not an
  independent reference (`packages/dsp/AGENTS.md` § "Fixtures") — the Python
  stage it was originally dumped from is now the Rust one. Its value is the
  before/after table above plus catching future drift.
