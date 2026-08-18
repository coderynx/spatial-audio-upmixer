# Phase 2 — Limiter channel-linking policy

Read `docs/plans/mastering/README.md` first for context and ground rules.
Requires phase 0 (the LFE-link duck-depth audit is the evidence base and
regression yardstick).

## Goal

`mastering/limiter.rs::lookahead_limit` derives one gain curve from the
envelope maximum across **all** channels — LFE included — and applies it
everywhere. Two consequences:

1. An LFE-only peak ducks the whole bed (phase 0 quantifies by how much).
   With the `cinema` bass profile (50% of the low bus into LFE) or a hot
   LFE trim, low-frequency events pump the mains.
2. There is no policy option at all: fully-linked is the only behavior.
   Immersive practice (FLUX Elixir's channel-link control, Pulsar P21
   Atlas's shared-GR-with-LFE-excluded design, McDSP's grouped surround
   topology) treats "how linked" as the one limiter decision that matters
   for spatial material.

## Design decisions (make, document, implement)

- **LFE leaves the shared link.** The mains (every non-LFE channel) keep
  the single shared gain curve — that is what preserves imaging and it
  stays the default. LFE gets its own independent gain curve from its
  own envelope, same look-ahead/release machinery, same ceiling. True
  peak compliance is unchanged (every channel is still capped); only the
  *coupling* goes. BS.1770-5 TP scanning still covers all channels — the
  detection change is which envelope feeds which gain curve, not what is
  measured.
- **Partial link (optional, default 1.0 = current behavior for mains).**
  A `limiter_link` scalar [0..1]: the applied per-channel gain is the
  geometric blend `shared^link * own^(1-link)`. At 1.0, identical to the
  shared curve; below 1.0, transient GR localizes to the channel that
  needs it while sustained reduction stays shared. Ship the parameter
  only if the phase 0 duck-depth number justifies it — if LFE unlinking
  alone closes the audible problem, log the decision and keep the knob
  out (explicit-control contract: no speculative parameters).
- **GR telemetry** (from phase 0's fields) is emitted by both the offline
  and streaming limiter so phase 3 can meter it: per-block max GR for
  the mains curve and the LFE curve separately.

## Deliverables

1. `dsp-core`: `lookahead_limit` and `stream/master.rs::StreamingLimiter`
   grow the LFE-separate gain path (LFE index is already threaded through
   `Bed` callers). One implementation of the gain computer, two envelope
   feeds — do not fork the algorithm.
2. PyO3 + wasm bindings updated; `LookAheadLimiter.process`
   (`mastering/limiter.py`) passes the LFE index it already knows from
   the chain.
3. Parity: contract §1 row unchanged (same shared function), §2 gains
   nothing unless `limiter_link` ships (then it is served through
   engine constants and `engineParams.ts`). Rebuild wasm.
4. `docs/standards/loudness_dsp_bs1770.md` "LFE and true-peak" section
   updated: LFE is TP-capped independently, excluded from the mains'
   gain link, with the FLUX/Pulsar practice citation.

## Watch out for

- The gain-modulation edge dilation (`FIR_MARGIN_SAMPLES`) must apply to
  *both* gain curves — the fresh-ISP recombination argument in
  `limiter.rs`'s docstring holds per curve.
- The streaming limiter's look-ahead queue length must not change, or
  the engine's emit horizon moves (P1 in the parity contract).
- `render_binaural_delivery` and the transaural path run their own
  limiter over two channels with no LFE — they must be unaffected
  (no LFE index → identical output; pin with a test).
- Bass-management interaction: after phase 2, a `split`-mode LFE that
  limits independently changes the LF sum only within GR windows. The
  commutation invariant (§1 of the parity contract) already excludes the
  limiter — it runs last — but say so in the phase report.

## Validation

- Golden tests: LFE-only peak fixture (mains untouched, LFE capped),
  mains-only peak fixture (bit-identical to current behavior), mixed
  fixture (phase 0's duck-depth measurement re-run showing mains GR at
  or near zero).
- `stream_equivalence.rs`: streaming vs offline on all three fixtures.
- Full suite + `npm run bench:engine` (second gain curve costs one more
  envelope pass; budget report required).
- A/B listening note on the LFE-heavy programme from phase 0.
