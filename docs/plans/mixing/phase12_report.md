# Phase 12 report — Wet/dry stem split routed as separate stems

Plan: `phase12_wet_dry_split.md`. Status: **code complete, default off.** A
harness run on a synthetic dry+RIR corpus is in §7 — directional only, not a
licensed-corpus verdict — and the A/B listening note is still outstanding.
The synthetic run did change the code: it measured the KB's harmony trap and
the combined-`Vocals` path now warns (§7.3).

## 1. What shipped

A per-stem dereverb stage in the separation plan. The dry output keeps the
parent stem's name; the wet output is a new ordinary stem, `"Vocals Reverb"`,
that the existing router places surround/height-heavy. No routing DSP was
touched.

- `config.stem_wet_dry_split` (default `False`) enables the split.
  `config.stem_dereverb_model` selects the checkpoint;
  `config.stem_wet_denoise` (default `False`) adds a denoise pass over the
  wet stem only. All three are plumbed through the manifest `engine` block,
  the CLI (`--stem-wet-dry-split`, `--stem-dereverb-model`,
  `--stem-wet-denoise`), and the API's separation-engine key lists.
- Registry entries for `dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt`
  and its less-aggressive sibling, sharing one bundled upstream YAML
  (`configs/dereverb_mel_band_roformer_anvuew.yaml`). GPL weights:
  runtime-download only, nothing bundled.
- `"Vocals Reverb"` is a canonical stem (`vocals-reverb` in the manifest
  vocabulary) with placement rows in every preset and hand-authored
  `ZONE_ROUTING` rows in all five multichannel-input zones.

## 2. Mechanism decisions

**The wet stem is the model's own residual, not a second model output.**
`demix.py` already returns `{target: primary, secondary: mix - primary}` for
single-target checkpoints, and the dereverb config's target is `noreverb`.
So the plan needed no residual code of its own: dry + wet nulls against the
parent by construction, exactly as the plan required, and the remask /
residual-sharing passes stay out of it — there is no remainder left to share.

**Which stem gets split.** `"Lead Vocals"` when the karaoke stage runs,
`"Vocals"` otherwise (the two never coexist in a plan). This is the KB trap
in `models/cleanup.md`: the anvuew checkpoints also strip non-centre
harmonies, so splitting a combined Vocals stem sends backing vocals into the
wet residual. Splitting the lead avoids it. Now measured, not just cited —
§7.3 — and the combined-`Vocals` path warns. `"Backing Vocals"` is not split
in this slice.

**Task-level stem naming.** The dereverb checkpoint names its outputs after
the split (`noreverb`/`reverb`), not after what it was fed, so
`SeparationTask` gained an optional `stem_overrides` map that takes
precedence over the per-model `MODEL_STEM_OVERRIDES` table. The wet name is
`"Vocals Reverb"` for either parent — one placement row, one routing row.

**Cache identity.** No change to `stem_identity.py` was needed: the dereverb
and wet-denoise stages are plan tasks, and `plan.inference_hash` already
folds every task's model, input and outputs into the cache key. Verified by
test: plain / split / split+denoise produce three distinct identities.

**The null assert is a test, not a runtime check.** Asserting it in the
pipeline would mean reloading the parent and both children on every export
for a property that is structural. `test_wet_dry_split.py` pins it at both
levels — through `execute_plan` with a stub separator, and through
`demix_roformer` with the real bundled config and a stub model.

**Transient duck.** No special case, per the plan's item 5: the wet stem is
sustain by construction, so the duck's onset detector finds nothing to hold
back and the shared code path is correct as-is.

**Rebalance profiles.** The wet stem tracks its dry stem's gain in all three
non-identity profiles (`vocal-forward` +2.5, `instrumental` −3.0,
`bass-heavy` −0.5). Moving one without the other changes the wet/dry ratio,
which is perceived distance — a thing a level profile should never do by
accident. `stem_eq` needed no equivalent: it has no built-in stem-name
defaults, only user-assigned profiles.

