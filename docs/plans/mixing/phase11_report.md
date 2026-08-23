# Phase 11 report — content-aware routing: archaeology, then a causal send split

Plan: `docs/plans/mixing/phase11_content_aware_routing.md`. Baseline tables
cited are phase 8's (`phase8_baseline.md`), per the README ground rules.

## 1. Archaeology (the gate)

### 1.1 There were two features, not one

The plan describes "a removed prior attempt". `git log --follow` shows two,
built four weeks apart, each with its own analyzer module, and each removed
for a different reason. That distinction decides the gate, so it is worth
stating precisely.

**Feature A — `ContentMixer` (May 2026).** `6159218 feat: add content-aware
stem mixing` added `upmixer/analysis/stem_analyzer.py` (`StemAnalyzer`, a
seven-field `StemFeatures`: rms, spectral centroid, lf/hf ratio, stereo
width, transient density, spectral flatness) plus
`upmixer/separation/content_mixer.py`, which mapped those features to
per-channel modifiers and handed `StemUpmixPipeline` a `per_stem_routing`
override. Config knobs `content_aware_mixing`, `content_mix_strength`,
`content_hf_analysis_hz`; CLI flags `--no-content-mix`,
`--content-mix-strength`.

`226f7b2 chore: code cleanup` deleted `content_mixer.py` and left
`analysis/stem_analyzer.py` behind with no importer. It has been unreachable
since. No quality objection was recorded — the module was simply orphaned
when its only consumer went, superseded by feature B, which had landed in the
router itself.

**Feature B — `_content_scale` (June–July 2026).** `e4a9885 feat: add stem
content analyzer and integrate into routing` added
`upmixer/separation/stem_analyzer.py` (`analyze_stem`/`analyze_stems`, a
four-field `StemFeatures`: stereo width, hf ratio, lf ratio, transient
ratio), hardened by `bbb37bf` into the 60 s-sampled form still on disk.
`StemRouter.route` took `stem_features` and, per output channel, multiplied
the static table gain by `_content_scale(features, label)` — a scale
calibrated to 1.0 at a neutral feature vector, applied as
`gain *= 1 + content_mix_strength * (scale - 1)`. Alongside it,
`analysis/spatial.py`'s `SpatialPlan` supplied per-channel-group envelopes
interpolated to sample rate.

`ae98849 refactor: static preset-based stem routing` (2026-07-23) removed
both call sites in one commit. Its message is explicit about what replaced
them: `build_stem_routing()` with named presets, `UpmixConfig.stem_routing`
and `stem_enabled`, manifest validation for both blocks, an
`/api/v1/stem-routing/resolve` endpoint, and a web speaker-routing matrix
replacing the 3D spatial scene. "Stem pipeline no longer analyzes separated
audio; routing is fully static and controlled via manifest or API."

### 1.2 What the feature actually did

Both analyzers are **whole-file, non-causal, and scalar per track**. Neither
produces a time-varying signal. `analyze_stem` samples up to 60 s across the
track, reduces it to four numbers, and those four numbers scale routing gains
by a constant for the whole render. The `SpatialPlan` envelope was the only
genuinely time-varying part, and it came from a different module
(`analysis/spatial.py`), not from either analyzer.

This matters because the plan's rung 1 asks for a *per-frame* transient/
sustain split. Neither dead module can supply one. Whatever the gate decides
about the feature, the modules themselves are not the code that would
implement it.

### 1.3 Why it was removed, and whether that reason still holds

Feature A: orphaned by cleanup, superseded by B. Nothing to re-litigate.

Feature B: removed because routing became an **explicitly controlled**
surface — preset, manifest, API, per-track override in the UI — and a hidden
whole-file gain scale contradicts that. The reason was product-shaped
(predictability and user control), not "it sounded bad" and not
"it was too slow".

Does it still hold after phases 0–10? **Yes, and harder than it did in
July**, for two reasons:

1. *Explicit control got more load-bearing, not less.* `preset_routing` /
   `build_stem_routing`, the `/stem-routing/resolve` endpoint, and the web
   routing matrix are now the primary way a user positions a stem, and phase
   10 put the MDAP panner underneath `StemPlacement` so the numbers the UI
   shows are the numbers the panner realizes. A whole-file scale multiplying
   those gains would make the displayed routing a lie.
2. *A constraint that did not exist in July now forbids the mechanism
   outright.* Since phase 3, every send-affecting stage lands once in
   `dsp-core` and runs identically in the offline PyO3 path and the wasm
   streaming preview (`docs/contracts/preview_export_parity.md`). A
   whole-file analysis pass **cannot** run in the preview — the worklet has
   no whole file, only the next 128 samples. Reviving `_content_scale` would
   manufacture a preview/export divergence of exactly the class the parity
   ledger exists to catch.

**Gate answer, part 1 — the modules: DELETE.** `analysis/stem_analyzer.py`
and `separation/stem_analyzer.py` are removed, with the one test that
referenced the latter. The removal reason stands, and the added parity
constraint independently rules out their mechanism.

### 1.4 The feature is not the modules

The plan's gate is worded as one decision ("Delete … phase ends here" /
"Revive … step 2"), which is right only if the modules and rung 1 are the
same thing. They are not, and both halves of the answer should be honoured
rather than letting a module deletion cancel a feature that was never judged.

Rung 1 — split the surround/height **send input** into transient and sustain
components, keep transients front-anchored — is a different mechanism from
what was removed:

| | Removed `_content_scale` | Rung 1 |
|---|---|---|
| Operates on | the routing *gain* | the send *input signal* |
| Time behaviour | one constant per track | per sample |
| Needs the whole file | yes | no — causal |
| Contradicts the user's preset | yes, silently | no, the preset gain is untouched |
| Can run in the worklet | no | yes |

Every objection in §1.3 is an objection to overriding user-set gains from
non-causal analysis. None of them lands on a causal filter that shapes what
goes *into* a send the user's preset already sized. So the gate's delete
outcome applies to the modules; rung 1 proceeds to step 2 on its own merits,
which the audit findings independently support ("drums smeared into
surrounds").

### 1.5 What not to repeat

From the archaeology, three failure modes to avoid:

1. **No whole-file analysis in the routing path.** It cannot exist in the
   preview. Causal or nothing.
2. **Do not multiply the user's routing gains.** The preset is the contract
   with the UI. Shape the signal, not the gain.
3. **Do not leave the analyzer behind when the consumer goes.** Feature A's
   module sat unreferenced for three months. One consumer, deleted together.

Rung 1 of §1.4 (a causal transient/sustain duck on the diffuse sends) shipped
after this archaeology and was later removed in full; see git history for its
implementation and removal.
