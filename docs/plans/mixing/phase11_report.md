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

## 2. What shipped

Two things, one per half of §1's answer.

**Deleted.** `packages/core/src/analysis/stem_analyzer.py`,
`packages/core/src/separation/stem_analyzer.py`, and
`test_stem_router.py::test_analyzer_treats_antiphase_and_hard_pan_as_wide`,
the only reference to either. No dangling imports across the five packages.

`content_mix_strength` and `content_hf_analysis_hz` survive the sweep on
purpose: despite the names they are read by `routing/channel_router.py` (the
non-stem coherence path), where the first blends the conservative and content
masks and the second sets the detail cutoff. Legacy names for live wiring, not
dead code — renaming them would churn the manifest, API and web surface for
nothing.

**Added.** `packages/dsp/crates/dsp-core/src/routing/transient.rs`: a causal
transient/sustain split on the surround and height send inputs, behind
`config.stem_transient_duck`, default 0.0.

## 3. The detector

Two envelope followers on the summed magnitude of both sides — a fast one
(1.5 ms attack, 60 ms release) and a slow reference (250 ms). The ratio
between them, above a floor, ducks the send:

```
score = clamp((fast/slow − 1.25) / (2.5 − 1.25), 0, 1)
gain  = 1 − depth · score
```

Both sides of the send take the same gain, so a one-sided onset cannot pull
the send's image across (`one_sided_onset_does_not_shift_the_balance`).

Three design points cost a rewrite each and are worth recording, since none is
visible from the final form:

1. **The reference must track the fast envelope, not the raw magnitude.**
   Against `|x|`, a steady sine reads as its own crest factor forever — the
   fast follower rides the peak, the slow one settles at the mean 2/π of it,
   so the ratio parks at 1.57 and never comes down. First measurement of that
   version: sustain ducked by **6.65 dB** on material with no transient in it
   at all. Tracking `fast` instead makes the steady-state ratio 1 by
   construction.

2. **The score needs a ratio floor.** A rectify-and-smooth follower ripples a
   few percent within every cycle of a low tone, and without a floor that
   ripple amplitude-modulates steady content at twice the tone frequency
   (residual after fix 1: 0.66 dB, at 2f). The floor is not sensitivity
   tuning; it is what makes sub-onset ripple score exactly zero, so steady
   content comes out bit for bit. This is the same device
   `PerBandTransientDetector` already uses with `transient_sensitivity_k`.

3. **The divide belongs behind the threshold test.** See §6.

Time constants and the two ratios are structural, in `routing::transient`,
not served — one detector for both bindings. Only `depth` crosses the wire.

## 4. Where it sits

`StemRouter.route` ducks `stem_L`/`stem_R` once and feeds the result to both
`_surround_send` and `_height_send`; the dry front/centre paths keep the
unducked stem. In `stream::routing`, `StemRouteState` runs one ducker over
the block before the sends. One ducker there against two offline calls is not
a divergence — the detector's state depends only on the stem's input, so a
single trajectory is what each offline call independently reproduces, and
`ducked_sends_match_the_offline_duck_then_shape_order` pins the whole
duck→filter→velvet order against the offline path, blocked in ragged sizes.

The interaction with phase 9 is the part that makes this work musically. The
duck removes energy from the diffuse sends, so `_route_scale` — which matches
each stem's routed loudness to its own — raises the whole stem to compensate.
The transient's energy therefore moves **to the front bed** rather than
leaving the mix. `test_transient_duck_attenuates_onsets_in_the_diffuse_sends_only`
asserts exactly that: SL/TFL down, FL up.

## 5. Measurements

Synthetic case, 48 kHz, snare-like hits (1 ms rise, 40 ms decay) over a steady
bed of noise + 330 Hz tone. Energy in the hit windows vs the sustain windows
between them, against the same render with the duck off:

| depth | hit | sustain | separation |
|---|---|---|---|
| 0.3 | −2.55 dB | 0.00 dB | 2.55 dB |
| 0.5 | −4.69 dB | 0.00 dB | 4.69 dB |
| 0.7 | −7.26 dB | 0.00 dB | 7.26 dB |
| 1.0 | −10.98 dB | 0.00 dB | 10.98 dB |

Sustain reads 0.00 dB at every depth — that is fix 2 in §3 doing its job, not
a rounding artifact.

**The stated figure: at depth 0.7 an onset reaches the surround and height
sends 7.26 dB quieter than the sustain around it.**

### The ceiling

A sub-millisecond impulse is a different story. The same measurement on a
30-sample click train:

