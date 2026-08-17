# Phase 7 report — elevation directional band (2026-08-17)

Plan: `docs/plans/mixing/phase7_elevation_eq_band.md`.

Shipped: `elevation_eq` gains a third stage, an RBJ peaking section at
`height_directional_band_hz` (default 8 kHz, Q 1) whose gain is a new served
constant. **The default gain is 1.0 — the band is present and wired, the
shipped voicing is unchanged.** The plan's own fallback, taken for the reason
in §1.

## 1. Why the voicing did not move

The plan's ship criterion is a blind-ish A/B on two tracks: "new voicing
perceived as higher/more open without added harshness". That is a listening
judgement and I cannot make it. Everything measurable says the candidate is
the better trade (§3), and nothing measurable says whether it images higher —
which is the entire point of the change. Shipping a re-voice of every height
send in every mix on the strength of a magnitude plot is not the same
decision the plan asked for, so the band ships at unity and the recipe is
below.

At gain exactly 1.0 the section is skipped outright in both the offline and
the streaming send, so this is not "a filter at unity" — it is the pre-band
signal path, bit for bit (`sends.rs::unity_band_gain_is_the_pre_band_output_bit_for_bit`).

## 2. How to try the candidate

```
uv run upmixer in.wav out.wav --format 7.1.4 \
    --height-high-shelf-gain 1.15 --height-directional-band-gain 1.6
```

Manifest: `routing.height_directional_band_gain`, next to the existing
`routing.height_high_shelf_gain`, and a "Height directional band" slider
beside its siblings in the composer's Spatial section. The band **centre** is
config-only (`height_directional_band_hz`), matching how
`height_crossover_hz` and `height_low_rolloff_hz` are already handled: the
gains are mix controls, the frequencies are the model.

To adopt the candidate as the default, move the two numbers in
`packages/core/src/config.py`; everything else — preview, CLI, manifest,
engine constants — already carries them.

## 3. Measured — the candidate against the shipped voicing

Third-octave magnitude of the whole height send chain, impulse in, dB
relative to input (48 kHz, low rolloff 150 Hz / 0.15, crossover 3 kHz):

| band Hz | shipped (shelf 1.5, no band) | shelf 1.5 + band 1.6 | **shelf 1.15 + band 1.6** | shelf 1.0 + band 1.6 |
|---|---|---|---|---|
| 905 | −0.47 | −0.43 | −0.18 | −0.07 |
| 1810 | −0.85 | −0.66 | −0.10 | +0.16 |
| 2874 | +0.33 | +0.85 | +0.51 | +0.50 |
| 4561 | +2.33 | +3.84 | +2.18 | +1.50 |
| 5747 | +2.87 | +5.47 | +3.50 | +2.59 |
| 7241 | +3.17 | +6.99 | **+4.86** | +3.81 |
| 9123 | +3.34 | +6.93 | +4.72 | +3.60 |
| 11494 | +3.43 | +5.48 | +3.22 | +2.05 |
| 14482 | +3.48 | +4.33 | +2.04 | +0.85 |
| 18246 | +3.51 | +3.75 | +1.45 | +0.24 |
| broadband, white noise | +2.93 | +4.45 | **+2.41** | +1.42 |

The middle-right column is the candidate the plan proposed and it does what
the plan predicted: **+1.7 dB more at 7–9 kHz than the shipped voicing while
being 0.5 dB darker broadband**, because the shelf's flat +3.5 dB from 11 kHz
up — which is air, not elevation — comes back down. `shelf 1.5 + band 1.6`
(band added on top, shelf untouched) is the one to avoid: +7 dB at 7 kHz and
+1.5 dB broadband is a brightness change wearing a cue's clothes.

Real render, 6 s synthetic stereo bed → 7.1.4 through `UpmixPipeline`, the
candidate against the default, per height channel:

| channel | 200 Hz–2 kHz | 4–6 kHz | 6.5–10 kHz | 12–20 kHz | broadband |
|---|---|---|---|---|---|
| TFL / TFR | −0.04 | −0.24 | **+1.43** | **−1.64** | −0.69 |
| TBL / TBR | −0.02 | −0.37 | **+1.43** | **−1.64** | −0.63 |

