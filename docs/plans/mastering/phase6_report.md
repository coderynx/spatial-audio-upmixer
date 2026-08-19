# Phase 6 report — Dither and export-path correctness

Plan: `docs/plans/mastering/phase6_dither_export.md`.
Date: 2026-08-19. Suites: Rust **222 → 231 passed**, Python **1202 → 1215
passed / 45 deselected**. Web untouched (see "Wasm" below).

## What shipped

- `dsp-core/src/dither.rs` — `quantize(channel, bits, mode, seed)`, mapping
  float samples onto the `bits`-deep integer PCM lattice. Three modes: `Off`
  (round to nearest), `Tpdf` (±1 LSB triangular, two SplitMix64 draws), and
  `Shaped` (TPDF plus `(1 - z⁻¹)²` error feedback). 67 lines, no new
  dependency, drawing from the existing `kernels::rng`.
- `dsp-py/src/dither.rs` — `upmixer_dsp.quantize_pcm(channels, bits, mode,
  seed)`, one independent dither stream per channel
  (`channel_seed(seed, index)`).
- `packages/core/src/io/writer.py` — `dither_channels()`, and `write_audio`
  now quantizes as its last act before handing bytes to the container.
  `AdmBwfWriter` calls the same helper for its PCM payload.
- `packages/core/src/resample.py` — the delivery resampler, moved out of
  `UpmixPipeline._resample_channels` and given a proper anti-imaging filter
  (audit below).
- `UpmixConfig.output_dither` (`off`/`tpdf`/`shaped`, default `tpdf`) and
  `output_dither_seed` (fixed at 20260819); manifest key `format.dither`,
  bounded in `manifest/validate.py`'s choices table.
- `docs/standards/loudness_dsp_bs1770.md` §"Export tail" and parity contract
  §3 entry **P5**.

## Why the lattice trick works

The kernel returns *normalized* floats that are exact multiples of
`2^-(bits-1)`, not integers, and the writer hands those straight to
libsndfile. That is safe because libsndfile scales float→PCM by `2^(bits-1)`
and truncates toward −∞: a value already on the lattice multiplies to an exact
integer, so the truncation is a no-op and the delivered codes are the ones the
dither chose. Verified bit-for-bit through WAV and FLAC at 16 and 24 bit
(`test_nothing_scales_the_bed_after_the_quantizer`). No integer plumbing, no
per-container special case.

`_audio_to_pcm` in `adm_chunks.py` had to move to the same mapping: it scaled
by `2^(bits-1) - 1`, which is a code short of libsndfile's and would have
re-rounded the top of the range. It now scales by `2^(bits-1)` and clips codes
to the asymmetric two's-complement range.

## The 16-bit acceptance fixture

Phase 0's fixture, at the level the plan asked for: a −90 dBFS 997 Hz tone
over a 4 s fade, and the same tone steady. `truncate` is the writer as it
shipped before this phase — float64 straight to libsndfile.

| signal | mode | error RMS dBFS | error / round-to-nearest | DC (LSB) | H3 dBFS | H5 dBFS |
|---|---|---|---|---|---|---|
| −90 dBFS fade | truncate | −94.9 | 2.046 | −0.500 | −104.1 | −107.8 |
| −90 dBFS fade | off | −101.7 | 0.930 | −0.000 | −109.4 | −114.0 |
| −90 dBFS fade | tpdf | −96.3 | **1.729** | −0.000 | **−153.0** | **−142.4** |
| −90 dBFS fade | shaped | −88.6 | 4.233 | −0.000 | −163.1 | −152.6 |
| −90 dBFS steady | truncate | −94.6 | 2.104 | −0.500 | −111.1 | −98.8 |
| −90 dBFS steady | off | −102.5 | 0.854 | −0.000 | −122.2 | −104.0 |
| −90 dBFS steady | tpdf | −96.4 | 1.726 | −0.000 | −144.5 | −145.2 |
| −90 dBFS steady | shaped | −88.5 | 4.250 | −0.000 | −162.8 | −149.8 |

Phase 0's two discriminators both resolve exactly as predicted: truncation
reads **2.0×** the round-to-nearest error RMS and carries **−0.500 LSB** of DC;
both are gone. The distortion columns are the point of the phase — TPDF puts
the third harmonic **49 dB** further down on the fade and **22 dB** on the
steady tone, and the fifth **35 dB** / **46 dB**. That is the "flat noise
floor instead of distortion" the plan asked for, as numbers rather than a
plot.

The single most legible number is the delivered level of the steady tone:

| mode | fundamental at 997 Hz |
|---|---|
| ideal | −90.00 dBFS |
| truncate | −90.57 |
| off (round) | −89.36 |
| tpdf | **−90.00** |

Round-to-nearest alone is not enough. A tone one LSB tall is reproduced by a
three-level staircase whose fundamental is a *different amplitude* from the
input; only dither linearizes the quantizer, and it does so to two decimals.

### Correction to phase 0

