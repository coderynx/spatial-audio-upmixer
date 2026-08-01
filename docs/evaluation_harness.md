# Objective Separation Evaluation Harness

Consult this document before changing separation models, ensembling, or any
phase-fix/debleed post-pass — it is the canonical in-repo spec for
`packages/core/src/eval/` and the precondition the roadmap sets on those changes
(see `~/Projects/upmixer-knowledge/roadmap.md` Phase 0.1 and
`~/Projects/upmixer-knowledge/techniques/evaluation.md` for the wider
community context these metrics are drawn from).

## Core rule

**Report SDR, fullness, and bleedless together, always.** SDR alone conflates
how much of the true stem survives with how much foreign content leaked in;
a model can score well on SDR while sounding worse on one axis and better on
the other. `upmixer.eval.report.format_report` enforces this by always
printing all three per stem and per category.

## Metrics (`packages/core/src/eval/metrics.py`)

**SDR** — the community-standard formula, fixed by convention:

```python
delta = 1e-7
sdr = 10 * log10((sum(reference**2) + delta) / (sum((reference - estimate)**2) + delta))
```

Known limits: energy-weighted (favors low frequencies), under-represents
transients and HF detail, and cannot distinguish *which* of fullness or
bleed moved the number.

**Fullness** and **bleedless** are only defined conceptually upstream
(jarredou's metrics, the MVSEP quality checker): fullness is "how much of the
true stem's content survives," bleedless is "how little foreign content
leaks in." This package operationalizes both on the magnitude STFT (reusing
`upmixer.analysis.stft.STFTAnalyzer`, summed to mono):

```
retained   = sum(min(|STFT(reference)|, |STFT(estimate)|))
fullness   = retained / sum(|STFT(reference)|)     # in [0, 1]
bleedless  = retained / sum(|STFT(estimate)|)       # in [0, 1]
```

Both are clipped to `[0, 1]`. 1.0 fullness means every bin of the reference
is at least matched by the estimate; 1.0 bleedless means the estimate has no
energy beyond what the reference has. This is a documented, in-house choice
— not a normative standard, and not identical to jarredou's/MVSEP's exact
computation — so treat cross-comparisons with the public MVSEP leaderboard
as directional, not literal.

**All three metrics are advisory community metrics.** Model-selection
decisions should also account for the regression-probe categories below, not
raw scores alone.

## Corpus (`packages/core/src/eval/corpus.py`)

**No copyrighted audio ships with this repository.** `MUSDB18-HQ` is
research-only and is not bundled; licensing for internal-eval use is an open
point tracked in the roadmap.

- `ReferenceCorpus.from_dir(path)` loads a user-supplied, lawfully-licensed
  directory containing a `corpus.json` manifest:

  ```json
  {
    "items": [
      {
        "mixture": "song1/mix.wav",
        "stems": {"Vocals": "song1/vocals.wav", "Bass": "song1/bass.wav"},
        "category": "default"
      }
    ]
  }
  ```

  Paths are resolved relative to `path`. `category` groups results for the
  regression-probe convention below; use `"default"` for ordinary material.

- `synthetic_corpus(sample_rate, out_dir)` generates a small, fully lawful,
  ground-truth-exact corpus in-process (every mixture is the literal sum of
  its known stems). It exists for deterministic testing of the harness
  itself and CI, **not** as a substitute for real-music evaluation — model
  rankings on synthetic tones do not transfer to musical material.

  Generated items must stay comfortably above **~3 seconds** at 44100 Hz:
  shorter clips have been observed to make `BS-Roformer-SW` (and MDXC models
  generally) silently return zero output stems at some
  segment_size/sample_rate combinations. Always drive real-model runs at the
  model's native 44100 Hz.

## Category regression probes

Per the "AI-killing tracks" convention (evaluation.md), a candidate model or
change must not regress on known model-killer material categories relative
to the incumbent, even if its overall/leaderboard number is higher.
`synthetic_corpus` includes two starter categories (`dense_synth`: a stacked
detuned-partial texture; `choir_cluster`: several near-unison detuned tones)
alongside `"default"`, so the per-category grouping (`EvalReport.by_category`)
is exercised from day one. Extend with real-material categories (vocoders,
dense synths, VHS-era sources, etc.) via a lawfully-licensed `from_dir`
corpus as they become available — do not use the community's own curated
track list, which is copyrighted commercial music.

## Deterministic settings (`packages/core/src/eval/harness.py`)

**Scores without settings are noise.** Every `EvalReport` carries the exact
`RunSettings` used to produce it: `model`, `sample_rate`, `segment_size`,
`overlap`, `batch_size`, and (for future ensembling work) `ensemble_algorithm`
/ `ensemble_models`. `format_report` always prints this block before the
score tables. When comparing two runs, only settings that differ should be
attributed as the cause of a score difference — everything else must match.

`separate_for_eval` drives the real `upmixer.separation.separator.StemSeparator`
— the same inference path production code uses — so harness scores measure
what production actually does. `evaluate_corpus` takes a pluggable
`separate_fn: Callable[[str], tuple[dict[str, np.ndarray], RunSettings]]`
so tests can substitute a fast, deterministic stand-in without downloading
model weights (`packages/core/tests/test_eval_metrics.py`); real evaluation runs bind
`separate_for_eval` via `functools.partial` (`packages/core/tests/test_eval_harness.py`).

## Running it

```bash
# Metric math + harness plumbing — deterministic, no model download.
uv run pytest packages/core/tests/test_eval_metrics.py -q

# Real separation on the synthetic corpus with the default model — downloads
# weights on first run, prints the per-stem SDR/fullness/bleedless report.
uv run pytest packages/core/tests -m perf -k eval -s
```

## What this unblocks

This harness is the precondition for:

- **Ensembling (roadmap 2.1)** — measuring whether an SW + Demucs_ft ensemble
  actually improves shared stems before it becomes a default tier.
- **Phase-fix/debleed (roadmap 2.2)** — measuring bleedless gain vs. fullness
  cost of any in-house phase-fixer or debleed pass.
- **Model swaps (roadmap 1.2, 2.3, 2.4)** — comparing a candidate model
  against the incumbent on both aggregate and per-category scores before it
  replaces anything in `packages/core/src/separation/stem_plan.py`.

Nothing in `stem_plan.py` should change model selection without a report
generated by this harness attached to the PR.
