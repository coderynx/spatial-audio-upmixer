# Phase 3 — Preview loudness metering and loudness-matched A/B

Read `docs/plans/mastering/README.md` first for context and ground rules.
Requires phases 0 (measurement definitions) and 2 (GR telemetry). Phase 1
is not required but its target presets make the meter's target line
meaningful — run after it if possible.

## Goal

The preview already *computes* everything a mastering meter shows —
BS.1770 integrated + TP in the measurement pass, per-block levels in
`stream/meters.rs`, gain reduction inside the limiter/compressor — and
displays none of it. The UI meters are RMS/peak strips
(`useStripMeterLoop.ts`); measured LKFS lives privately in
`audioEngine.ts`. Meanwhile the master-bypass button (B key,
`MasterBypassButton.tsx`) compares mastered vs unmastered at unmatched
loudness, which biases every judgment toward whichever side is louder.

Deliverables:

1. **Live loudness meters on the master strip**: momentary (400 ms) and
   short-term (3 s) LUFS at the emit position, integrated LUFS and max TP
   from the measurement pass (already computed — surface, don't
   recompute), and the resolved target as a meter scale marker. PLR/PSR
   readout derived from the same values.
2. **Gain-reduction meters**: bus compressor GR and limiter GR (mains
   and LFE curves separately, from phase 2's telemetry), rendered on the
   master strip the way the stem strips render level.
3. **Loudness-matched bypass**: bypassing the master chain applies a
   compensating scalar so both states play at matched loudness.
   Match on the two states' integrated loudness: the mastered side's is
   already measured (P3 pass); the bypassed side needs one more
   measurement pass over the unmastered programme — run it lazily on
   first bypass, cached until the mix edits invalidate it (same
   signature discipline as the reference-match recompute, D13). While
   the bypass-side measurement is pending, show the same "calibrating"
   affordance the preview already uses rather than playing unmatched.

## Where things land

- `dsp-core`: momentary/short-term rolling meters in `loudness_stream.rs`
  (the 400 ms / 75% machinery exists in `IntegratedLoudnessMeter`;
  extract the block accumulator rather than duplicating it), per-block
  GR taps on `StreamingCompressor`/`StreamingLimiter`, all read out
  through the existing `Meters` block (`stream/meters.rs`) so the wasm
  boundary stays one flat float array per render.
- Meters measure at the **emit position** like every existing meter —
  never the render horizon (`stream/meters.rs` module docstring rule).
- Web: master strip UI in `MixerView.tsx` + a loudness readout in the
  preview panel; meter scales follow `docs/web_ui_design.md` tokens.
  No DSP in TypeScript — the worklet ships numbers, the UI draws them.
- API: nothing new; all values are preview-side.

## Watch out for

- **Budget.** Momentary/short-term add one K-weighted power accumulation
  per channel per block. `npm run bench:engine` before/after is
  mandatory; the K-weighted path may be shareable with the measurement
  pass only when one is running — donate the phase report a paragraph on
  what the meters cost.
- The bypassed-side measurement must fork the engine with
  `bypass_mastering` set — the flag exists (`params.bypass_mastering`) —
  and must respect D29's flush ordering (measure what was just set, not
  what the worklet had).
- Loudness matching changes *monitoring* gain only — it must never leak
  into the exported master or the stored manifest. Keep it in
  `audioEngine.ts`'s output-gain path (`master_gain` ramp), clearly
  separated from `master.output_gain`'s persisted meaning.
- Meter ballistics (peak-hold, decay) follow the existing
  `meterScale.ts`/`useStripMeterLoop.ts` conventions; LUFS meters are
  numeric + bar per EBU Tech 3341 mode conventions (M/S/I labels).

## Validation

- Rust: meter unit tests against the offline measurements (a rendered
  programme's max momentary/short-term from the streaming meter matches
  the phase 0 offline kit within tolerance); GR taps pinned on the
  phase 2 fixtures.
- Web: vitest for the readout formatting and bypass-compensation state
  machine; `npm run build` + `npm test`.
- `npm run bench:engine` with meters on — all three budget rows.
- A/B listening note: bypass toggle at matched loudness on both test
  programmes (this is the phase that makes every later listening note
  trustworthy — say so in the report).