Phase 0 predicted TPDF would read **√2 ×** the round-to-nearest RMS. That is
wrong: non-subtractive TPDF adds the dither's own variance (2/12 LSB²) to the
quantizer's (1/12), so the total is **√3 = 1.732×**, which is what every row
above measures (1.726–1.732). The comment in
`test_master_measurement.py::test_audit_quantization_floor` has been corrected.
The rest of phase 0's audit 4 stands unchanged.

`shaped` measures 4.23–4.25×, matching `√(3 × 6) = 4.243` — three units of
TPDF error through a shaper of power gain 6.

## What noise shaping actually buys

On a −20 dBFS fade at 16 bit, comparing total error RMS with the error's
low-frequency content (32-sample moving average, roughly below 750 Hz):

| mode | total | low band |
|---|---|---|
| off | −101.1 dBFS | −116.1 dBFS |
| tpdf | −96.3 | −111.4 |
| shaped | −88.6 | **−120.4** |

7.7 dB more total noise power for 9.0 dB less where the ear is most sensitive
to a steady floor. That is the whole trade, and it is why `shaped` is offered
but not the default: it is a 16-bit-delivery move, and at 24 bit the floor it
optimizes is 48 dB below anything.

## Ordering, pinned

`packages/core/tests/test_export_tail.py` holds the three guarantees:

- **Resample before mastering.** `test_mastering_runs_after_the_resample_and_at_the_delivery_rate`
  spies both stages through a real 44.1 → 48 kHz render and asserts the order
  *and* that `MasteringChain.process` is handed 48 kHz.
- **The stem path too.** `test_the_stem_pipeline_masters_at_the_delivery_rate`.
  Finding on the way: `stem_pipeline.py` carried an `if out_sr != sep_sr:`
  resample **after** the mastering call — the exact defect the pin exists to
  catch. It was unreachable (`stem_pipeline_separate.py` sets
  `sep_sr = out_sr` unconditionally, so the stem path separates at the
  delivery rate), so nothing was ever mis-mastered; the dead branch is
  deleted rather than moved. The pin now watches the invariant that made it
  dead.
- **Nothing after dither.** `test_nothing_scales_the_bed_after_the_quantizer`
  compares the delivered file byte-for-byte against `dither_channels`'s own
  output, so any gain, normalization or fade inserted after quantization is a
  failure, not a rounding difference.

Determinism: `test_two_renders_of_the_same_job_are_byte_identical` renders the
same job twice through `UpmixPipeline` and compares the two files' bytes.

## Audit 5 — sample-rate conversion quality

```
uv run pytest packages/core/tests/test_export_tail.py -m perf -s
```

Tones resampled to 48 kHz, 0.5 s of filter transient trimmed from each end of
a 4 s render, 3 s analysed rectangular — so every integer tone frequency lands
on an exact bin and the measured floor is the resampler's, not the window's.
"worst" is the loudest spectral component that is not the tone.

| source | tone Hz | scipy default pass dB | scipy default worst dBFS | shipped pass dB | shipped worst dBFS |
|---|---|---|---|---|---|
| 44.1 kHz | 100 | +0.000 | −72.7 | −0.000 | −142.3 |
| 44.1 kHz | 1000 | +0.005 | −76.3 | −0.000 | −141.5 |
| 44.1 kHz | 5000 | +0.003 | −74.9 | −0.000 | −140.1 |
| 44.1 kHz | 10000 | +0.010 | −66.1 | +0.000 | −135.5 |
| 44.1 kHz | 15000 | +0.014 | −63.6 | −0.000 | −130.9 |
| 44.1 kHz | 19000 | −0.125 | **−36.4** | −0.000 | −126.0 |
| 44.1 kHz | 20000 | **−0.826** | **−20.7** | −0.000 | −85.7 |
| 96 kHz | 100 | +0.000 | −283.3 | +0.000 | −283.3 |
| 96 kHz | 1000 | +0.004 | −259.8 | +0.000 | −259.8 |
| 96 kHz | 10000 | +0.002 | −237.3 | +0.000 | −237.3 |
| 96 kHz | 20000 | +0.002 | −231.3 | +0.000 | −231.3 |
| 96 kHz | 23000 | **−2.885** | −227.5 | −0.606 | −227.5 |
| 96 kHz | 30000 | — | **−57.5** | — | −128.2 |
| 96 kHz | 40000 | — | −83.9 | — | −147.3 |

**The audit failed, so the plan's conditional upgrade was taken.** SciPy's
`resample_poly` defaults to a Kaiser β=5 window over `10·max(up, down)` taps.
β=5 puts the stopband ripple near −37 dB, and 3201 taps at the 7.056 MHz
polyphase rate is a ~7 kHz transition around the 22.05 kHz cutoff — so the
44.1 → 48 kHz conversion started rolling off at **18.5 kHz** and left images
**37 dB** down. Those images are not all ultrasonic: an image of a 15 kHz tone
lands at 29.1 kHz, folds to 18.9 kHz, and reads −63.6 dBFS in the delivered
file. The 96 → 48 kHz case is worse in kind — `max_rate` is 2, so scipy builds
a **41-tap** filter, which costs 2.9 dB at 23 kHz and rejects a 30 kHz tone by
only 57.5 dB.

