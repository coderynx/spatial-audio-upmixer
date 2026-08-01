# Signed Contracts — Index and Rules for AI Agents

This directory defines the **signed contracts** that keep the web preview
(`apps/web/`, `apps/api/`) and the core export pipeline (`packages/core/`)
producing equivalent audio. It exists because of a specific architectural
fact: **the frontend does not send audio to the backend to preview an
upmix.** It re-implements the mixing, spatial routing, mastering, and
binaural rendering stages as a Web Audio graph in the browser
(`apps/web/src/features/projects/useStemPreview.ts`,
`apps/web/src/features/projects/masteringProfiles.ts`, `apps/web/src/lib/spatial.ts`)
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

The tunable DSP constants in `preview_export_parity.md`'s catalog are
**single-sourced from core** and served to the web (see the mechanism section
below and that document's §4) — the web has no second copy to keep in sync.
Changing a served constant means:

1. Update the Python source (`packages/core/src/...`) — the only place the value lives.
2. Update the constants catalog / tier / threshold in
   `preview_export_parity.md` to describe the new value.
3. Update the web test fixture
   `apps/web/src/features/projects/engineConstants.fixture.ts` to match (it
   feeds the golden harness), then re-run the golden render diff
   (`packages/core/tests/test_preview_export_golden.py`, which runs by
   default — refresh its fixtures with `npm run golden:render` first) until
   green.

For a DSP change that alters the *realization* on only one side (Tier 2/3, or
a web-local structural constant), the golden render diff is what holds the
result within tolerance. Do not silence, skip, or loosen a tolerance to make
a failing test pass; fix the divergent side, or if the contract itself should
change, follow the steps above.

If you are an AI agent asked to change DSP behavior anywhere in
`packages/core/src/` or in the preview graph, **read `preview_export_parity.md`
first** to find out whether the value you're touching is contracted, and if
so, at which parity tier — that determines whether the other side must also
change and how tightly re-verification must hold.

## Single-source constants mechanism

The full mechanism (`engine_constants()`, what is served vs. kept web-local,
and why there is no value cross-check to run) is specified once, in
[`preview_export_parity.md` §4](preview_export_parity.md#4-single-source-of-truth-for-the-constants-3).
Signal-level equivalence — the thing that actually matters once constants
can no longer drift — is the job of the golden render diff
(`packages/core/tests/test_preview_export_golden.py`), which runs by
default and is described in that document's tolerance-thresholds section
(§5).
