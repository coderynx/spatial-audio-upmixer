# Phase 6 — Dither and export-path correctness

Read `docs/plans/mastering/README.md` first for context and ground rules.
Requires phase 0 (its quantization-floor audit is the evidence base and
supplies the acceptance fixture). Independent of phases 2–5.

## Goal

The export path quantizes without dither and its ordering guarantees are
implicit. Make the tail of the pipeline spec-correct:

1. **TPDF dither at bit-depth reduction.** `io/writer.py` passes float64
   to libsndfile at `PCM_24`/`PCM_16`; the reduction is undithered.
   Add ±1 LSB TPDF dither at the target bit depth, applied to integer
   PCM subtypes only (float subtypes and lossy codecs skip it), exactly
   once, as the last operation before the samples leave the process.
   Optional simple noise shaping (first/second-order error feedback) as
   a config choice, default plain TPDF — hand-rolled in `dsp-core` with
   golden tests, per the no-new-deps rule.
2. **Ordering guarantee, made explicit.** The chain already does the
   right things in the right order — resample happens before mastering
   (`pipeline.py`: resample → master → write), so the limiter sees the
   final rate and the TP ceiling holds at delivery rate. Pin it: a test
   that fails if mastering ever runs before resampling or if any gain
   is applied after dither, and a short "export tail" section in
   `docs/standards/loudness_dsp_bs1770.md` stating the contract
   (master at output rate → quantize+dither last → nothing after).
3. **SRC quality audit.** `_resample_channels` uses
   `scipy.signal.resample_poly` (windowed-sinc polyphase — adequate).
   Measure it once: sweep + alias-image levels for 44.1→48 and 96→48,
   numbers in the phase report. Upgrade only if the images clear
   −120 dBFS worst-case expectations; otherwise document as verified.

## Design decisions (make, document, implement)

- Dither lives in the writer (`io/writer.py` + a `dsp-core` kernel), not
  in `MasteringChain` — the chain's contract ends at a float bed +
  measurements; only the writer knows the target subtype. The ADM/BWF
  writer (`io/adm_writer.py`) uses the same helper for its PCM payload.
- Dither noise is below the −1 dBTP ceiling by construction (±1 LSB at
  24-bit ≈ −138 dBFS) — no re-measurement of TP after dither; state this
  in the standards doc rather than re-running the meter.
- Deterministic renders: seed the dither RNG from config (fixed default
  seed) so byte-identical re-renders stay byte-identical — the same
  reproducibility discipline the velvet decorrelator seeds follow.
- The web preview plays float32 — dither does not apply; note in the
  parity contract §3 that the export's dithered tail is a deliberate,
  inaudible-by-construction divergence (it is below the wasm path's own
  float32 quantization).

## Deliverables

1. `dsp-core` dither kernel (TPDF + optional shaping), PyO3 binding,
   writer integration for `PCM_16`/`PCM_24` across `AudioWriter` and
   `AdmBwfWriter`; config key (`output_dither`: `off`/`tpdf`/`shaped`,
   default `tpdf` for integer subtypes).
2. Ordering pin test; standards-doc "export tail" section; parity §3
   note.
3. SRC audit numbers in the phase report.

## Validation

- Golden: 16-bit export of a −90 dBFS fade — undithered truncation
  distortion visible in the phase 0 fixture, gone (replaced by flat
  noise floor) with TPDF; spectrum plots in the report.
- Determinism: two identical renders byte-compare equal.
- 24-bit default output: difference vs float master bounded by the
  dithered LSB, and no test regression anywhere (dither must not break
  golden comparisons — widen those tolerances deliberately, per test,
  never globally).
- Full suite; no wasm/bench impact (export-only phase).