| depth | click | sustain | separation |
|---|---|---|---|
| 0.7 | −2.70 dB | 0.00 dB | 2.70 dB |
| 1.0 | −3.73 dB | 0.00 dB | 3.73 dB |

The 1.5 ms attack is longer than the click, so the detector reacts after the
event is over. This is inherent to a feed-forward detector with no lookahead,
and lookahead is not available here: it would delay the diffuse sends against
the front bed, which is a worse defect than the one being fixed. Real drum
transients are 5–50 ms and land in the first table; a 0.6 ms click is the
worst case and it is stated rather than tuned away.

Off is off, exactly: `transient_duck(x, x, sr, 0.0)` returns `x` bit for bit,
and routing with `stem_transient_duck=0.0` is `np.array_equal` to routing
with the field untouched, on every channel.

## 6. Budget

The plan cites D33's lesson — budget the stage before wiring it into the
worklet — and this is the phase where that lesson paid for itself.

The bench fixture now sets `stem_transient_duck: 1`, following the
`decorrelate: 1` convention already in that file: a stage that ships
default-off is benched *on*, because the budget question is what it costs when
a user reaches for it.

First cut, at full depth, two cases over:

| case | p99 | budget |
|---|---|---|
| measuring (exact, paused) | 2.673 ms (1.00x) | 1.0x |
| measuring (fast excerpt, playing) | 4.731 ms (1.77x) | 1.5x |

The cost was a division on the per-sample dependency chain, taken whether or
not the sample was an onset. Comparing `fast` against `slow · threshold`
first — algebraically the same score — skips it for the overwhelming majority
of samples; the send buffers also `reserve` instead of growing.

After, at full depth, every case green:

| case | mean | p99 | worst |
|---|---|---|---|
| binaural (order-3 decode) | 0.832 ms (0.31x) | 2.322 ms (0.87x) | 3.653 ms (1.37x) |
| transaural | 0.815 ms (0.31x) | 2.137 ms (0.80x) | 3.326 ms (1.25x) |
| native 7.1.4 + limiter | 0.687 ms (0.26x) | 1.986 ms (0.74x) | 3.323 ms (1.25x) |
| stereo downmix | 0.567 ms (0.21x) | 1.877 ms (0.70x) | 3.132 ms (1.17x) |
| measuring (exact, paused) | 1.777 ms (0.67x) | 2.434 ms (0.91x) | 2.980 ms (1.12x) |
| measuring (fast excerpt, playing) | 1.339 ms (0.50x) | 4.198 ms (1.57x) | 4.927 ms (1.85x) |
| mix edit (mute + compressor) | 0.047 ms (0.02x) | 0.063 ms (0.02x) | 0.110 ms (0.04x) |

At the shipped default the loop is skipped outright and the numbers sit on
the phase 8 baseline (binaural mean 0.756 ms / 0.28x, native 7.1.4 mean
0.622 ms / 0.23x).

Ledger entry D35 records the overrun and its fix.

## 7. Validation

- `cd packages/dsp && cargo test` — 181 passed, 0 failed (136 lib + goldens +
  `stream_equivalence`).
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` —
  **1134 passed, 34 deselected**, from a 1133 baseline: −1 for the deleted
  analyzer test, +2 for the duck's.
- `cd apps/web && npm test` — 244 passed, 31 files. `npm run build` — clean.
- `npm run build:wasm` then `npm run bench:engine` — §6.
- Rebuilt both bindings: `uv sync … --reinstall-package upmixer-dsp` and the
  committed `apps/web/public/wasm/upmixer_dsp.wasm`, so the §1
  build-provenance risk stays closed.

## 8. Still open

**The A/B listening note the plan asks for has not been done.** Rung 1 ships
default-off with the objective case measured (§5) and the budget cleared
(§6), but "dense rock track: dry kit stays front, tails wrap; sparse track: no
pumping or spatial wander" is a listening judgement on real material and no
listening pass has been run. Until it is:

- The default stays 0.0, and nothing in the presets sets it.
- The 0.7 figure in §5 is a measurement, not a recommended value.
- Rung 2 (per-section envelope on existing sends) stays unstarted — the plan
  gates it on rung 1 shipping *and* listening asking for more.

This joins phase 7's directional-band A/B and phase 10's preset A/B as
outstanding listening work; all three are default-off or unity-gain until
someone runs them.

The one-sided-onset link is worth re-checking by ear specifically: it is
correct by construction and pinned by test, but the audible question — does a
shared gain make a hard-panned hi-hat duck the whole send pair
distractingly — is exactly the kind of thing measurement will not answer.

