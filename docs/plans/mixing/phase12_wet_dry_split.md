# Phase 12 — Wet/dry stem split routed as separate stems

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phases 0–11 merged. This is the highest-value follow-on to
phase 11: instead of inferring "what belongs in the surrounds" from the
send input causally, produce it at separation time — split each eligible
stem into a dry component and a reverb/ambience component, and let the
existing router place them. This is what a human Atmos mixer does with
reverb returns; here it becomes two ordinary stems flowing through the
machinery phases 0–11 built.

**This phase changes separation behavior.** Unlike every earlier mixing
phase, the separation eval harness IS required
(`docs/evaluation_harness.md`), and the knowledge base must be consulted
before touching the model registry (`packages/core/AGENTS.md`):
`~/Projects/upmixer-knowledge/roadmap.md` 2.4 and
`~/Projects/upmixer-knowledge/models/` catalog the candidate dereverb
models — anvuew Mel dereverb (GPL weights: runtime-download only, never
bundle), the Sucial de-echo family, aufr33 denoise. Read those entries
first; pick by harness measurement, not reputation.

## Mechanism

1. **Separation side.** A per-stem dereverb pass as a staged plan task —
   the same chained mechanism drumsep already uses
   (`SeparationPlan` tasks with `input_source="Vocals"` etc., executed by
   `stem_pipeline_exec.py`). The model's output is the dry stem; the wet
   stem is the **residual** (`input − dry`), so the pair nulls against
   the parent exactly and no energy is invented or lost. Check how the
   remask/residual-sharing passes compose with this (the drum/primary
   remask decisions in `docs/reports/` are precedent for residual
   handling — a full re-projection was a regression there; the residual
   convention here must not repeat that mistake).
2. **Eligible stems, first slice.** Vocals (and Lead/Backing when
   present) only — the strong dereverb models are vocal-trained, and
   vocal reverb smeared to the front wall is the most audible complaint.
   Checkpoint trap recorded in the KB (`models/cleanup.md` selection
   guidance): anvuew Mel v2 also removes non-center harmonies, so on a
   combined Vocals stem its wet residual swallows backing-vocal content,
   not just reverb — when the karaoke split is active, run the split on
   Lead Vocals, and otherwise prefer a checkpoint without that side
   effect (harness + listening decide).
   Guitar/piano/other are a later slice via the expansion lanes below.
3. **Naming and flow.** Wet stems are ordinary stems: `"Vocals Reverb"`
   (naming decision to confirm against `STEM_NAME_MAP` conventions and
   the web display surface), zone-tagged like their parent, present in
   `stem_summary`, the cache (`_stem_cache_identity` must include the
   dereverb pass identity), the routing matrix, and the manifest
   `stems`/`stem_routing` blocks. No special-case routing code.
4. **Placement defaults.** New entries in the placement preset tables:
   wet stems sit surround/height-heavy (roughly where `Crowd` sits in
   spirit — behind and above, wide, zero LFE), per preset (the
   "intimate" preset pulls them in, "immersive" lifts them). Dry vocal
   placements stay exactly where Vocals sits today. `ZONE_ROUTING` gets
   matching hand-authored rows for multichannel-input zones.
5. **Interaction with the phase 11 duck.** Wet stems are sustain by
   construction; decide (and record) whether the transient duck applies
   to their sends — the natural answer is yes-but-irrelevant (no onsets
   survive dereverb), so prefer no special case unless measurement shows
   one is needed.
6. **Default off.** `config.stem_wet_dry_split` (or a per-stem-family
   variant — follow existing config style), default disabled: it adds a
   model download (possibly GPL, runtime-fetch only) and an inference
   stage per zone. CLI + manifest plumbing per existing patterns. When
   off, output is bit-identical to phase 11 head.

## Candidate checkpoints (web-verified 2026-08-17; details in KB `models/cleanup.md`)

| Candidate | Content | Arch | Integration cost |
|---|---|---|---|
| anvuew Mel Dereverb family (incl. mono variant, strongest) | vocals only (model card: mono single-singer/speech training data) | Mel-Roformer | in-core arch, registry entry only |
| Sucial De-Reverb/De-Echo family | vocals only | Mel-Roformer | in-core arch, registry entry only |
| anvuew BS Dereverb / Dereverb Room | vocals only | BS-Roformer | in-core arch, registry entry only |
| MDX23C De-Reverb (aufr33/jarredou) | **speech and music — full-track/instrumental capable** | TFC-TDF v3 | in-core arch, registry entry only |
| Reverb HQ (FoxJoy) | full tracks (legacy; weak on early reflections) | MDX-Net ONNX | arch NOT ported — real port cost |
| MVSep Team universal dereverb (2026.07) | "any stem" | BS-Roformer | **service-only, no downloadable checkpoint** — not usable |

First slice picks among the vocal-trained rows by harness measurement.

## Instrument expansion lanes (later slice, in priority order)

