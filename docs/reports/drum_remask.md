# Drumsep soft-mask re-projection — evaluation report

Change under test: which soft-mask variant the drumsep stage
(`MDX23C-DrumSep-aufr33-jarredou`, parent `Drums` → Kick/Snare/Toms/Hi-Hat/
Ride/Crash) should use, and at what exponent. Generated per
[the evaluation harness spec](../evaluation_harness.md); metrics come from
`upmixer.eval.metrics` (SDR, fullness, bleedless), reported together
throughout.

**Shipped:** the drumsep stage now shares the parent's *remainder*
(`share_parent_residual`), the same variant the primary stage already used.
Full re-projection (`reproject_stems`), the previous default, was measured and
rejected — it costs SDR on every shell piece at every exponent while giving the
cymbals no SDR and no dullness recovery. The `stem_drum_remask_alpha` and
`stem_primary_remask_alpha` knobs are **deleted**: nothing measurable moves
across 0.5–2.0 for the shipped
variant, on either stage. `stem_drum_remask` / `stem_primary_remask` remain as
bare on/off.

The exemption that put full re-projection here — "six broadband kit pieces that
genuinely collide", asserted in `primary_remask.md` — does not hold. Kick and
Toms are as spectrally disjoint from the cymbals as Bass is from Drums, and
re-projection damages them the same way.

## Run settings

All separation ran through the production `StemSeparator` path.

| Setting | Value |
| --- | --- |
| Model | `MDX23C-DrumSep-aufr33-jarredou.ckpt` |
| sample_rate | 44100 (model native) |
| batch_size | 1 |
| segment_size / overlap / chunk_duration_s | backend defaults (unset) |
| tta / pitch_shift | off |
| Re-mask STFT | n_fft 2048, hop 512, Hann (`remask.py` constants) |
| Host | M3 Pro, 18 GiB, MPS, no parallel separations |

Separation ran **once** per item; the eleven conditions (raw + two variants ×
five exponents) are pure DSP applied to the same saved model output, so nothing
but the pass differs between rows.

## Corpora

1. **Kit-piece ground truth** (primary evidence). Kit pieces have no ground
   truth in the existing corpus, which is why `primary_remask.md` deferred
   this. Built one: Logic's Drum Kit Designer consolidated stereo samples
   (`/Library/Application Support/Logic/EXS Factory Samples/Drum Kit Designer
   Consolidated/Drum Kit Designer/Stereo`, real acoustic recordings of Bluebird
   / SoCal / Heavy kits, 44.1 kHz) sliced into one-shots on onsets and
   sequenced into grooves, one track per piece. The sum of the six tracks is
   the drumsep input; the six tracks are the references. Four 24 s items:

   | Item | Category | What it tests |
   | --- | --- | --- |
   | `dense_cymbals` (Heavy, 148 bpm) | dense | 16th hats + 8th ride + crashes — the original dullness defect |
   | `sparse_no_ride_no_toms` (SoCal, 96 bpm) | sparse | ride, toms, crash never played |
   | `sparse_late_ride` (Bluebird, 110 bpm) | sparse | ride silent for the first 18 s, crash never played |
   | `default_rock` (SoCal, 120 bpm) | default | every piece present at plausible levels |

   Not shipped with the repo (Apple factory content, locally licensed); the
   recipe above reproduces it.

2. **Real chain, three 30 s Drums excerpts** from local delivered projects —
   a **dense studio** master, a **pop/funk studio** master whose ride is
   essentially unplayed, and a **live cymbal-heavy** recording. Delivered
   `Drums` stems, resampled 48 → 44.1 kHz, re-separated with drumsep. No
   ground truth; used for the proxies that found the original bug
   (per-piece HF/LF ratio vs sibling energy, dull-frame %, partition hardness)
   and for the near-silent-piece behaviour the synthetic corpus cannot test.

