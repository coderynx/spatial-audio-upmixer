# Primary-stage soft-mask re-projection — evaluation report

Change under test: making the primary instrument split (`BS-Roformer-SW`,
`_deux_inst` → Bass/Drums/Guitar/Piano/Other) sum back to its parent, the way
`reproject_stems` already does for the drumsep stage. Generated per
[the evaluation harness spec](../evaluation_harness.md); metrics come from
`upmixer.eval.metrics` (SDR, fullness, bleedless), reported together
throughout.

**Shipped:** the parent's *remainder* is shared over the five stems
(`share_parent_residual`), not a full re-projection. Full re-projection was
measured and rejected — it costs SDR, fullness **and** bleedless on every
stem. Default alpha `1.0`, leftover vocals output redistributed rather than
held out. Details below.

## Run settings

All separation ran through the production `StemSeparator` path.

| Setting | Value |
| --- | --- |
| Models | `becruily_deux.ckpt` → `BS-Roformer-SW.ckpt` (→ `MDX23C-DrumSep-aufr33-jarredou.ckpt` for the composition check) |
| sample_rate | 44100 (model native) |
| batch_size | 1 |
| segment_size / overlap / chunk_duration_s | backend defaults (unset) |
| tta / pitch_shift | off |
| Re-mask STFT | n_fft 2048, hop 512, Hann — `BS-Roformer-SW.yaml` `model.stft_n_fft`/`stft_hop_length` |
| Host | M3 Pro, 18 GiB, MPS, no parallel separations |

The model's `audio.hop_length: 441` is marked "don't work (use in model)"
upstream; the STFT the model actually runs is `model.stft_*` — 2048/512,
identical to the drumsep config, so both stages share one constant pair in
`remask.py`.

## Corpora

1. **Cross-track ground truth** (primary evidence). Five instrument stems,
   each taken from a *different* delivered project, level-matched
   (Bass −20, Drums −18, Guitar −22, Piano −26, Other −24 dBFS RMS) and
   summed into a 30 s mixture. Truth = those five stems. A second item adds a
   real vocal stem at −45 dBFS to test the leftover question.
   Every truth stem is itself a model output, so absolute scores are not
   comparable to a leaderboard — but the *mixture* is one no model has seen,
   so the relative A/B is fair.
2. **Real chain, two 30 s excerpts** from local source material — a loud
   **dense studio** master and a **live keys-heavy** recording from a
   192 kHz source. Used for stage nulls and per-stem character, where no
   ground truth exists.
   The harness's own `synthetic_corpus` path
   (`uv run pytest packages/core/tests -m perf -k eval -s`) was run and is
   green, but it drives `separate_for_eval` → `StemSeparator` directly, which
   bypasses `execute_plan` and therefore never runs this pass. Its metric
   functions are what score the corpora above.
3. **Rejected: re-separation corpus.** Feeding the model the sum of one
   track's own delivered stems and scoring against those stems was tried
   first and thrown out: the model reproduces its own output nearly exactly
   (raw SDR 12–19 dB), so the setup scores *any* modification as a loss by
   construction. Numbers from it are not used here.

## Result 1 — full re-projection is a regression

Cross-track corpus, means over the five stems, instrumental item:

| Variant | SDR | fullness | bleedless | stage null |
| --- | --- | --- | --- | --- |
| raw (no pass) | **12.92** | 0.8063 | **0.9378** | −33.9 dB |
| re-project α=0.5 | 5.65 | 0.6633 | 0.8228 | −146.5 dB |
| re-project α=0.7 | 6.73 | 0.6995 | 0.8750 | −146.1 dB |
| re-project α=1.0 | 7.57 | 0.7193 | 0.9015 | −145.9 dB |
| re-project α=1.5 | 8.08 | 0.7269 | 0.9072 | −145.7 dB |
| re-project α=2.0 | 8.21 | **0.7283** | 0.9040 | −145.7 dB |

Per stem at α=1.0: Bass SDR 16.41→7.15 (fullness 0.856→0.672), Drums
17.16→8.86, Guitar 11.27→7.48, Piano 9.88→6.52, Other 9.86→7.86. Only Drums
bleedless improved (0.9727→0.9768).

Every alpha is worse on all three metrics, monotonically better as alpha
rises — i.e. the closer the mask gets to the model's own hard partition, the
less damage it does, and it never catches up. The mechanism is the point of
the pass and also its cost: a re-projected stem is a magnitude-ratio
approximation carrying the *parent's* phase. Where the model's own waveform
output is already good, that is a downgrade, and it moves foreign content in.

