# Stem-Mixing Quality — Phase Plans

Goal: fix the signal-quality defects and close the biggest quality gaps in
the stem remix path — the code that mixes separated stems into stereo /
5.1 / 7.1.4 beds: `packages/core/src/separation/stem_router.py`,
`stem_placement.py`, `stem_rebalance.py`, `upmix/multichannel.py`,
`utils.py` (downmix), and the shared Rust kernels in
`packages/dsp/crates/dsp-core/src/routing/sends.rs` +
`stream/routing.rs`.

Why (findings from the 2026-08-17 mixing audit):

1. `diffuse_send` is a single-tap delay blend (45% dry / 55% wet at
   23–37 ms) — a comb filter with ~−20 dB notches every ~27–43 Hz on every
   surround and height send.
2. `haas_decorrelate` is a pure one-sided delay: identical content delayed
   between paired channels combs again on any BS.775 fold-down, and the
   `MultichannelUpmixer` variant delays only the right side (systematic
   lateral bias).
3. `itu_downmix_stereo` drops height channels entirely, while the presets
   route most cymbal/air energy overhead (Crash TFL 0.80, Hi-Hat TFL 0.72)
   — that content vanishes from the stereo downmix. The stereo *render*
   path (`fold_route_to_stereo`) does include heights, so the same track
   has two different stereo images.
4. LFE sends (Kick 0.85–0.90, Bass 0.75) ignore the +10 dB in-band
   playback gain of the LFE channel while full-band bass also stays in
   FL/FR, and the LFE lowpass has no phase-aligned relationship to the
   unfiltered mains bass.
5. `StemRebalancer` applies `tanh(x/0.95)*0.95` to the entire stem when a
   boost exceeds +3 dB — harmonic distortion at all levels, before the
   mastering chain's look-ahead limiter that already owns overload
   protection.
6. `MultichannelUpmixer` derives C as `0.707*0.5*(FL+FR)` without
   subtracting from FL/FR — correlated +3 dB center build-up.

## Phases

Run in order. Each phase is a self-contained agent task with its own
validation; a phase must be green before the next starts.

| Phase | File | Deliverable |
|-------|------|-------------|
| 0 | `phase0_baseline_measurement.md` | Objective measurement kit (send frequency response, downmix null, LFE energy audit, channel energy accounting) + baseline report. May re-scope later phases — run first. |
| 1 | `phase1_rebalancer_softclip.md` | Remove the full-signal tanh in `StemRebalancer`; headroom handled by the mastering limiter. |
| 2 | `phase2_velvet_decorrelator_kernel.md` | Velvet-noise decorrelator pair kernel in `dsp-core` (+ PyO3 + wasm), downmix-flat by construction, with parity/flatness tests. |
| 3 | `phase3_send_integration_parity.md` | Wire the new kernel into `StemRouter` and `MultichannelUpmixer` sends (symmetric L/R), streaming engine, web preview parity, contract re-hash. |
| 4 | `phase4_downmix_height_fold.md` | Fold heights into the BS.775 stereo downmix; one consistent stereo image across render and downmix paths. |
| 5 | `phase5_lfe_calibration.md` | LFE level policy (+10 dB in-band compensation), Linkwitz-Riley-aligned LFE lowpass, preset send re-scale. |
| 6 | `phase6_multichannel_center.md` | Replace the passive-sum derived center in `MultichannelUpmixer` with coherence-based extraction (subtractive). |
| 7 | `phase7_elevation_eq_band.md` | Optional: retune the elevation EQ toward the ~8 kHz directional band (Blauert) instead of the broad 3 kHz shelf. |

Deferred (not planned as phases; revisit after 0–5 ship and are heard):

- **MDAP/VBAP panner** replacing the raised-cosine spread panner in
  `stem_placement.py` — largest redesign, benefits mostly the "stage"
  preset's point placements.
- **Content-aware / time-varying routing** — roadmap 4.1 in
  `~/Projects/upmixer-knowledge/roadmap.md`; do its archaeology step first.
- **Windowed / BS.1770-weighted energy renormalization** in
  `StemRouter.route` and `_normalize_to_source` — interacts with the
  null-test-audit findings; needs its own measurement pass.

## Ground rules for every phase

- Read the repo root `AGENTS.md` and `packages/core/AGENTS.md` first.
  Comment policy, file-size policy (~400 soft / ~600 hard), and package
  boundaries (web/CLI consume only core's public API; no quality logic in
  the web layer) all apply.
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q`
  must pass before and after every phase (baseline: 846 tests). Phases
  touching `apps/web` also run `npm test` and `npm run build` there.
- **Preview/export parity is a hard constraint.** The surround/height send
  constants (`SURROUND_HAAS_DELAY_MS_*`, `DIFFUSE_SEND_BLEND`) are public,
  served by the apps/api engine-constants endpoint
  (`apps/api/src/features/system/service.py`) and consumed by
  `apps/web/src/features/projects/engineParams.ts` and the wasm streaming
  engine. Any send change lands once in `dsp-core` so the PyO3 export path
  and the wasm preview path stay bit-identical; update
  `docs/contracts/preview_export_parity.md` (ledger entry + contract
  signature) in the same phase. `packages/dsp` golden tests
  (`golden_kernels.rs`, `golden_spatial.rs`, `stream_equivalence.rs`)
  gate the Rust side.
- The preview worklet must stay inside its 2.67 ms/quantum budget
  (overrun = silence, not glitch). Any phase touching the streaming path
  runs `npm run bench:engine` in `apps/web` and reports numbers.
- Routing quality is not SDR-measurable. Every audible change validates
  with (a) the phase 0 measurement kit re-run, and (b) a short A/B
  listening note in the phase report (protocol:
  `~/Projects/upmixer-knowledge/techniques/evaluation.md` §6). The
  separation eval harness is NOT required — no phase here may change
  separation behavior; if one accidentally does, stop and re-scope.
- No new Python or JS dependencies. New DSP is hand-rolled in `dsp-core`
  with golden tests, matching existing kernel style.
- Standards-governed changes (downmix coefficients, LFE behavior) must
  update the matching doc under `docs/standards/` in the same phase and
  cite it from code with at most a one-line pointer.
