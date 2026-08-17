# Phase 3 — Wire velvet decorrelation into the send paths, keep parity

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 2 merged. This is the largest phase; it is the one that
changes what listeners hear.

## Goal

Replace the comb-prone delay-blend sends with the phase 2 velvet pair in
every mixing send path, symmetrically per channel pair, in both the
Python/PyO3 export path and the Rust streaming preview path — and keep the
two bit-identical per the parity contract.

## Call sites to convert (complete list — verify by grep before starting)

1. `packages/core/src/separation/stem_router.py` — `StemRouter.route`:
   `surround_L/R` and `height_L/R` currently built from
   `diffuse_send(..., delay_ms=SURROUND_HAAS_DELAY_MS_*)` etc. Replace
   with the velvet pair (distinct seeds per zone class — one pair for
   surround, one for height — so surround and height sends of the same
   stem are also mutually decorrelated). The surround highpass and height
   `elevation_eq` pre-filters stay.
2. `packages/core/src/upmix/multichannel.py` — `MultichannelUpmixer`:
   every `diffuse_send` / `haas_decorrelate` use. This also fixes the
   one-sided-delay bias: both sides of each derived pair get their own
   velvet filter; no side is a plain undelayed copy while the other is
   delayed. Preserve the derivation topology (SL from FL, BL from SL, …)
   — only the decorrelation operator changes.
3. `packages/dsp/crates/dsp-core/src/stream/routing.rs` (and
   `stream/params.rs`, `stream/measure.rs` as needed) — the streaming
   engine's equivalent sends, using the streaming form of the kernel with
   carried state. `stream_equivalence.rs` must keep proving
   offline == streamed.
4. `packages/core/src/utils.py` — after conversion, decide per the
   repo's dead-code rule: `diffuse_send`/`haas_decorrelate` wrappers and
   the Rust kernels they call are removed if nothing references them, kept
   only if something still does. Search all five packages including tests.

## Constants and parity surface

- Retire `SURROUND_HAAS_DELAY_MS_*`, `HEIGHT_HAAS_DELAY_MS_*`,
  `DIFFUSE_SEND_BLEND` from the public constants surface and publish the
  new kernel constants (seeds, length, taps, mix) in their place:
  `stem_router.py` module level → apps/api engine-constants endpoint
  (`apps/api/src/features/system/service.py`) →
  `apps/web/src/features/projects/engineParams.ts` and
  `engineConstants.fixture.ts`. Update `masteringProfiles.ts` if it
  references the old names (grep found it does).
- Update `docs/contracts/preview_export_parity.md`: ledger entry, new
  contract signature, and the golden-vector cross-check (phase 2's golden
  vector must produce identical output through PyO3 and wasm).

## Validation

- Re-run the phase 0 measurement kit; the report's send-response and
  downmix-null tables must show: worst per-channel notch depth improved
  from ~−20 dB to within ±1.5 dB band deviation, fold-down ripple within
  ±1 dB. Append before/after tables to `docs/plans/mixing/phase0_report.md`.
- Full Python suite green; `cargo test`; `apps/web`: `npm test`,
  `npm run build`, and `npm run bench:engine` — report per-quantum cost
  before/after; must stay within the 2.67 ms budget with the usual
  headroom.
- A/B listening note (protocol:
  `~/Projects/upmixer-knowledge/techniques/evaluation.md` §6): one dense
  rock/pop track and one sparse acoustic track, 7.1.4 render + its stereo
  downmix, old vs new sends. Listen specifically for (a) loss of the
  hollow/phasey surround character, (b) unchanged front imaging, (c) no
  new artifacts on separation bleed in surrounds (the sends run
  post-separation precisely so artifacts do not multiply — confirm that
  still holds).

## Out of scope

- Gain-table / preset changes (phases 4–5). Total routed energy per stem
  must be unchanged (the energy renormalization in `StemRouter.route`
  guarantees this — assert it in a test).
- The `MultichannelUpmixer` center derivation (phase 6).
- Binaural/transaural renderers (their own decorrelation lives elsewhere;
  do not touch).

## Done when

- All validation above green and reported with numbers.
- Old kernels/constants either removed everywhere or their remaining
  users listed in the PR with justification.
- Parity contract re-hashed; web fixture and endpoint serve the new
  constants; preview and export null against each other on a test render
  (existing parity test infrastructure — find and extend it).
