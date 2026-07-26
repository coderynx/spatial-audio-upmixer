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

A contract in this directory is **signed**: `preview_export_parity.md`
pins a `contract_signature()` hash computed from the real constants on both
sides (see below). Changing anything the contract covers means:

1. Update the Python source (`upmixer/...`).
2. Update the TypeScript source (`web/src/...`).
3. Update the constants catalog / tier / threshold in
   `preview_export_parity.md` and regenerate the pinned signature (see that
   file's "Regenerating the signature" section).
4. Re-run both signature tests (`tests/test_contract_parity.py`,
   `web/src/lib/contract.test.ts`) and the golden render diff
   (`tests/test_preview_export_golden.py`) until green.

**Changing only one side is a contract violation.** It is not a style
preference — `contract_signature()` is computed from the actual constant
values imported from each side's real source modules (never re-typed
literals), so an unmatched change makes the corresponding signature test
fail on whichever side lags. Do not silence, skip, or loosen a tolerance to
make a failing test pass; fix the divergent side, or if the contract itself
should change, follow all four steps above.

If you are an AI agent asked to change DSP behavior anywhere in `upmixer/`
or in the preview graph, **read `preview_export_parity.md` first** to find
out whether the value you're touching is contracted, and if so, at which
parity tier — that determines whether the other side must also change and
how tightly re-verification must hold.

## Signature mechanism

`upmixer/contract.py::contract_signature()` and `web/src/lib/
contract.ts::contractSignature()` each build a normalized, sorted-key
structure from their real constants and hash it (sha256 over stable JSON).
The two functions are mirrors of each other by construction — same key set,
same normalization — so if either side's underlying constants change, its
own signature changes. Both are asserted against the single value pinned in
`preview_export_parity.md`. This does **not** prove the two engines compute
the same signal — only that the documented shared constants are unchanged
on whichever side ran the test. Actual signal-level equivalence is the job
of the golden render diff (`tests/test_preview_export_golden.py`), described
in `preview_export_parity.md`'s tolerance-thresholds section.