1. **Measure MDX23C De-Reverb on instrument stems** (guitar/piano/other)
   via the harness — the only downloadable checkpoint trained on music
   content, and zero porting work since TFC-TDF v3 is already in
   `inference/archs/`. Its 6.91 SDR is on the dereverb task, not
   comparable to vocal-model numbers; only the harness verdict counts.
2. **Content-agnostic DSP late-reverb estimator** (Lebart/Habets-style
   spectral subtraction with estimated decay) if the checkpoint fails —
   no model, hand-rolled in core, quality below ML on vocals but works
   on any content. The residual construction makes failure graceful
   either way: a mis-split leaves reverb in the dry stem, which is
   today's status quo — it never invents artifacts in the sum.
3. **Drums: never.** Drum room is usually a desirable part of the kit
   image, and reverb-vs-cymbal-wash separation is where dereverb fails
   worst; drum ambience stays served by the sends + duck.

No universal Roformer dereverb checkpoint is publicly downloadable as of
2026-08-17 — if MVSep-class weights ever get released, they slot into
lane 1's measurement, nothing else changes.

## Post-split processing of the wet stem

The wet stem flows through the same post-separation path every stem gets
(`_post_process_stems`: rebalance, per-stem EQ, then routing), but four
points need explicit handling rather than inheritance by accident:

1. **Null before everything.** The dry + wet = parent property holds only
   at the split itself; every later pass (cleanup, rebalance, EQ)
   intentionally breaks the exact sum. Assert the null immediately after
   the split, before any other processing touches either half.
2. **Wet-residual cleanup (the real addition).** The residual
   concentrates what the dereverb model got wrong — musical-noise
   artifacts, hiss, low-level dry leakage — and it routes to the
   surround/height speakers where artifacts are most audible. Add an
   optional gentle denoise pass on the wet stem only (KB
   `models/cleanup.md` candidates: aufr33 Mel Denoise non-aggressive
   first — already registry-known; roadmap 2.2's insertion point is the
   precedent). Default off, own config key alongside the split's,
   harness + listening gated like the split itself. Runs after the null
   assert, enters `_stem_cache_identity`.
3. **Rebalance/EQ profile coverage.** Automatic name-keyed matching means
   `"Vocals Reverb"` gets independent entries — and that built-in
   rebalance profiles (`vocal-forward`, `instrumental`) currently
   reference `"Vocals"` only. Decide per profile whether the wet stem
   follows the dry gain (boosting dry but not its tail shifts the
   perceived wet/dry ratio, i.e. distance — sometimes wanted, never
   accidental) and add explicit wet entries accordingly. Same review for
   `stem_eq` profile docs.
4. **Existing gates.** Verify, don't assume: the bleed-reduction gate
   (`stem_reaches_surround_height`) must cover wet stems once the new
   placement rows exist (they are surround/height-heavy, so it should
   fall out of the tables — test it); the transient duck needs no
   special case per Mechanism item 5; remask/residual-sharing does not
   apply — the wet stem *is* the residual, there is no remainder left to
   share.

## Parity

No new routing DSP: wet stems are data, consumed identically by export
and preview (the preview already plays whatever cached stems exist).
Verify the preview's stem list handling tolerates the new names (stem
count, colors/labels in the routing matrix, solo/enable per stem) and
that `/api/v1/stem-routing/resolve` serves the new rows. No worklet
change expected; if one appears, the parity contract applies as usual.

## Validation

- **Eval harness report** (mandatory): dereverb-pass quality per
  candidate model on the vocal stem — SDR/fullness/bleedless co-reported
  per the harness contract; the chosen model's report ships with the PR.
  Known harness quirk: MDXC models need >~3 s clips at native 44100 Hz.
- Null test: dry + wet sums to the pre-split stem bit-exactly (residual
  construction guarantees it — assert in a test), measured at the split
  output, before wet cleanup or any other post pass.
- Measurement kit: channel energy tables with the split on — vocal
  reverb energy moves rearward/upward, dry vocal front distribution
  unchanged within tolerance.
- Full suites green; feature-off bit-identical (regression anchor).
- A/B listening note (protocol `evaluation.md` §6): a reverb-heavy
  ballad and a dry-produced pop track. Listen for: vocal sits front and
  intelligible with its tail wrapping (the win), no gated/chopped tail
  artifacts at phrase ends (the dereverb failure mode), mono/stereo
  downmix stays acceptable (wet stems fold back per phase 4 rules).

## Out of scope

- Non-vocal stems (later slice via the expansion lanes above,
  harness-gated).
- Dereverb as a *repair* feature (roadmap 2.4's denoise/de-echo cleanup
  use) — here the wet output is a spatial source, not discard.
- Bundling model weights (GPL: runtime download only, per KB notes).
- Any change to the duck, panner, or send DSP.

## Done when

- Harness report + null test + measurement tables + listening note in
  `docs/plans/mixing/phase12_report.md`.
- Wet stems appear end-to-end (CLI render, API project flow, web matrix,
  manifest round-trip) with the feature on; bit-identical with it off.
- Knowledge base updated: roadmap 2.4 marked partially realized, model
  registry entry recorded per KB conventions.
- Test-count baseline in the README updated (record before/after).
