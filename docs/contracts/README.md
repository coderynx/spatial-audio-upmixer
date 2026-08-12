# Signed Contracts — Index and Rules for AI Agents

This directory defines the **signed contracts** that keep the web preview
(`apps/web/`, `apps/api/`) and the core export pipeline (`packages/core/`)
producing equivalent audio. It exists because of a specific architectural
fact: **the frontend does not send audio to the backend to preview an
upmix.** It renders the mix locally and plays it back live.

Until the Rust port, it did that by re-implementing the DSP as a Web Audio
graph, and this directory existed to keep two implementations in agreement.
It no longer does: preview and export both run
[`packages/dsp`](../../packages/dsp/AGENTS.md) — through WebAssembly in the
browser worklet, through PyO3 in the pipeline. What these documents now
govern is the much smaller surface that can still diverge: build provenance
of the committed wasm artifact, the constants the browser is served, and the
handful of behaviours that differ by nature (live-parameter latency, seek
warm-up, correction staleness).

## Preview-as-reference principle

**The preview is first-class, not a rough approximation to be second-guessed
against the export.** A user judges whether an upmix is good — balance,
spatial placement, loudness, tone — by listening to the preview. That
judgment is only trustworthy if the preview is provably close to what
export will actually deliver. Treat preview/export divergence as a **bug in
the preview or the export**, not as an acceptable cost of the preview being
"just a preview." Where a difference is unavoidable — the preview must respond to a control
while the export renders offline — the allowed gap is bounded and stated
explicitly in the contract, not left implicit.

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

The tunable DSP constants are **single-sourced from core** and served to the
web (`preview_export_parity.md` §2) — the web has no second copy to keep in
sync. Changing a served constant means:

1. Update the Python source (`packages/core/src/...`) — the only place the value lives.
2. Update `preview_export_parity.md` if the change affects what that
   document describes.
3. Update the web test fixture
   `apps/web/src/features/projects/engineConstants.fixture.ts` to match (it
   feeds the golden harness), then re-run the golden render diff
   (`packages/core/tests/test_preview_export_golden.py`, which runs by
   default — refresh its fixtures with `npm run golden:render` first) until
   green.

Any change under `packages/dsp` also needs the browser artifact rebuilt
(`npm run build:wasm` from `apps/web/`) — it is committed, not built on
install, so a stale one ships a different algorithm to the browser. The
golden render is what catches that. Do not silence, skip, or loosen a
tolerance to make a failing test pass; fix the cause, or if the contract
itself should change, follow the steps above.

If you are an AI agent asked to change DSP behavior anywhere in
`packages/core/src/` or in the preview graph, **read `preview_export_parity.md`
first** to find out whether the value you're touching is contracted, and if
so, at which parity tier — that determines whether the other side must also
change and how tightly re-verification must hold.

## Single-source constants mechanism

The mechanism (`engine_constants()`, what is served, what stays in Rust) is
specified once, in [`preview_export_parity.md` §2](preview_export_parity.md).
Signal-level equivalence is the job of the golden render
(`packages/core/tests/test_preview_export_golden.py`), which runs by default
and is described in that document's §4.