`upmixer.resample.anti_imaging_fir` designs the filter explicitly instead: same
`1/max_rate` cutoff, but a Kaiser sized by `kaiserord` for a **120 dB**
stopband with the transition spanning **10%** of the lower of the two rates.
Passband error is now ±0.000 dB to 20 kHz on 44.1 → 48, and the worst
in-audible-band spurious drops **65–90 dB**. The residual −85.7 dBFS on the
20 kHz row is the image at 24.1 kHz folding to 23.9 kHz, i.e. above the
audible band.

Cost, 60 s of mono, this host:

| conversion | default | shipped |
|---|---|---|
| 44.1 → 48 kHz | 3201 taps, 28 ms | 12489 taps, 134 ms |
| 96 → 48 kHz | 41 taps, 59 ms | 159 taps, 334 ms |

~4.7× on a stage that only runs when the delivery rate differs from the
source. For a 12-channel 8-minute export that is roughly 3 s → 13 s, once.

## Validation

```
cd packages/dsp && cargo test                          # 231 passed, 0 failed
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
                                                       # 1215 passed / 45 deselected
uv run pytest packages/core/tests/test_export_tail.py -m perf -s
cd apps/web && npm run build:wasm                      # byte-identical artifact
```

New coverage: `unit_dither.rs` (9 tests — the code lattice at 16/24/32 bit and
all three modes, round-to-nearest's ideal error, TPDF's √3 and ±1.5 LSB bound,
a tone 20 dB *below* the LSB surviving dither and being deleted by rounding,
harmonics replaced by noise, shaping's low-band trade, full-scale clamping
instead of wrapping, and seed reproducibility with independent neighbouring
channels); `test_export_tail.py` (13 tests + the perf audit — the three
ordering pins, determinism, the 16-bit fixture, the 24-bit bound per mode,
float/lossy pass-through, the ADM writer agreeing with the WAV writer
sample-for-sample, manifest round-trip and rejection, and the default).

One existing test needed a widened tolerance, per the plan's "per test, never
globally" rule: `test_stereo_output_duplicates_a_mono_source` asserted
`np.allclose(L, R)` on a mono-duplicated stereo delivery. Independent dither
streams per channel are the correct behaviour — correlated dither would put
its noise in the centre image — so the assertion now carries `atol = 2 LSB` at
24 bit. Nothing else in 1215 tests moved.

### Wasm

`npm run build:wasm` produces a **byte-identical** artifact
(`fa4b2ef449a91aa7bc45ac47aa7e73ef` before and after). `dither.rs` is
unreachable from the wasm crate and is stripped, which is the mechanical proof
of parity entry P5's claim that this is an export-only stage. No realtime
budget to re-measure: nothing on the audio thread changed, so
`npm run bench:engine` was not run.

## A/B listening note — owed, not done

**No listening pass was run: this was an agent session with no audio output.**

What needs ears, specifically:

- **16-bit delivery of a quiet fade-out or a reverb tail.** The measurements
  above say the distortion is gone; only listening says whether the
  replacement noise floor is preferable at −90 dBFS on real material. The
  comparison to run is the same fade exported at `off` and `tpdf`, monitored
  loud.
- **`shaped` versus `tpdf` at 16 bit.** The 9 dB the shaper takes out of the
  low band is bought with 7.7 dB more total, most of it above 10 kHz. Whether
  that reads as "quieter" or as "hiss" is exactly the judgement a measurement
  cannot make, and it is why `tpdf` is the default.
- **The resampler upgrade on a bright master.** The audit says a 15 kHz tone's
  alias fell from −63.6 to −130.9 dBFS and the passband gained 1.5 kHz of
  flat top. On cymbal-heavy 44.1 kHz material at a 48 kHz delivery that should
  be audible as less HF grain; nothing here proves it is.

## Notes

- Knowledge base (`~/Projects/upmixer-knowledge/techniques/
  mastering_restoration.md`) was consulted. It is concept-level and carries
  **no dither, quantization or sample-rate-conversion guidance at all** —
  nothing in it conflicts with the above, and nothing in it informed the
  design. Its chain-order doctrine ("limit last, measure after") is what the
  export tail extends by one step.
- No new Python, JS or Rust dependency. `firwin`/`kaiserord` are scipy, which
  the resampler already used.
- Deliberately not done: exposing `output_dither` in the web composer. The
  plan scopes no UI, the default is right for every integer subtype, and the
  manifest and Python API carry the full surface.
- File sizes: `dither.rs` 67, `dsp-py/dither.rs` 36, `resample.py` 41,
  `writer.py` 84 → 117, `adm_writer.py` 123 → 129, `test_export_tail.py` 290.
  `pipeline.py` 583 → 572 (the resampler moved out).
