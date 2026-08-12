# preview/export golden-diff fixtures

The `web_*_metrics.json` files here are generated, not hand-written, by
`apps/web/scripts/render-preview-golden.mjs` — see
`docs/contracts/preview_export_parity.md` §5.

- `web_bed_metrics.json`: the web preview engine's measured
  loudness/true-peak/per-channel-RMS for the fixed deterministic bed after
  `buildMasteringGraph`'s EQ/compressor/bass chain — diffed against
  `test_cross_engine_golden_diff` (Python side: `_render_python_bed`).
- `web_reference_match_metrics.json`: the same bed and mastering config,
  plus reference matching as mastering step 0 — diffed against
  `test_cross_engine_reference_match_golden_diff` (Python side:
  `_render_python_reference_match`; Ledger D21). LFE is excluded from this
  diff's channel comparison — see `test_preview_export_golden.py`'s module
  docstring.
- `web_binaural_metrics.json`: the plain bed's measured
  loudness/true-peak/per-ear-RMS after `buildBinauralGraph`'s ambisonic
  encode/decode/voicing plus the collapse-stage loudness gain and soft-limit
  (Studio profile) — diffed against `test_cross_engine_binaural_golden_diff`
  (Python side: `_render_python_binaural`, i.e. `render_binaural_delivery`).

`reference_match_fir.wav`/`reference_match_meta.json` are different: they're
gitignored (`*.wav`) inputs the *Python* side exports for the *web* harness
to read (the reverse of the flow above), since reference matching has no
shipped named-profile FIR asset the way `eq_fir/*.wav` does — every real one
is computed per-project. Regenerate them via `REGENERATE_GOLDEN=1
python3 -m pytest tests/test_preview_export_golden.py::test_python_reference_match_metrics_golden -s`
(see `_write_reference_match_fixture`) **before** running `npm run
golden:render` for the first time on a fresh checkout, same as `eq_fir/*.wav`
needing `scripts/build_eq_filters.py` run once first.

Regenerate the `web_*_metrics.json` files after any change to the bed, the
mastering config in `test_preview_export_golden.py::_mastering_config`/
`_reference_match_config`/`_binaural_config`, or anything under
`packages/dsp` — the last case also needs the wasm artifact rebuilt, since
that is what the harness loads:

```bash
cd apps/web && npm run build:wasm && npm run golden:render
```

Then re-run `uv run pytest packages/core/tests/test_preview_export_golden.py`
to confirm the tolerances in the contract doc still hold. Do not hand-edit
any of these files.
