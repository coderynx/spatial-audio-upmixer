# DSP stem-cleanup baseline

Date: 2026-08-28

This report freezes the evidence available when the model-backed phase-fix
and per-stem debleed paths were retired. The acceptance contract is
[the evaluation harness](../evaluation_harness.md): SDR, fullness, and
bleedless must be considered together, with listening and runtime gates.

## Model-backed cost

The existing M3 Pro/MPS measurements in
[`docs/plans/mlx/phase0_report.md`](../plans/mlx/phase0_report.md) recorded the
Kim FT2 bleedless reference model at 24.66 seconds warm for 60 seconds of
audio after the compiled-RoPE optimization. Its added RTF was therefore
0.411, over four times the `0.1` exception limit. Linear five-minute cost is
about 123 seconds, before any per-stem debleed inference.

| Retired pass | Quality baseline | Runtime decision |
| --- | --- | --- |
| Kim FT2 phase reference | No licensed, aligned raw/AI corpus result is stored in this repository | Reject: measured RTF 0.411 |
| Bleed Suppressor v1 | Unavailable; the old output-selection contract was ambiguous | Reject: no qualifying latency report |
| Gabox Denoise-Debleed | Unavailable; the old output-selection contract was ambiguous | Reject: no qualifying latency report |

The migration plan explicitly forbids repairing those retired paths merely
to complete a baseline. Aufr33's normal denoise checkpoint is not retired;
it independently serves optional wet-stem denoising.

## Corpus and listening status

No licensed stem-cleanup corpus or listening assets are present in this
repository. The synthetic corpus is valid for deterministic engineering
checks but is not a substitute for musical material. Consequently:

- no raw-vs-AI real-music score is claimed;
- no listening preference is claimed;
- no held-out musical subset was tuned against; and
- DSP cleanup must remain default-off.

The admissible baseline winner remains the raw `becruily_deux` output.
