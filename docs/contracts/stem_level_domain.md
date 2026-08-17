# Stem level domain

Separated stems are delivered in the **input file's level domain**: for one
model stage, `sum(stems) == input`, to within the model's own reconstruction
error. Nothing in the separation path applies an undeclared gain.

## Why the normalization is undone, not removed

The models are trained on peak-normalized audio, so
`inference/engine.py` still peak-normalizes the mix to 0.9 before demix.
`audio_io.normalize` returns the scale it applied, and the engine divides that
scale back out of every stem before writing.

Previously the scale was neither recorded nor undone, and `write_stem`
peak-normalized every output stem a *second* time, independently. Because a
stem plan chains 3–5 model stages (`stem_plan.py`), each stage rescaled its
output by a different amount and the error compounded with chain depth. Two
defects followed:

- **Level loss.** Loud masters lost 1.7–3.8 dB overall.
- **Balance error.** Stems exiting the chain at different depths drifted apart
  by 1–2.2 dB — vocals leave after one stage, drum kit pieces after three — so
  the error was not a single correctable trim.

Quiet and dynamic sources were unaffected: their peak never exceeded 0.9, so
the clamp never engaged. The bug was silent on exactly the material used to
sanity-check it.

Do **not** reintroduce a mid-chain peak clamp. A clamp anywhere between stages
re-creates the compounding error. The only normalization is the pre-demix one,
and it must be divided back out in the same function that applied it.

## Storage subtype

A stem carries the source's true level, so a stem separated from a clipped
master exceeds 1.0 — measured at +0.87 dBFS on a −0.05 dBFS master. Both stem
stores therefore write **float** WAV, not PCM_24, which would hard-clip:

- `separation/stem_store.py` (`PlainStemStore`, web project stems)
- `separation/stem_cache.py` (`StemCache`, CLI cache)

Consumers read these as float32 and depend on sample rate, channel count and
duration only; nothing assumes a fixed-point subtype.

## Cache invalidation

`stem_cache.py`'s `_ENGINE_VERSION` gates every cached entry and is bumped on
any change that alters stem audio — this fix bumped it to `upmixer-sep-3`.
`PlainStemStore` has no cache identity by design, so web project stems under
`apps/api/data/project-stems` are **not** invalidated automatically and must be
regenerated after a change like this one.
