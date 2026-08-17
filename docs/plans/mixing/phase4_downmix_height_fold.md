# Phase 4 — Height fold-down in the stereo downmix

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 0 (its downmix-null table quantifies the loss per preset).
Independent of phases 1–3, but run after 3 so listening checks use the
new sends.

## Goal

`itu_downmix_stereo` / `itu_downmix_mono`
(`packages/core/src/utils.py` → `dsp-core` `itu_downmix_*` kernels)
exclude height channels because BS.775 predates them. With presets
routing the majority of cymbal/air energy overhead (phase 0 table has the
exact fractions), the written stereo downmix loses that content, and it
diverges from the stereo *render* path (`fold_route_to_stereo`), which
does fold heights. Fix: fold heights into the downmix with explicit,
documented coefficients, consistent across both stereo paths.

## Design decision (make it, document it, then implement)

BS.775-4 defines no height coefficients. Adopt the common re-render
practice: TFL/TFR fold into FL/FR, TBL/TBR fold into SL/SR (then through
the existing surround coefficient), each at a fixed height coefficient
`k_h`. Default `k_h = 1/√2` (−3 dB), configurable like
`surround_downmix_coeff` (`config.height_downmix_coeff`, manifest key in
the same block, CLI flag following the existing pattern). Record the
choice and its rationale in
`docs/standards/spatial_layouts_bs775_bs2051.md` (new subsection: heights
are outside BS.775; this is a project convention aligned with Atmos
re-render behavior), and check
`docs/standards/dolby_atmos_profile.md` for any constraint on downmix
metadata that must stay consistent.

## Deliverables

1. `dsp-core` `itu_downmix_stereo`/`itu_downmix_mono` kernels: accept
   TFL/TFR/TBL/TBR with the height coefficient. Mono row: heights enter
   through the same fold then the existing mono weights.
2. `packages/core/src/utils.py`: `_DOWNMIX_SOURCES` gains the four height
   labels; docstrings updated (they currently say "excluded per standard"
   — now they must state the convention and point to the standards doc).
3. Config/manifest/CLI plumbing for `height_downmix_coeff` following the
   exact pattern of `surround_downmix_coeff` (grep its full path:
   config → manifest block → CLI flag → apps/api exposure if any).
4. Callers audit: `stem_pipeline.py::_write_downmix`, `pipeline.py`'s
   downmix path, loudness/true-peak measurement on the downmix, and any
   web/API preview downmix (the preview's stereo-downmix monitoring path
   in the wasm engine — if it implements its own fold, it must match;
   check `docs/contracts/preview_export_parity.md` and re-hash if
   touched).
5. Consistency: add a test asserting the *relative* height treatment of
   `fold_route_to_stereo` (render path) and `itu_downmix_stereo` (downmix
   path) agree in kind — both include heights; exact gains may differ by
   documented design (render path is a pan law renormalized per stem;
   downmix is a level law). State the relationship in the standards doc
   subsection.

## Tests

- Kernel tests (Rust + Python golden): a signal only in TFL/TFR must
  appear in the downmix at `k_h` (not vanish); mono fold likewise;
  existing BS.775 vectors for non-height channels unchanged at default
  coefficients.
- `height_downmix_coeff=0.0` reproduces today's output exactly
  (escape hatch + regression anchor).
- Phase 0 downmix-null measurement re-run: per-preset energy loss table
  before/after appended to the report — Crash/Hi-Hat/height-zone stems
  must no longer lose the majority of their energy.

## Out of scope

- Changing routing presets to compensate (they should not need it).
- 5.1/7.1 intermediate downmixes (only 2.0 and 1.0 exist in this
  codebase today — do not add others).

## Done when

- Full suites green (Python, Rust, web if the preview path was touched).
- Standards doc updated; downmix docstrings point to it.
- Phase 0 report appended with before/after; short A/B listening note on
  one cymbal-heavy track's stereo downmix.
