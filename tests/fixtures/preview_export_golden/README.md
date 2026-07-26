# preview/export golden-diff fixtures

`web_bed_metrics.json` is generated, not hand-written. It is the web
preview engine's measured loudness/true-peak/per-channel-RMS for the fixed
deterministic bed both `tests/test_preview_export_golden.py` (Python side)
and `web/scripts/render-preview-golden.mjs` (web side) render — see
`docs/contracts/preview_export_parity.md` §5.

Regenerate after any change to the bed, the mastering config in
`test_preview_export_golden.py::_mastering_config`, or
`web/src/features/projects/previewGraph.ts`:

```bash
cd web && npm run golden:render
```

Then re-run `python3 -m pytest tests/test_preview_export_golden.py -m perf`
to confirm the cross-engine tolerances in the contract doc still hold. Do
not hand-edit this file.
