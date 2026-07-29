# Signed Contracts — Index and Rules for AI Agents

This directory defines the **signed contracts** that keep the web preview
(`web/`, `upmixer_web/`) and the core export pipeline (`upmixer/`) producing
equivalent audio. It exists because of a specific architectural fact: **the
frontend does not send audio to the backend to preview an upmix.** It
re-implements the mixing, spatial routing, mastering, and binaural rendering
stages as a Web Audio graph in the browser
(`web/src/features/projects/useStemPreview.ts`,
`web/src/features/projects/masteringProfiles.ts`, `web/src/lib/spatial.ts`)
and plays that back live. Export renders the same project through
`upmixer.pipeline.UpmixPipeline` / `upmixer.separation.stem_pipeline
.StemUpmixPipeline`. Two independent implementations of the same DSP only
stay in agreement if something enforces it — that is what this directory is.

## Preview-as-reference principle

**The preview is first-class, not a rough approximation to be second-guessed
against the export.** A user judges whether an upmix is good — balance,
spatial placement, loudness, tone — by listening to the preview. That
judgment is only trustworthy if the preview is provably close to what
export will actually deliver. Treat preview/export divergence as a **bug in
the preview or the export**, not as an acceptable cost of the preview being
"just a preview." Where an exact match is currently infeasible (browser
`BiquadFilterNode` vs. SciPy `sosfilt`, for instance), the allowed gap is
bounded and stated explicitly in the contract, not left implicit.

## The two contracts

- **[`preview_export_parity.md`](preview_export_parity.md)** — the master
  contract. Stage-by-stage pipeline map naming the Python and TypeScript
  implementation of each stage, the full canonical-constants catalog, parity
  tiers, golden-render tolerance thresholds, and the pinned contract
  signature.
- **[`../standards/spatial_audio_engine.md`](../standards/spatial_audio_engine.md)**
  — the pre-existing, narrower contract for the binaural rendering pass
  specifically (geometry, ambisonic convention, decode filters, voicing
  chain). Still authoritative for that subsystem; cross-linked from, not
  duplicated by, `preview_export_parity.md`.

Both derive numeric requirements from the **industry standards** in
`docs/standards/`: `loudness_dsp_bs1770.md` (BS.1770-5 loudness/true-peak),
`spatial_layouts_bs775_bs2051.md` (BS.775/BS.2051 layouts, LFE, downmix),
`adm_metadata_bs2076.md` and `dolby_atmos_profile.md` (ADM-BWF delivery).
Where a contracted constant exists because a standard requires it (e.g. LFE
−10 dB per BS.775-4 Annex 7, K-weighting coefficients per BS.1770-4 Annex 1),
`preview_export_parity.md` cites that standard, not just the code.

## Change protocol — binding on human and AI contributors

This directory is **cross-checked**: `preview_export_parity.md`'s
constants catalog is verified by comparing the real constants on both
sides directly (see below) — no hash, nothing to regenerate but a JSON
fixture. Changing anything the contract covers means:

1. Update the Python source (`upmixer/...`).
2. Update the TypeScript source (`web/src/...`).
3. Update the constants catalog / tier / threshold in
   `preview_export_parity.md`, then run `npm run constants:dump` (from
   `web/`) to refresh `tests/fixtures/contract/web_constants.json`.
4. Re-run the value cross-check (`tests/test_contract_parity.py`,
   `web/src/lib/contract.test.ts`) and the golden render diff
   (`tests/test_preview_export_golden.py`, which runs by default — refresh
   its fixtures with `npm run golden:render` first) until green.

**Changing only one side is a contract violation.** It is not a style
preference — `tests/test_contract_parity.py` loads the constants each side
actually computes (never re-typed literals) and diffs them directly, so an
unmatched change fails that test and names the specific diverging key(s).
Do not silence, skip, or loosen a tolerance to make a failing test pass; fix
the divergent side, or if the contract itself should change, follow all
four steps above.

If you are an AI agent asked to change DSP behavior anywhere in `upmixer/`
or in the preview graph, **read `preview_export_parity.md` first** to find
out whether the value you're touching is contracted, and if so, at which
parity tier — that determines whether the other side must also change and
how tightly re-verification must hold.

## Value cross-check mechanism

`upmixer/contract.py::canonical_constants()` and `web/src/lib/
contract.ts::canonicalConstants()` each build a normalized structure from
their real constants — same key set by construction. `web/scripts/
dump-constants.mjs` (`npm run constants:dump`) dumps the TypeScript side's
structure to the committed `tests/fixtures/contract/web_constants.json`;
`tests/test_contract_parity.py` loads that fixture and diffs it directly
against the live Python structure (normalizing both through
`upmixer.contract._canonical_value` so number-formatting differences
between the two languages never cause a false mismatch). If either side's
underlying constants change without the other following, the diff names
the exact diverging key. This does **not** prove the two engines compute
the same signal — only that the documented shared constants agree. Actual
signal-level equivalence is the job of the golden render diff (`tests/
test_preview_export_golden.py`), which runs by default (not opt-in) and is
described in `preview_export_parity.md`'s tolerance-thresholds section.
