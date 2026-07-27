# preview/export golden-diff fixtures

Both files here are generated, not hand-written, by
`web/scripts/render-preview-golden.mjs` — see
`docs/contracts/preview_export_parity.md` §5.

- `web_bed_metrics.json`: the web preview engine's measured
  loudness/true-peak/per-channel-RMS for the fixed deterministic bed after
  `buildMasteringGraph`'s EQ/compressor/bass chain — diffed against
  `test_cross_engine_golden_diff` (Python side: `_render_python_bed`).
- `web_binaural_metrics.json`: the same bed's measured
  loudness/true-peak/per-ear-RMS after `buildBinauralGraph`'s ambisonic
  encode/decode/voicing plus the collapse-stage loudness gain and soft-limit
  (Studio profile) — diffed against `test_cross_engine_binaural_golden_diff`
  (Python side: `_render_python_binaural`, i.e. `render_binaural_delivery`).

Regenerate both after any change to the bed, the mastering config in
`test_preview_export_golden.py::_mastering_config`/`_binaural_config`, or
`web/src/features/projects/previewGraph.ts`:

```bash
cd web && npm run golden:render
```

Then re-run `python3 -m pytest tests/test_preview_export_golden.py -m perf`
to confirm the cross-engine tolerances in the contract doc still hold. Do
not hand-edit either file.
