# Height mask / send parity report (2026-08-17)

Closes the defect phase 7 recorded and left open
(`docs/plans/mixing/phase7_report.md` §4): `HeightFilter`'s STFT mask and the
time-domain `elevation_eq` send voiced height channels differently, by up to
5.4 dB at 50 Hz.

## 1. What shipped

`routing::sends::elevation_response` evaluates the magnitude of the *whole*
elevation chain — the same `butter_sos` low-pass, `butter_sos` high-pass and
`peaking_sos` band `elevation_eq` filters with — on a frequency grid, exported
as `upmixer_dsp.elevation_response`. `HeightFilter._build_elevation_mask` is
now one call to it. The two logistic curves it used for the sub-bass rolloff
and the presence shelf are gone; nothing in the mask is approximated any more.

The parallel stages are complex sums (`1 − (1−g)·H_lp`, `1 + (s−1)·H_hp`), not
cascaded magnitudes, so `kernels::biquad::sos_magnitude` was split into a
complex `sos_response` (plus `sos_cascade_response`) with `sos_magnitude`
keeping its old meaning as `.norm()` of it. No second magnitude formula exists
in Python.

Magnitude, not phase: the mask is a zero-phase per-bin multiply and the
sections are minimum-phase, so magnitude agreement is what is achievable and
what is now pinned.

`upmixer_dsp.elevation_band_response` is deleted — the whole-chain export
subsumes its only caller. `config.height_transition_width_hz` (2000.0) is
deleted too: it existed only to set the shelf sigmoid's width, and no
manifest key, CLI flag, served constant, or other package read it.

## 2. The voicing decision: match the biquads exactly

Two options were on the table — match the biquads, or match them *and* re-tune
`height_low_rolloff_hz` / `_gain` so the resulting bass lands near where the
sigmoid actually was. **Taken: match the biquads exactly, unchanged
constants.** Reasons, in order of weight:

1. The sigmoid was never a tuning decision. It was a fit-by-eye stand-in for
   the biquads, and phase 7 documented it as such. Preserving its bass
   response means preserving a drafting error on purpose.
2. Re-tuning `height_low_rolloff_hz`/`_gain` moves **all four** consumers —
   `StemRouter._height_send`, `MultichannelUpmixer`, the wasm streaming
   engine, and the mask — to fix one. Three of them were already correct.
3. The extra rolloff the mask was applying is inaudible on real material
   anyway (§4): the height source in `ChannelRouter.route_frame` is the
   surround side signal already shaped by `_surround_freq_mask`
   (250 Hz) and `_detail_freq_mask`, so the bins where the two curves disagree
   most carry ~100 dB less energy than the front channels. There is no bass in
   the height sends for the rolloff error to have been protecting anyone from.

## 3. Measured — the curve

48 kHz, defaults (low rolloff 150 Hz / 0.15, crossover 3 kHz, shelf 1.5, band
at unity), dB relative to input:

| | 50 Hz | 100 Hz | 200 Hz | 500 Hz | 2 kHz | 4 kHz | 8 kHz | 16 kHz |
|---|---|---|---|---|---|---|---|---|
| time-domain send | −8.86 | −4.93 | −1.91 | −0.48 | −0.81 | +1.86 | +3.25 | +3.50 |
| mask, before | −13.58 | −9.73 | −1.71 | +0.03 | +0.50 | +3.17 | +3.52 | +3.52 |
| mask, after | −8.86 | −4.93 | −1.91 | −0.48 | −0.81 | +1.86 | +3.25 | +3.50 |
| **change** | **+4.72** | **+4.80** | −0.20 | −0.51 | −1.32 | −1.31 | −0.27 | −0.03 |

Mask against send over 20 Hz–20 kHz after the fix: **max 2.3e-14 dB** with the
band at unity, **4.6e-14 dB** at band gain 1.6 — the same "it is literally the
same design" number the band term alone used to give.

## 4. Measured — a real render

60 s excerpt of a full acoustic mix (44.1 kHz stereo), stereo → 7.1.4 through
`UpmixPipeline`: `uv run upmixer in.wav out.wav --format 7.1.4`, once on this
branch and once with the pre-fix sigmoid mask patched back in. Per-channel
band energy, after minus before, dB:

| channel | 20–200 Hz | 200 Hz–2 kHz | 4–6 kHz | 6.5–10 kHz | 12–20 kHz | broadband |
|---|---|---|---|---|---|---|
| FL/FR/C/LFE/SL/SR/BL/BR | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 | +0.00 |
| TFL / TFR | +0.90 | −0.99 | −0.93 | −0.25 | −0.04 | −0.57 |
| TBL / TBR | +0.94 | −0.74 | −0.99 | −0.25 | −0.04 | −0.89 |

Whole bed: **+0.000 dB**, identical peak (0.5307), identical loudness
normalization (−14.4 → −18.0 LKFS, −3.6 dB, −5.5 dBTP) on both renders. Only
the four height channels move, which is the intended blast radius.

The "heights get audibly more bass" worry, resolved with numbers. TFL absolute
band level in the finished render, against FL in the same render:

| band | TFL − FL (after) | TFL change |
|---|---|---|
| 20–60 Hz | −130.9 dB | +1.13 |
| 60–120 Hz | −106.0 dB | +4.42 |
| 120–200 Hz | −92.9 dB | +0.79 |
| 200–500 Hz | −76.1 dB | −0.68 |
| 2–4 kHz | −47.9 dB | −1.39 |
| 6.5–10 kHz | −36.9 dB | −0.25 |

The +4.4 dB at 60–120 Hz — the full theoretical divergence — lands on content
sitting **106 dB below the front channels**. Height channels in this path only
carry meaningful energy above ~2 kHz. The audible part of the change is the
~1 dB midrange/presence *reduction*, not bass gain: the mask used to be
brighter than the send from 500 Hz up, and now is not.

Renders are in the session scratchpad (`in.wav`, `before.wav`, `after.wav`),
not committed. **I did not listen to them.** No claim is made here about how
the change sounds; every number above is measured, and the one perceptual
statement worth making — whether ~1 dB less presence in the heights reads as
less "air" — is untested by ear.

## 5. Validation

- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` →
  **1110 passed, 31 deselected**, unchanged from the branch baseline.
- `test_height_voicing.py::test_stft_mask_and_time_domain_send_share_the_band`
  is now `..._share_the_whole_curve`: same third-octave 0.5 dB budget, applied
  to the absolute curve instead of the band-only lift, at band gains 1.0 and
  1.6. Passes at 2.3e-14 / 4.6e-14 dB.
- `cd packages/dsp && cargo test` → green, +1
  (`routing::sends::elevation_response_matches_the_time_domain_chain`, which
  pins the export against steady-state tone amplitudes through the actual
  time-domain filters at 1e-9).
- Phase 0 kit (`test_mix_measurement.py -m perf -s`): output **byte-identical**
  before and after. The kit measures `StemRouter._height_send`, i.e. the
  time-domain path, which this change does not touch — worth recording as a
  coverage gap, not as evidence of no change.
- No web work: `HeightFilter`'s only caller is `ChannelRouter.__init__` on the
  non-stem `UpmixPipeline` path; the preview renders stems through
  `stream::routing` and never constructs it. Wheel rebuilt with
  `--reinstall-package upmixer-dsp`; no wasm rebuild needed, and none done.