**What the synthetic corpus cannot test.** Its absent pieces are *exactly*
zero, so their masks are exactly zero and no exponent can hand them a share.
Real drumsep output for an unplayed piece is never zero (the pop/funk
excerpt's Ride sits at −63.9 dBFS against a −21.2 dBFS parent), and a low
exponent does hand *that* a share. The corpus therefore understates the
low-alpha risk by
construction; §Result 3 reads the two together, and they agree in sign and in
alpha-dependence.

## Result 1 — full re-projection is a regression here too

Kit-piece corpus, means over played pieces, all four items pooled:

| Variant | SDR | fullness | bleedless | stage null |
| --- | --- | --- | --- | --- |
| raw (no pass) | 11.78 | 0.7051 | **0.8754** | −31.1 dB |
| re-project α=0.5 | 7.38 | 0.6176 | 0.7177 | −146.4 dB |
| re-project α=0.7 | 9.36 | 0.6740 | 0.8132 | −145.9 dB |
| re-project α=1.0 | 10.33 | 0.6964 | 0.8728 | −145.6 dB |
| re-project α=1.5 | 10.46 | 0.6954 | 0.8849 | −145.4 dB |
| re-project α=2.0 | 10.36 | 0.6910 | 0.8791 | −145.4 dB |
| **residual α=1.0 (shipped)** | **11.81** | **0.7159** | 0.8704 | −142.1 dB |

Per-piece SDR, mean over the items where the piece is played:

| Variant | Kick | Snare | Toms | Hi-Hat | Ride | Crash |
| --- | --- | --- | --- | --- | --- | --- |
| raw | 23.59 | 13.03 | 14.38 | 7.48 | 2.44 | 4.41 |
| re-project α=0.5 | 12.35 | 9.67 | 6.61 | 6.13 | 2.29 | 4.11 |
| re-project α=0.7 | 17.10 | 12.07 | 8.36 | 7.22 | 2.52 | 4.46 |
| re-project α=1.0 | 20.12 | 12.81 | 9.56 | 7.49 | 2.48 | 4.41 |
| re-project α=1.5 | 20.80 | 12.77 | 10.17 | 7.32 | 2.26 | 4.13 |
| re-project α=2.0 | 20.85 | 12.62 | 10.24 | 7.13 | 2.06 | 3.92 |
| residual α=1.0 | 23.70 | 13.03 | 14.37 | 7.50 | 2.48 | 4.40 |

This is the whole decision. At the shipped α=1.0 re-projection costs **Kick
−3.5 dB and Toms −4.8 dB of SDR**, and hands the three cymbals **+0.01, +0.04,
0.00 dB** — nothing, on exactly the pieces the pass exists to fix. Worst single
item: `default_rock` Toms 18.34 → 10.44, Kick 23.73 → 17.40; `sparse_late_ride`
Kick 25.89 → 21.06, Toms 16.80 → 12.48.

The mechanism is the one `primary_remask.md` named: a re-projected stem is a
magnitude-ratio approximation carrying the *parent's* phase, so wherever the
model's own waveform was already good it is a downgrade. Kick and Toms are
where drumsep is most accurate (SDR 23.6 / 14.4 raw), so they lose the most.
Cymbals are where it is worst (Ride 2.44, Crash 4.41) and a ratio mask cannot
tell them apart any better than the model did.

Fullness and bleedless per piece at α=1.0 tell the same story — re-projection
takes fullness off Kick (0.8417 → 0.8180), Snare (0.7018 → 0.6801) and Toms
(0.7189 → 0.6880), gives some back on Ride (0.5231 → 0.5407) and Crash
(0.5480 → 0.5749), and loses bleedless on Toms (0.8544 → 0.8345) and Crash
(0.8112 → 0.7966).

## Result 2 — sharing the remainder is free, and slightly better

`share_parent_residual`: keep the model's output, split `parent − Σ children`
by the same soft masks, add it back.

| Metric, α=1.0 | Kick | Snare | Toms | Hi-Hat | Ride | Crash |
| --- | --- | --- | --- | --- | --- | --- |
| SDR raw → shared | 23.59→23.70 | 13.03→13.03 | 14.38→14.37 | 7.48→7.50 | 2.44→2.48 | 4.41→4.40 |
| fullness | 0.8417→0.8477 | 0.7018→0.7079 | 0.7189→0.7271 | 0.7762→0.7926 | 0.5231→0.5376 | 0.5480→0.5652 |
| bleedless | 0.9539→0.9507 | 0.8993→0.8960 | 0.8544→0.8484 | 0.9403→0.9363 | 0.7164→0.7097 | 0.8112→0.8009 |

Fullness up on all six pieces (+0.006 to +0.017, mean +0.011); bleedless down
on all six (−0.003 to −0.010, mean −0.005); SDR flat (±0.04 dB, noise). Stage
null −31.1 → −142.1 dB (floor is STFT reconstruction error).

Per-item stage nulls, raw → shared: `dense_cymbals` −27.6 → −142.8,
`sparse_no_ride_no_toms` −36.1 → −141.5, `sparse_late_ride` −29.8 → −142.1,
`default_rock` −30.8 → −142.1. On the real excerpts: dense studio −36.0,
pop/funk −29.3, live −25.4 → −141.7/−142.0/−141.8.

## Result 3 — H1: foreign energy into near-silent pieces

**Confirmed for α < 1.0, not at α ≥ 1.0, for re-projection; not at any α for
the residual.**

Corpus, pieces absent from the reference — injected level (dB RMS), so lower
is better:

| Item / piece | raw | rp α=0.5 | rp α=0.7 | rp α=1.0 | rp α=1.5 | rs α=0.5 | rs α=1.0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sparse_no_ride…`/Toms | −64.3 | **−51.1** | −58.7 | −64.5 | −66.1 | −64.0 | −64.1 |
| `sparse_no_ride…`/Ride | −72.2 | **−52.8** | −61.2 | −72.4 | −83.9 | −71.0 | −71.9 |
| `sparse_no_ride…`/Crash | −76.9 | **−55.5** | −64.7 | −77.4 | −92.4 | −75.9 | −76.8 |
| `sparse_late_ride`/Crash | −50.3 | −46.1 | −48.0 | −49.8 | −51.4 | −49.6 | −49.6 |

Real excerpts, the near-silent pieces on each — same measurement, no ground
truth needed:

| Excerpt / piece | raw | rp α=0.5 | rp α=0.7 | rp α=1.0 | rp α=2.0 | rs α=0.5 | rs α=1.0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dense studio / Toms | −57.6 | **−46.7** | −53.8 | −57.8 | −58.7 | −57.3 | −57.3 |
| dense studio / Ride | −59.3 | **−49.9** | −56.1 | −58.8 | −58.6 | −58.5 | −58.5 |
| pop/funk / Ride | −63.9 | **−48.6** | −55.8 | −63.9 | −67.1 | −62.3 | −63.4 |
| pop/funk / Toms | −48.7 | −45.9 | −48.0 | −48.7 | −48.4 | −48.4 | −48.3 |

So the primary-stage failure mode reproduces on drums: at α=0.5 a ratio mask
hands a near-silent piece up to **+15.3 dB** (the pop/funk excerpt's
near-silent Ride) of
content that is not its own. It just needs a low exponent to show, because
drumsep's partition is far harder than the primary model's — at α=1.0 the mask
of a near-silent piece is already small enough that re-projection tracks the
raw level to within 0.5 dB.

The residual variant never moves a near-silent piece by more than **1.6 dB**
(pop/funk Ride at α=0.5), and by ≤0.5 dB at α=1.0.

Corpus and proxies agree in direction and in alpha-dependence. They disagree in
magnitude — the corpus's exactly-zero references cap the injection at +13 dB
where the real chain reaches +15 dB with far less headroom above the noise —
and the real-chain number is the one to trust, for the reason given under
Corpora.

## Result 4 — H2: the 6–12 kHz remainder, measured directly

**Confirmed that the remainder is HF-concentrated; refuted that recovering it
fixes the dullness.**

Band profile of `parent − Σ children` relative to the parent in the same band
(real excerpts, drumsep raw output):

| Band | dense studio | pop/funk | live |
| --- | --- | --- | --- |
| 0–200 Hz | −45.8 | −38.8 | −38.4 |
| 200–800 Hz | −39.1 | −31.9 | −27.2 |
| 800–2000 Hz | −31.6 | −31.8 | −23.6 |
| 2–6 kHz | −25.6 | −27.7 | −17.5 |
| **6–12 kHz** | **−25.5** | **−26.4** | **−11.5** |
| 12–22 kHz | −24.2 | −24.2 | −20.8 |

The un-claimed energy is 14–20 dB more prominent above 2 kHz than below it, and
the live excerpt's −11.5 dB in 6–12 kHz matches the −12.9 / −13.7 dB the
2026-08-15
dullness diagnosis recorded. The premise holds: the energy the pass was built
to recover *is* the parent-minus-children remainder, and recovering it never
required replacing the model's kit-piece waveforms.

What the recovery is worth, per piece, in 6–12 kHz (residual α=1.0):

| Excerpt | Kick | Snare | Toms | Hi-Hat | Ride | Crash |
| --- | --- | --- | --- | --- | --- | --- |
| dense studio | +3.86 | −0.05 | +6.27 | +0.05 | +0.89 | +0.37 |
| pop/funk | +1.67 | +0.12 | +1.68 | +0.04 | +0.34 | +0.07 |
| live | +11.82 | +0.16 | +12.58 | +0.43 | +0.61 | +0.28 |

The cymbals — the dull ones — get **+0.04 to +0.89 dB**. The large numbers are
Kick and Toms, which own almost no 6–12 kHz to begin with (live-excerpt Kick
−76.2 → −64.4 dB, still 14 dB under that excerpt's Hi-Hat) and whose
broadband level moves +0.04 dB. That is the residual variant's own bleed
cost, in the same direction as the rejected re-projection's Bass +24 dB but
two orders smaller and inaudible.

**So the pass does not fix the dullness.** The dullness is a *misassignment*
between pieces, not lost energy: the remainder is only −25 dB relative, and the
cymbals' share of it is under 1 dB. Sum-preservation is what this pass buys.

## Result 5 — H3: alpha, and the deletion of both knobs

**Confirmed.** For the shipped variant nothing measurable moves across
0.5 → 2.0:

| α | SDR | fullness | bleedless | Kick SDR | worst near-silent piece |
| --- | --- | --- | --- | --- | --- |
| 0.5 | 11.82 | 0.7164 | 0.8665 | 23.71 | +1.6 dB (pop/funk Ride) |
| 0.7 | 11.82 | 0.7161 | 0.8691 | 23.71 | +1.0 dB |
| 1.0 | 11.81 | 0.7159 | 0.8704 | 23.70 | +0.5 dB |
| 1.5 | 11.81 | 0.7156 | 0.8709 | 23.70 | +0.2 dB |
| 2.0 | 11.80 | 0.7154 | 0.8709 | 23.69 | +0.1 dB |

That is the third decimal on the corpus metrics and ≤1.6 dB on a piece sitting
40 dB below its parent. The primary stage measured the same way in
`primary_remask.md` (flat to the fourth decimal), and its α=1.0 choice came
from the real chain, not the corpus — a choice the sweep above reproduces and
does not improve on.

`stem_drum_remask_alpha` and `stem_primary_remask_alpha` were therefore
**deleted** from `config.py`, both `manifest/schema.py` tables,
`apps/cli/src/args_stems.py` + `flags.py`, the `apps/api` engine-key
passthrough, and `stem_identity.py`'s cache component. Both passes run the
plain ratio mask (exponent 1.0). The `alpha` argument stays on
`reproject_stems` / `share_parent_residual` themselves — it is what makes the
sweep above reproducible from shipped code, and it is one defaulted parameter,
not a plumbed knob.

## Result 6 — real-track proxies

Partition hardness (energy-weighted mean dominant-piece share per T-F bin) and
effective pieces per bin, 30 s excerpts, drumsep STFT:

| Condition | dense studio | pop/funk | live |
| --- | --- | --- | --- |
| raw | 0.893 / 1.27 | 0.903 / 1.23 | 0.859 / 1.39 |
| re-project α=0.5 | 0.700 / 1.97 | 0.698 / 1.94 | 0.637 / 2.32 |
| re-project α=0.7 | 0.815 / 1.52 | 0.819 / 1.48 | 0.752 / 1.79 |
| re-project α=1.0 | 0.888 / 1.29 | 0.897 / 1.25 | 0.838 / 1.48 |
| residual α=1.0 | 0.890 / 1.28 | 0.900 / 1.24 | 0.843 / 1.46 |

This is the second finding that kills the previous default: **at α=1.0, full
re-projection barely softens the partition it was adopted to soften** (0.903 →
0.897 on the pop/funk excerpt). It only softens meaningfully at α ≤ 0.7 —
precisely where
Result 3 shows it flooding near-silent pieces and Result 1 shows it costing
11 dB of Kick SDR. There is no exponent at which it is a win.

Dull-frame percentage (share of active frames whose 6–12 kHz / 200–2000 Hz
ratio sits ≥6 dB under that piece's median) and its correlation with total
sibling energy:

| Excerpt | condition | Kick | Snare | Toms | Hi-Hat | Ride | Crash |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dense studio | raw | 16% | 4% | 9% | 1% | 10% | 5% |
| dense studio | re-project α=1.0 | 14% | 3% | 9% | 2% | 9% | 6% |
| dense studio | residual α=1.0 | 14% | 4% | 10% | 1% | 8% | 5% |
| pop/funk | raw | 31% | 2% | 23% | 8% | 12% | 23% |
| pop/funk | re-project α=1.0 | 26% | 3% | 24% | 8% | 12% | 29% |
| pop/funk | residual α=1.0 | 29% | 2% | 24% | 8% | 11% | 21% |
| live | raw | 2% | 12% | 9% | 2% | 3% | 11% |
| live | re-project α=1.0 | 7% | 11% | 13% | 3% | 3% | 7% |
| live | residual α=1.0 | 7% | 12% | 14% | 3% | 4% | 8% |

Neither variant moves dullness by more than a few points, in either direction.
The live excerpt's Kick/Toms get *worse* under both (2 → 7%, 9 → 13–14%) —
that is the
+11.8 / +12.6 dB of shared 6–12 kHz from Result 4 landing in pieces with no HF
of their own, showing up as more frames whose HF/LF ratio deviates from their
own median. Absolute level change is +0.07 / +0.04 dB broadband.

These figures do not reproduce the 2026-08-15 diagnosis numbers exactly
(hardness 0.978–0.985, effective pieces 1.10, Snare/Hi-Hat/Crash 32–47% dull in
dense passages). That measurement ran full-length at 48 kHz with a different
STFT and a different dull-frame threshold; this one is 30 s at 44.1 kHz on the
remask STFT. The conclusion each supports is the same — the partition is very
hard and dullness tracks sibling density — so no attempt was made to reconcile
the exact values.

## Honest reading — what got worse

Per piece, shipping the residual variant instead of raw output:

- **bleedless drops on all six pieces**, −0.003 to −0.010 (Crash worst,
  0.8112 → 0.8009; mean 0.8754 → 0.8704). The shared remainder is unassigned
  content, so part of what each piece gains is foreign. That is the cost, and
  it is real.
- **Kick and Toms take the shared HF.** Up to +12.6 dB in 6–12 kHz on the live
  excerpt, on
  a band 14–20 dB below the cymbals' — broadband +0.04 dB, and it costs
  that excerpt's Kick 5 points of dull-frame rate.
- **The pass does not fix the dullness it was built for** (Result 4, Result 6).
  Anyone reading the 2026-08-15 diagnosis should not expect this to have.

Against raw, it buys fullness +0.011 mean (up on all six pieces), SDR +0.03 dB
(noise), and exact sum-preservation. If the −0.005 mean bleedless is judged
more important than sum-preservation, `--no-stem-drum-remask` turns the stage
off; that is a defensible call.

Per piece, shipping the residual variant instead of the **previous default**
(full re-projection at α=1.0) — i.e. what actually changes for users: Kick
+3.6 dB SDR, Toms +4.8 dB, Snare +0.2 dB, Hi-Hat +0.01 dB, Ride 0.0 dB, Crash
−0.01 dB; fullness up on Kick/Snare/Toms/Hi-Hat, down on Ride (0.5407 →
0.5376) and Crash (0.5749 → 0.5652); bleedless down on Kick (0.9585 → 0.9507),
Snare, Hi-Hat and Ride, up on Toms and Crash. **Ride and Crash are marginally
worse than they were.** They are also the two pieces where both variants sit
within noise of raw, and the shells' 3.6–4.8 dB dominates the trade.

**Not verified by listening.** I cannot hear the output. Everything above is
proxies — SDR/fullness/bleedless, band energy, dull-frame rate, partition
hardness — consistent with "shells clearly better, cymbals unchanged", not
proof of it.

## Cost

Pure DSP, six children, medians of 3 on the same host: 30 s audio 0.87 s
(re-project) vs 0.88 s (residual); 240 s audio 7.03 s vs 7.07 s. The variant
switch is free — `share_parent_residual` is `reproject_stems` applied to the
remainder plus one subtraction and one addition.

## Validation

- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` —
  **1083 passed, 27 deselected**, 0 failed (1082 before this change).
- `packages/core/tests/test_remask.py` gains
  `test_kit_pieces_keep_their_model_waveforms` (each piece stays closer to the
  model's own output under the residual than under re-projection) and
  `test_both_stages_share_their_parent_remainder` (stage dispatch); the
  composition test now runs the residual on both stages.
- **Generated audio, end to end.** A 30 s excerpt of a local source track,
  real `execute_plan` with the production models: the six kit pieces null
  against the delivered `Drums` at **−141.6 dB** with the pass on and
  −35.2 dB with it off, and the log reads
  `[stage 3/3] sharing the remainder of parent Drums`.
  Delivered stem levels move ≤0.6 dB (Ride −57.9 → −57.3 dBFS RMS, Toms
  −38.5 → −38.2, all others ≤0.2 dB).
- **Cache identity.** `remask_cache_component` now emits `drumremask` /
  `primaryremask` without the exponent, so the key changes with this switch and
  pre-change stems are not served
  (`test_stem_cache_identity_changes_for_remask`). Web project stems under
  `apps/api/data/project-stems` are **not** cache-invalidated automatically and
  must be regenerated to pick this up.
- Level domain: the pass adds an STFT-derived signal and never normalizes;
  `docs/contracts/stem_level_domain.md` is unaffected.

## Follow-up

The dullness the drumsep pass was adopted to fix is still there, and this
report shows re-masking cannot fix it at any exponent — the collision is in the
model's partition, not in the discarded remainder. The remaining levers are the
ones the knowledge base already lists: jarredou's 5-stem MDX23C variant
(ride+crash merged, +2 SDR per class), or ensembling drumsep models from
different architectures. Both need their own report.