Whole bed: +0.001 dB — loudness normalization absorbs the small height-side
level change, so an A/B is a tone comparison, not a level one. Midrange is
untouched to 0.04 dB, which is the point: the cue is a band, not a tilt.

Both renders are in the session scratchpad (`source.wav`, `height_off.wav`,
`height_band.wav`), not committed. **I did not listen to them.**

## 4. One design, three consumers

The peaking section is designed once — `kernels::biquad::peaking_sos`, wrapped
as `routing::sends::directional_band_sos` — and reached by:

- `StemRouter._height_send` and `MultichannelUpmixer`, through
  `upmixer_dsp.elevation_eq`;
- the streaming engine, as a third `SosFilter` in each height `Send`
  (`stream::routing`), with the offline/stream equivalence test now run at
  both band gains;
- `HeightFilter`'s STFT mask, which reads the same section's magnitude on the
  bin grid rather than approximating it with a sigmoid the way its other two
  stages did at the time of writing.

Q is deliberately not a served parameter (`DIRECTIONAL_BAND_Q = 1.0` in
`routing::sends`): the band is a psychoacoustic cue, its width is not a mix
control. Recorded in `docs/contracts/preview_export_parity.md` §2.

### The mask's other two stages did not agree with their biquads — fixed since

**Closed by `docs/plans/mixing/phase7_mask_parity_report.md` (2026-08-17).**

As shipped in phase 7, only the band agreed. `HeightFilter`'s sub-bass and
shelf sections were logistic curves fitted by eye to butterworth sections, off
by up to **5.4 dB at 50 Hz** and 1.3 dB at 4 kHz, so the stereo→multichannel
STFT path and the stem path did not voice heights the same. Phase 7 recorded
that rather than fixing it, because closing it re-voices shipped height output.

The follow-up closed it the same way the band was done: the mask is now the
whole chain's magnitude from the same section designs
(`upmixer_dsp.elevation_response`), agreeing to 2e-14 dB across 20 Hz–20 kHz,
and `test_..._share_the_band` became `test_..._share_the_whole_curve`. The
biquads were matched exactly rather than re-tuned; measurements and the
reasoning are in that report.

## 5. Parity

- Served: `height_directional_band_hz` / `height_directional_band_gain` added
  to `engine_constants()`, `ServedEngineConstants`/`HeightShaping`,
  `resolveEngineConstants`, `engineParams.ts`'s `sends` block, the web fixture
  and the wasm test's parameter block. `SendParams` takes both as required
  fields — no serde default, matching that module's "nothing here has a
  default of its own" rule.
- `apps/web/public/wasm/upmixer_dsp.wasm` rebuilt.
- Contract §2 updated (the served pair, the structural Q, the mask sharing the
  design, the unity-gain bit-identity). No new ledger row: nothing here is a
  discrepancy.

## 6. Realtime budget

`npm run bench:engine`, this build against a wasm rebuilt from `HEAD`
(phase 6), binaural order-3 case:

| build | mean | p99 | worst |
|---|---|---|---|
| baseline (HEAD) | 0.72x | 2.59x | 2.94x |
| phase 7, band off | 0.72x | 2.59x | 3.03x |
| phase 7, band 1.6 | 0.71x | 2.75x | 3.17x |

Unchanged within run-to-run noise, which is what one branch (band off) or one
biquad per height send (band on) should cost. **D33 is still open and still
the reason those rows say FAIL** — mid-bass decorrelation, untouched here.

## 7. Validation

- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` →
  **1110 passed, 31 deselected** (1107 before; +3 in `test_height_voicing.py`).
- `cargo test` in `packages/dsp` → green, +3 in `routing::sends` and the
  height-send equivalence test now covering both band gains.
- `npm test` → 249 passed; `npm run build` → clean.
- Phase 0 kit (`test_mix_measurement.py -m perf -s`) re-run: unchanged, as it
  must be with the band at unity.
- Generated audio: §3.

## 8. What phase 7 did not do

- Move the shipped voicing (§1).
- Fix the STFT mask's sigmoid approximation of the other two stages (§4) —
  done afterwards, `phase7_mask_parity_report.md`.
- Touch the binaural renderer's height handling, or `head_model.py`'s use of
  `elevation_eq` — it calls the shelf by keyword and takes the band at unity.