The real chain shows the same thing physically: at α=1.0 the re-projected
Bass gained **+24 dB** of 6–12 kHz energy on the dense studio excerpt (+33 dB at
α=0.5) — cymbals and air redistributed into a stem whose own HF is near
zero, because a ratio mask hands a near-silent child a share of every bin it
sits in. This is why the pass worked for drumsep (six broadband kit pieces
that genuinely collide) and does not work here (five spectrally disjoint
instruments).

## Result 2 — sharing only the remainder gets the null for free

`share_parent_residual`: keep the model's output, split
`parent − Σ children` by the same soft masks, add it back. Sum-preservation is
identical; the model's waveforms survive.

| Variant | SDR | fullness | bleedless | stage null |
| --- | --- | --- | --- | --- |
| raw (no pass) | 12.92 | 0.8063 | **0.9378** | −33.9 dB |
| residual α=0.5 | **12.93** | **0.8102** | 0.9360 | −142.9 dB |
| residual α=0.7 | **12.93** | 0.8100 | 0.9360 | −142.9 dB |
| residual α=1.0 | **12.93** | 0.8099 | 0.9360 | −142.9 dB |
| residual α=1.5 | **12.93** | 0.8098 | 0.9359 | −142.9 dB |
| residual α=2.0 | **12.93** | 0.8098 | 0.9358 | −143.0 dB |

Per stem at α=1.0 (raw → shared): Bass SDR 16.41→16.43, fullness
0.8557→0.8570, bleedless 0.9560→0.9554; Drums 17.16→17.16 / 0.9142→0.9155 /
0.9727→0.9722; Guitar 11.27→11.29 / 0.8016→0.8065 / 0.9267→0.9241; Piano
9.88→9.90 / 0.7303→0.7367 / 0.9136→0.9097; Other 9.86→9.87 / 0.7294→0.7336 /
0.9202→0.9186.

**Honest reading: bleedless goes down on four of five stems** — by 0.0006 to
0.0039, mean 0.9378 → 0.9360. That is the cost, and it is real: the shared
remainder is unassigned content, so some of what each stem gains is foreign.
It buys fullness +0.004 mean (up on all five stems), SDR +0.01 dB (noise),
and exact sum-preservation. If the −0.002 mean bleedless is judged more
important than the sum-preservation, this pass should be turned off — that is
a defensible call, and `--no-stem-primary-remask` makes it.

What it is *not* defensible as: an audible improvement. The recovered
remainder sits 34 dB below the parent (60 dB on the live excerpt); nobody
will hear it restored. The case for the pass is structural — the delivered
stem set reconstructs its input exactly, so anything downstream that sums
stems is no longer silently short — not sonic.

## Result 3 — alpha, chosen from the chain, not the corpus

Corpus metrics are flat across alpha (4th decimal). The real chain is not:

| α | dense studio: energy added per stem | Piano (near-dead stem) |
| --- | --- | --- |
| 0.5 | −42 to −29 dB | **+11 dB energy, +11.4 dB in 6–12 kHz** |
| 0.7 | −41 to −29 dB | +4 dB |
| 1.0 | −41 to −29 dB | −29 dB (proportional) |
| 1.5 | −40 to −30 dB | −55 dB |
| 2.0 | −40 to −31 dB | −96 dB (effectively excluded) |