**Intermediate cleanup (incidental fix).** `execute_plan` kept every on-disk
intermediate whose name was in `requested_stems`, and would have leaked the
pre-split parent file once a later stage re-emitted a name. Superseded copies
are now unlinked when a stage replaces them, and every intermediate is
unlinked at the end — all of them have been read into memory by then. This
also closes a pre-existing leak for requested stems that were staged on disk
(e.g. `Drums` feeding drumsep).

## 3. Placement and routing

`"Vocals Reverb"` sits behind and above, wide, zero LFE — where `Crowd` sits
in spirit, lifted further. Balanced: azimuth 180°, elevation 22°, width 150°,
spread 84°. `intimate` pulls it in (elev 10°, width 104°), `immersive` lifts
it (elev 38°, width 160°); `wide` and `live` get their own rows; `stage`
inherits balanced.

Zone rows are surround/height-heavy in every zone, so the bleed-reduction
gate (`stem_reaches_surround_height`) reports `(True, True)` for the wet stem
in all five zones plus the untagged key — verified by test rather than
assumed.

## 4. Measurement

Measurement kit 4 (`test_mix_measurement.py -m perf -k channel_energy`),
balanced preset, zone energy as a fraction of stem input energy:

| Layout | Stem | front | surround | height | LFE |
|---|---|---|---|---|---|
| 7.1.4 | Vocals | 0.993 | 0.000 | 0.003 | 0.000 |
| 7.1.4 | Vocals Reverb | 0.001 | 0.140 | 0.310 | 0.000 |
| 7.1.4 | Crowd | 0.001 | 0.347 | 0.108 | 0.000 |
| 5.1 | Vocals | 0.999 | 0.000 | — | 0.000 |
| 5.1 | Vocals Reverb | 0.022 | 0.334 | — | 0.000 |
| stereo | Vocals Reverb | 1.000 | 0.000 | 0.000 | 0.000 |

The vocal tail moves rearward and upward (height 0.310 vs the dry stem's
0.003) while the dry vocal's front distribution is untouched — the `Vocals`
row is identical to the phase 8 baseline, because nothing about the dry path
changed. Stereo folds the wet stem back to the front pair per the phase 4
rules, as every placement does at that layout.

The 4b per-preset averages moved slightly (they average over every stem, and
there is now one more stem in the table); no other kit measurement changed.

## 5. Parity

No new routing DSP, no send-constant change, no worklet change — the wet stem
is data, consumed identically by export and preview. The API's
`/api/v1/stem-routing/resolve` serves the new rows (test added), and the web
surfaces tolerate the name: colour and icon entries added, and the stem
vocabulary the configuration endpoint publishes now includes
`"Vocals Reverb"`, which also means selecting it in the composer enables the
split on its own. No ledger entry needed.

## 6. Validation

- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q`:
  **1151 passed / 38 deselected** (before this phase: 1135 passed / 34
  deselected — the README's 1133 was stale by phase 11).
- `npm test` (31 files, 246 tests) and `npm run build` green in `apps/web`.
- Null test: dry + wet reconstructs the parent to `atol=1e-6` through
  `execute_plan`, and `noreverb + reverb == mix` to `atol=1e-6` through
  `demix_roformer` with the bundled dereverb config. Not bit-exact: both
  halves are rounded independently on the float32 WAV round-trip.
- Registry smoke (tier 1) builds the Mel-Band Roformer arch from the new
  bundled YAML for both dereverb checkpoints.
- Feature off: no code path differs. The resolver adds no task, the executor
  passes no overrides, and the new placement/routing rows are name-keyed
  lookups nothing iterates in the audio path. Output is bit-identical to
  phase 11 head.

## 7. Harness run — synthetic corpus, directional only

Both checkpoints were downloaded (913 MB each, GPL, runtime-fetched into the
model cache, nothing bundled) and scored with `upmixer.eval`'s harness over a
synthetic corpus built for this run: a vocal-like dry source (phrased harmonic
stack with vibrato, three formants, breath noise, hard phrase stops), wet =
dry convolved with a synthetic RIR minus its direct impulse, mixture = dry +
wet exactly. Three reverb categories at fixed wet/dry ratios, 10 s each,
44.1 kHz. Corpus builder: `scratchpad/dereverb_eval.py` (not committed —
regenerate rather than trust a stale copy).

**Read these numbers as a comparison between two checkpoints, not as an
absolute quality claim.** A formant-shaped harmonic stack and an
exponentially-decaying filtered-noise tail are the easiest possible material
for a dereverb model; real singing in a real room is harder, so the absolute
SDRs are optimistic. This is the "directional" run, not the licensed-corpus
report the plan asks for.

### 7.1 Per-stem and per-category

| Model | Stem | SDR | fullness | bleedless |
|---|---|---|---|---|
| anvuew 19.1729 (standard) | Vocals (dry) | **16.28** | 0.919 | 0.977 |
| anvuew 19.1729 (standard) | Vocals Reverb (wet) | **9.28** | 0.726 | 0.930 |
| anvuew 18.8050 (less aggr.) | Vocals (dry) | 15.81 | 0.916 | 0.975 |
| anvuew 18.8050 (less aggr.) | Vocals Reverb (wet) | 8.81 | 0.718 | 0.930 |

| Category (wet/dry ratio) | standard SDR | less-aggressive SDR |
|---|---|---|
| vocal_hall (T60 1.8 s, −4 dB) | **14.06** | 13.82 |
| vocal_room (T60 0.45 s, −10 dB) | **12.73** | 11.17 |
| vocal_plate (T60 1.1 s, −7 dB) | 11.54 | **11.93** |

The standard checkpoint leads on average (+0.47 dB SDR overall, +1.56 dB on
the short room) and loses only the plate by 0.4 dB. **Decision: keep
`stem_dereverb_model` on the standard checkpoint** — no longer a placeholder,
but a thin margin on synthetic material, so a licensed-corpus run could
overturn it. Both stay registered.

The wet stem scores well below the dry one on every axis (fullness 0.73):
what the model gets wrong lands in the residual by construction. That is the
motivation for `stem_wet_denoise`, still unmeasured.

### 7.2 Dry control — the split is safe on dry material

Fed a mono, centred, reverb-free vocal, the wet stem comes back at **−57.9 dB**
(standard) / **−61.6 dB** (less aggressive) relative to the input. Neither
model invents reverb, so enabling the split on a dry-produced track costs
essentially nothing — the wet stem is silent and the dry stem is the input.
In-memory null of dry + wet against the input: −238 dB.

### 7.3 Harmony probe — the KB trap, measured, and a code change

The KB warns that these checkpoints strip non-centre harmonies. A first probe
with a *centred* harmony showed nothing (−50 dB), which was the probe's fault,
not the model's. Re-run with a genuinely side-dominant harmony (double-tracked,
a different take per channel, −9.4 dB side/mid) and **no reverb at all**:

| Model | wet stem vs input | wet stem vs the harmony layer |
|---|---|---|
| anvuew 19.1729 | −7.43 dB | **−0.79 dB** |
| anvuew 18.8050 | −7.44 dB | **−0.80 dB** |

The "reverb" stem is the harmony layer, near enough in full, on both
checkpoints. So the trap is real and large, and it lands exactly on the
default path: splitting a combined `Vocals` stem sends double-tracked backing
vocals to the surrounds and heights. `separate()` now logs a warning when the
split runs on `Vocals` rather than `Lead Vocals`
(`warn_combined_vocal_split`), pointing at the karaoke-stem workaround the
resolver already prefers. Not an error: sending wide backing vocals up and
back is a legitimate mix move — it just must not happen silently under a knob
labelled "reverb".

### 7.4 Still outstanding

- A licensed-corpus harness run. The synthetic distribution flatters the
  models and cannot represent real rooms or real singers.
- The A/B listening note (`evaluation.md` §6) on a reverb-heavy ballad and a
  dry-produced pop track. Watch for gated/chopped tails at phrase ends — the
  corpus has hard phrase stops precisely there, but SDR does not hear
  gating.
- `stem_wet_denoise` is implemented and unmeasured.

## 8. Out of scope, unchanged

Instrument stems (the MDX23C De-Reverb lane), dereverb-as-repair, weight
bundling, and any change to the duck, panner or send DSP.
