# CLI Agent Guide

Read the root guide. `src/` owns argument parsing, flag application, and
manifest-run orchestration only; processing behavior stays in core and is
reached through its public API.

Parameter precedence is: CLI flags > manifest values > profile defaults >
`UpmixConfig` defaults.

```bash
uv run upmixer input.wav output.wav --format 7.1.4
uv run upmixer --manifest examples/atmos_music.yaml
```