On the live excerpt α=0.5 adds −9 to −16 dB per stem where α=1.0 adds −47 to
−78 dB. Low alpha flattens magnitude differences, so a stem that is
essentially silent (this project's Piano) becomes the garbage collector for
the remainder. High alpha excludes quiet stems entirely.

**Default α = 1.0** — plain ratio mask, matches the drum stage, dead-stem safe,
no metric cost. (`stem_primary_remask_alpha` and `stem_drum_remask_alpha` were
later deleted — the exponent is fixed at 1.0 on both stages, see
[drum_remask.md](drum_remask.md) §Result 5.)

## Result 4 — the leftover-vocals decision

The primary model also emits a vocals output that the plan discards
(`stem_plan.py` excludes it from `output_stems`). Sharing the remainder
redistributes it into the instruments; holding it out keeps it away but leaves
the five stems short.

Measured on the vocal-bearing corpus item (model vocals-out at −67.5 dB
relative to the mixture):

| Variant | SDR | fullness | bleedless | stage null |
| --- | --- | --- | --- | --- |
| share all (shipped) | 12.87 | 0.8103 | 0.9323 | −142.8 dB |
| hold vocals out | 12.87 | 0.8103 | 0.9323 | **−67.5 dB** |

Identical to four decimals on every metric, at every alpha, on both corpus
items; the only difference is the null, which the hold-out variant caps at
exactly the residue level. **Shipped: share it all.** The premise that this
residue is worth −36 dB no longer holds — measured at −67 to −121 dB relative
across four excerpts, so redistributing it costs nothing measurable. Vocal
correlation of the added content on the real chain is ≤0.054 against deux's
Vocals stem.

(Under a *full* re-projection the choice would have mattered: holding the
residue out cost 90 dB of null at α=0.5 and 0 dB at α=2.0, because a
near-silent sixth mask still claims a large share at low alpha.)

## Result 5 — stage nulls, end to end

Whole instrument tree (six kit pieces + Bass/Guitar/Piano/Other) summed and
nulled against `_deux_inst`, through the real `execute_plan`:

| Excerpt | both passes off | drums only (previous default) | shipped |
| --- | --- | --- | --- |
| dense studio | −33.3 dB | −35.1 dB | **−142.5 dB** |
| live keys-heavy | −60.9 dB | −60.9 dB | **−144.2 dB** |

Floor is STFT reconstruction error. Ordering holds as required: the primary
stage shares its remainder first and rewrites the Drums file kept on disk, so
drumsep separates *and* re-projects against the shared Drums, and the two
passes compose (`test_kit_remask_composes_with_the_shared_primary_stage`,
`test_remask_stage_rewrites_children_kept_on_disk`).

Note the starting point differs from the 2026-08-15 null audit, which recorded
−28 to −31 dB for this stage. On current `main` (after the stem-level fix in
`docs/contracts/stem_level_domain.md`) the same measurement reads −33 to
−61 dB: the loss was partly the level cascade, not the model. The residual is
0.03 % of parent energy on the dense studio excerpt and 0.0001 % on the live
one.

## Result 6 — character check on Bass and Guitar

The mechanism substitutes the parent's phase for the added content, which is
where transient damage would show. Measured on both excerpts at α=1.0
(shipped variant):

| Excerpt | stem | added level | Δ 6–12 kHz | Δ crest factor |
| --- | --- | --- | --- | --- |
| dense studio | Bass | −40.8 dB | +0.53 dB | −0.01 dB |
| dense studio | Guitar | −31.9 dB | +0.23 dB | −0.03 dB |
| live | Bass | −54.2 dB | +0.00 dB | +0.00 dB |
| live | Guitar | −56.6 dB | −0.00 dB | −0.00 dB |

Compare the rejected full re-projection: Bass +24 dB in 6–12 kHz and +1.33 dB
crest on the same excerpt.

**Not verified by listening.** I cannot hear the output; the numbers above are
proxies (band energy, crest factor, correlation with the vocal stem) and they
are consistent with "no audible change", not proof of it. The rendered
excerpts are reproducible from the scripts noted below if a listening pass is
wanted before this ships to users.

## Cost

Pure DSP, measured in isolation (medians of 3, same host):

| Pass | 30 s audio | 240 s audio |
| --- | --- | --- |
| Primary, 5 children (added) | 0.73 s | 5.91 s |
| Drumsep, 6 children (existing) | 0.90 s | 7.84 s |

≈1.5 s per minute of audio added, about 75 % of the existing drum pass. End
to end on the 30 s excerpts the measured wall clock moved 58.3 → 67.0 s
(dense studio) and 63.4 → 68.7 s (live), but identical-work runs varied by 8 s
on this host, so the isolated figure is the reliable one. Memory cost is one
STFT block (512 k samples) per channel at a time; no change to the separation
peak.

## Validation

- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` —
  1082 passed, 0 failed (1075 before this change; +7 tests here).
  The 846 figure in older notes is stale.
- `packages/core/tests/test_remask.py` covers sum-preservation for both
  variants, model-output preservation, alpha validation, silent-bin equal
  sharing, held-out children, on-disk write-back, stage dispatch, and the
  drumsep composition property.
- Cache identity: `remask_cache_component` now serializes each pass only for
  plans that run its own model, so a Vocals-only plan keeps its old key and a
  primary-running plan does not serve pre-change stems
  (`test_stem_cache_identity_changes_for_remask`).
- Level domain: the pass adds an STFT-derived signal and never normalizes;
  `docs/contracts/stem_level_domain.md` is unaffected. Web project stems under
  `apps/api/data/project-stems` are not cache-invalidated automatically and
  must be regenerated to pick this up.

## Follow-up

**Resolved by [drum_remask.md](drum_remask.md).** The drumsep stage then still
used full re-projection, chosen before this comparison existed; the same swap
was measured against a purpose-built kit-piece corpus and shipped. Two claims
made above did not survive it: the drumsep exemption ("six broadband kit pieces
that genuinely collide") is wrong — re-projection costs Kick 3.5 dB and Toms
4.8 dB of SDR there for no cymbal gain — and sharing the remainder does *not*
recover the 6–12 kHz collision loss, because that loss is a misassignment
between pieces rather than discarded energy.
