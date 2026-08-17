# Phase 0 report — mixing measurement kit and baseline

Kit: `packages/core/tests/test_mix_measurement.py`, perf-marked. Run with

```
uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s
```

1.7 s, no models, no audio files, bit-identical across runs (verified by
diffing two runs). All numbers below are that run's output verbatim.

Signals: 2 s @ 48 kHz. Every chain measured here is LTI — no spatial plan,
and `StemRouter.route`'s only signal-dependent stage is a scalar
renormalization — so transfer functions come from an **impulse response**
rather than the white noise / log sweep the plan named. Same measurement,
exact per bin, zero estimator variance; the sweep would only add noise to a
result available in closed form. Energy accounting uses seeded unit-RMS pink
noise (`_pink`, seed 20260817), which is where a spectrum choice does matter
— see the §4 caveat.

Production code is unchanged, as required.

## 1. Send frequency response

### 1a. Send chain response, third-octave band level (dB, full chain)

Flat = 0 dB. Sub-300 Hz jitter is the comb interacting with band edges, not
estimator noise.

| Band Hz | surround L (HP250 → 31 ms) | surround R (HP250 → 37 ms) | height L (elev EQ → 23 ms) | height R (elev EQ → 29 ms) |
|---|---|---|---|---|
| 25 | -41.63 | -39.97 | -23.51 | -16.39 |
| 31 | -35.90 | -37.57 | -15.30 | -12.27 |
| 40 | -35.03 | -41.71 | -10.97 | -12.00 |
| 50 | -35.35 | -28.61 | -10.56 | -19.12 |
| 63 | -24.38 | -29.54 | -17.58 | -9.04 |
| 79 | -26.08 | -21.21 | -7.63 | -10.77 |
| 100 | -18.32 | -18.17 | -9.36 | -6.42 |
| 126 | -14.33 | -14.42 | -5.26 | -6.81 |
| 159 | -11.81 | -12.18 | -5.83 | -5.38 |
| 200 | -8.31 | -7.62 | -4.64 | -5.32 |
| 252 | -6.28 | -6.03 | -4.71 | -3.88 |
| 317 | -4.04 | -4.18 | -3.48 | -3.87 |
| 400 | -3.79 | -3.89 | -3.60 | -3.80 |
| 504 | -3.18 | -3.20 | -3.65 | -3.35 |
| 635 | -2.95 | -2.93 | -3.28 | -3.62 |
| 800 | -3.18 | -2.98 | -3.63 | -3.26 |
| 1008 | -2.86 | -3.12 | -3.38 | -3.61 |
| 1270 | -2.94 | -2.99 | -3.76 | -3.51 |
| 1600 | -3.08 | -2.90 | -3.65 | -3.83 |
| 2016 | -2.89 | -3.02 | -3.78 | -3.80 |
| 2540 | -2.97 | -2.93 | -3.20 | -3.18 |
| 3200 | -2.96 | -2.99 | -2.12 | -2.05 |
| 4032 | -2.96 | -2.95 | -1.03 | -1.06 |
| 5080 | -3.00 | -2.98 | -0.34 | -0.35 |
| 6400 | -2.97 | -2.98 | +0.07 | +0.06 |
| 8063 | -2.96 | -2.96 | +0.29 | +0.28 |
| 10159 | -2.96 | -2.96 | +0.42 | +0.41 |
| 12800 | -2.96 | -2.97 | +0.48 | +0.49 |

The −2.96 dB floor above ~500 Hz is the comb's average power loss
(0.45² + 0.55² = 0.505 ≈ −2.97 dB), not EQ: **every surround and height send
runs ~3 dB down broadband** purely as a side effect of the dry/wet blend.
`StemRouter.route`'s renormalization then scales the whole stem back up, so
the loss reappears as front-channel level rather than as missing output.

### 1b. Diffuse-send comb, isolated from the send EQ

Comb transfer function = full chain ÷ EQ-only chain, so these columns are the
`diffuse_send` blend alone.

| Chain | worst notch (dB) | notch spacing (1/D) | band ripple p-p (dB) | worst notch, full chain |
|---|---|---|---|---|
| surround L (HP250 → 31 ms) | -20.00 @ 1500 Hz | 32.3 Hz | 8.36 | -21.58 @ 306 Hz |
| surround R (HP250 → 37 ms) | -20.00 @ 4500 Hz | 27.0 Hz | 9.33 | -21.31 @ 311 Hz |
| height L (elev EQ → 23 ms) | -20.00 @ 1500 Hz | 43.5 Hz | 10.24 | -20.85 @ 326 Hz |
| height R (elev EQ → 29 ms) | -20.00 @ 4500 Hz | 34.5 Hz | 9.35 | -20.83 @ 1638 Hz |

−20.00 dB confirms the analytic prediction exactly (20·log₁₀(0.55 − 0.45)).
The audit's "~−20 dB notches every ~27–43 Hz" was right to the dB and to the
hertz. Third-octave averaging *hides* it (8–10 dB p-p) because a band is
wider than the notch spacing — any later phase judging this by band curves
will conclude wrongly; judge per bin.

### 1c. MultichannelUpmixer derived channels, stereo (FL=FR=impulse) in

| Channel | broadband gain (dB) | band ripple p-p (dB) | worst notch (dB) |
|---|---|---|---|
| C build-up (FL+FR+C vs FL+FR) | +0.97 | - | - |
| C | -3.01 | 0.00 | -3.01 @ 300 Hz |
| BL | -13.86 | 11.02 | -49.63 @ 300 Hz |
| BR | -13.86 | 11.02 | -49.63 @ 700 Hz |
| SL | -7.40 | 6.89 | -24.44 @ 500 Hz |
| SR | -7.40 | 6.89 | -24.44 @ 2900 Hz |
| TFL | -6.85 | 15.59 | -12.52 @ 300 Hz |
| TFR | -8.01 | 19.61 | -15.91 @ 1630 Hz |
| TBL | -13.77 | 20.17 | -36.93 @ 300 Hz |
| TBR | -15.35 | 18.38 | -36.73 @ 1900 Hz |

Derived LFE omitted: it is the same butter lowpass measured in §3, and its
stopband would dominate both ripple columns with nothing audible.

Two findings. **The derived center adds +0.97 dB of front-bed energy** for
fully correlated input — real build-up, smaller than the +3 dB the audit
estimated, because `0.707 × 0.5` already attenuates 9 dB.

**The lateral bias is not a level difference on the pair that carries the
delay.** SL and SR are magnitude-identical (−7.40 dB, same ripple) — a pure
delay has unit magnitude, so the one-sided `haas_decorrelate` costs nothing
per channel. The asymmetry surfaces one derivation *downstream*: TFL/TFR are
built from `FL·0.5 + SL·0.3` and `FR·0.5 + SR·0.3`, and because SR is already
delayed, the right height source combs differently from the left —
**TFR is 1.16 dB down on TFL, TBR 1.58 dB down on TBL**, with 4 dB more
ripple. That is the measurable form of audit finding 2.

### 1d. Mono fold-down of each decorrelated pair (L+R)

| Pair | sum vs power sum (dB) | worst notch rel. (dB) | per-bin ripple σ (dB) |
|---|---|---|---|
| StemRouter surround (31/37 ms) | +1.46 | -31.97 @ 368 Hz | 5.17 |
| StemRouter height (23/29 ms) | +1.46 | -40.98 @ 1634 Hz | 5.39 |
| MultichannelUpmixer SL+SR | +0.00 | -238.58 @ 500 Hz | 10.86 |
| MultichannelUpmixer BL+BR | +0.00 | -234.58 @ 750 Hz | 15.21 |
| MultichannelUpmixer TFL+TFR | -0.00 | -41.37 @ 1912 Hz | 6.50 |
| MultichannelUpmixer TBL+TBR | -0.00 | -55.00 @ 1986 Hz | 9.13 |

This is the worst result in phase 0. `MultichannelUpmixer`'s surround and
back pairs are `s` and `s·z^−D` — the same signal and a pure delay — so their
mono sum has **complete nulls (−238 dB is numerical zero), every 43.5 Hz for
SL+SR and every 52.6 Hz for BL+BR**, with 10.9–15.2 dB of per-bin ripple.
Correlated (center-panned) programme material is what hits this; the 0-dB
"sum vs power sum" column shows total energy is preserved, which is precisely
why a broadband check would miss it. The `StemRouter` pairs use *different*
delays per side, which is why their nulls are finite (−32 to −41 dB) rather
than total — the differing L/R delays were the right instinct, but they
buy 40 dB of null depth, not flatness.

## 2. Downmix null / fold-down comb

### 2a. Downmix (7.1.4 → BS.775 stereo) vs direct stereo render, balanced

Ratio of the BS.775 stereo downmix of a 7.1.4 render against
`fold_route_to_stereo`'s stereo render of the same stem — the two stereo
images the audit says disagree.

| Stem | level offset (dB) | band ripple p-p (dB) | per-bin ripple σ (dB) | worst notch rel. (dB) |
|---|---|---|---|---|
| Crowd | -0.51 | 3.22 | 5.14 | -52.60 @ 8470 Hz |
| Other | +1.14 | 0.33 | 0.28 | -6.34 @ 13806 Hz |
| Crash | -0.85 | 0.00 | 0.00 | -0.00 @ 12003 Hz |
| Hi-Hat | +0.72 | 0.00 | 0.00 | -0.00 @ 9002 Hz |
| Backing Vocals | +1.70 | 0.00 | 0.00 | -0.00 @ 13806 Hz |
| Drums | +2.16 | 0.00 | 0.00 | -0.00 @ 9002 Hz |

Two regimes, both real defects:

- **Stems with surround sends** (Crowd, Other) get the fold-down comb:
  −52.6 dB nulls and 5.1 dB per-bin ripple for Crowd. The two stereo images
  genuinely differ in timbre, not just level.
- **Stems whose only spatial content is height** (Crash, Hi-Hat, Backing
  Vocals, Drums in the balanced preset) show **exactly zero ripple** — the
  downmix is spectrally identical to the front bed because the height
  channels are dropped whole. Nothing is smeared; content is simply gone.
  A spectral-ripple metric alone would score this path as perfect.

The level offsets (−0.85 to +2.16 dB) are the second half of the mismatch:
the downmix is generally *louder* despite dropping heights, because C enters
at 0.707 and the surrounds add on top, while `fold_route_to_stereo` is a pan
law that gets renormalized.

### 2b. Energy lost by dropping heights from the downmix, per preset (7.1.4)

| Preset | mean loss (dB) | worst stem (dB) | max height fraction |
|---|---|---|---|
| balanced | 0.23 | Crowd 1.58 | 0.304 |
| intimate | 0.07 | Crowd 0.37 | 0.081 |
| stage | 0.33 | Crash 2.64 | 0.455 |
| wide | 0.82 | Crash 6.17 | 0.758 |
| immersive | 1.38 | Crash 5.08 | 0.689 |
| live | 0.51 | Crowd 2.70 | 0.463 |

### 2c. Height fraction and downmix loss per stem, balanced (7.1.4)

| Stem | height fraction | loss (dB) |
|---|---|---|
| Lead Vocals | 0.000 | -0.00 |
| Vocals | 0.000 | -0.00 |
| Backing Vocals | 0.047 | 0.21 |
| Bass | 0.000 | -0.00 |
| Kick | 0.000 | -0.00 |
| Snare | 0.000 | -0.00 |
| Toms | 0.000 | 0.00 |
| Drums | 0.000 | 0.00 |
| Hi-Hat | 0.066 | 0.30 |
| Ride | 0.116 | 0.53 |
| Crash | 0.179 | 0.85 |
| Guitar | 0.005 | 0.02 |
| Piano | 0.007 | 0.03 |
| Other | 0.021 | 0.09 |
| Instrumental | 0.008 | 0.04 |
| Crowd | 0.304 | 1.58 |

**This is the number that re-scopes phase 4.** The audit read the preset
tables (Crash TFL 0.80, Hi-Hat TFL 0.72) as "most cymbal/air energy
overhead". Measured, on the default preset, Crash loses **0.85 dB** and
Hi-Hat **0.30 dB** — not their content, a fraction of it. Two reasons the
table gains mislead: those weights sit alongside comparable front weights and
are L2-normalized, and pink noise puts most energy below the cymbal band that
actually goes up. The loss is only material on the height-forward presets:
`wide` Crash **6.17 dB**, `immersive` Crash 5.08 dB, `immersive` mean 1.38 dB.

## 3. LFE energy audit

In-band = below `lfe_cutoff_hz` (120 Hz). "LFE vs mains" applies the +10 dB
in-band playback weighting to the LFE channel, then compares its in-band
energy against the in-band energy of every other output channel summed.
"Crossover sum vs power" is 10·log₁₀(Σ|A+B|² / Σ(|A|²+|B|²)) over bins within
±10 Hz of 120 Hz — what a coincident listener gets versus what an incoherent
sum would give.

| Preset | Stem | LFE vs mains (dB) | crossover sum vs power (dB) |
|---|---|---|---|
| balanced | Bass | -9.96 | -2.41 |
| balanced | Kick | -9.12 | -2.71 |
| balanced | Toms | -21.39 | -0.56 |
| balanced | Drums | -18.01 | -0.84 |
| balanced | Other | -23.31 | -0.45 |
| balanced | Instrumental | -15.18 | -1.21 |
| intimate | Bass | -10.92 | -2.11 |
| intimate | Kick | -9.57 | -2.55 |
| intimate | Toms | -22.77 | -0.47 |
| intimate | Drums | -18.89 | -0.75 |
| intimate | Other | -26.19 | -0.31 |
| intimate | Instrumental | -17.02 | -0.95 |
| stage | Bass | -9.96 | -2.41 |
| stage | Kick | -9.12 | -2.71 |
| stage | Toms | -21.74 | -0.53 |
| stage | Drums | -18.01 | -0.84 |
| stage | Other | -23.79 | -0.42 |
| stage | Instrumental | -15.18 | -1.21 |
| wide | Bass | -9.94 | -2.42 |
| wide | Kick | -9.12 | -2.71 |
| wide | Toms | -21.43 | -0.55 |
| wide | Drums | -18.27 | -0.81 |
| wide | Other | -23.34 | -0.44 |
| wide | Instrumental | -14.66 | -1.30 |
| immersive | Bass | -9.96 | -2.41 |
| immersive | Kick | -9.12 | -2.71 |
| immersive | Toms | -21.64 | -0.54 |
| immersive | Drums | -18.37 | -0.81 |
| immersive | Other | -22.73 | -0.44 |
| immersive | Instrumental | -14.60 | -1.29 |
| live | Bass | -9.96 | -2.41 |
| live | Kick | -9.12 | -2.71 |
| live | Toms | -21.63 | -0.54 |
| live | Drums | -18.27 | -0.82 |
| live | Other | -22.61 | -0.49 |
| live | Instrumental | -14.58 | -1.32 |

**The audit's premise is wrong in direction.** It claimed the sends "ignore
the +10 dB in-band playback gain". They do not: `UpmixConfig.lfe_gain`
defaults to 0.3162 = **−10 dB**, cited to BS.775-4 Annex 7 in `config.py`, so
after the +10 dB playback weighting the LFE path is at unity and the
0.75–0.90 send weights are already compensated. Measured, LFE arrives
**9–10 dB below the mains** in-band on the strongest stems (Bass, Kick) and
15–26 dB below on the rest. There is no LFE level runaway to fix. Preset
choice barely moves it (≤1 dB), because `.lfe` placement values are shared
across presets.

The phase relationship is a real, if modest, defect: at the crossover the
coherent sum is **2.4–2.7 dB below** the power sum for Bass and Kick — the
4th-order Butterworth LFE lowpass is partially cancelling against the
unfiltered mains bass rather than summing with it. Stems with weak LFE sends
show less (−0.3 to −1.3 dB) simply because the LFE term is smaller.

## 4. Channel energy accounting

Fraction of each stem's input energy landing in each zone after
`StemRouter.route`'s renormalization, seeded pink noise. front = FL/FR/C,
surround = SL/SR/BL/BR, height = TFL/TFR/TBL/TBR.

Caveat for later phases: front/surround/height sum to exactly 1.0000 by
construction (the renormalization targets the non-LFE channels), and **LFE is
added after it, outside the normalization** — so the LFE column is energy on
top of unity, not a share of it. Also, the surround and height shares are
suppressed by the send EQ (HP250, elevation rolloff) acting on a pink
spectrum: these are energy shares of realistic-ish material, not routing
gains. Re-run the kit unchanged for comparability rather than reinterpreting
the numbers.

### Balanced preset, per layout

Stereo: every stem is 1.000 front / 0 elsewhere (the fold collapses onto
FL/FR), so only 5.1 and 7.1.4 are reproduced here.

**5.1**

| Stem | front | surround | height | LFE |
|---|---|---|---|---|
| Lead Vocals | 1.000 | 0.000 | 0.000 | 0.000 |
| Vocals | 1.000 | 0.000 | 0.000 | 0.000 |
| Backing Vocals | 1.000 | 0.000 | 0.000 | 0.000 |
| Bass | 1.000 | 0.000 | 0.000 | 0.015 |
| Kick | 1.000 | 0.000 | 0.000 | 0.019 |
| Snare | 1.000 | 0.000 | 0.000 | 0.000 |
| Toms | 1.000 | 0.000 | 0.000 | 0.001 |
| Drums | 1.000 | 0.000 | 0.000 | 0.002 |
| Hi-Hat | 1.000 | 0.000 | 0.000 | 0.000 |
| Ride | 1.000 | 0.000 | 0.000 | 0.000 |
| Crash | 0.986 | 0.014 | 0.000 | 0.000 |
| Guitar | 0.965 | 0.035 | 0.000 | 0.000 |
| Piano | 0.990 | 0.010 | 0.000 | 0.000 |
| Other | 0.962 | 0.038 | 0.000 | 0.001 |
| Instrumental | 0.996 | 0.004 | 0.000 | 0.004 |
| Crowd | 0.018 | 0.982 | 0.000 | 0.000 |

**7.1.4**

| Stem | front | surround | height | LFE |
|---|---|---|---|---|
| Lead Vocals | 1.000 | 0.000 | 0.000 | 0.000 |
| Vocals | 1.000 | 0.000 | 0.000 | 0.000 |
| Backing Vocals | 0.953 | 0.000 | 0.047 | 0.000 |
| Bass | 1.000 | 0.000 | 0.000 | 0.015 |
| Kick | 1.000 | 0.000 | 0.000 | 0.019 |
| Snare | 1.000 | 0.000 | 0.000 | 0.000 |
| Toms | 0.999 | 0.000 | 0.000 | 0.001 |
| Drums | 1.000 | 0.000 | 0.000 | 0.002 |
| Hi-Hat | 0.934 | 0.000 | 0.066 | 0.000 |
| Ride | 0.884 | 0.000 | 0.116 | 0.000 |
| Crash | 0.821 | 0.000 | 0.179 | 0.000 |
| Guitar | 0.960 | 0.035 | 0.005 | 0.000 |
| Piano | 0.990 | 0.003 | 0.007 | 0.000 |
| Other | 0.972 | 0.007 | 0.021 | 0.001 |
| Instrumental | 0.992 | 0.000 | 0.008 | 0.004 |
| Crowd | 0.000 | 0.696 | 0.304 | 0.000 |

### 4b. Zone fraction averaged over all 16 stems, per preset (7.1.4)

| Preset | front | surround | height | LFE |
|---|---|---|---|---|
| balanced | 0.907 | 0.046 | 0.047 | 0.003 |
| intimate | 0.926 | 0.060 | 0.015 | 0.002 |
| stage | 0.893 | 0.046 | 0.061 | 0.003 |
| wide | 0.826 | 0.043 | 0.130 | 0.003 |
| immersive | 0.756 | 0.025 | 0.218 | 0.003 |
| live | 0.867 | 0.037 | 0.095 | 0.003 |

The mix is far more front-dominated than the routing tables suggest: 90.7% of
stem energy stays in FL/FR/C on the default preset, and even `immersive`
keeps 75.6%. Only Crowd genuinely leaves the front wall.

### 4c. Channels the preset does not request but the merged route keeps

Found while building the accounting table, and it is a production behaviour,
not a kit artefact. `StemRouter._routing_for` merges overrides *over*
`DEFAULT_ROUTING` (balanced on 7.1.4), so channels the requested preset does
not include keep their balanced weight. One worst case per layout × preset,
counting only channels the layout can actually reproduce:

| Layout | Preset | worst stem | max residual gain | residual channels |
|---|---|---|---|---|
| 5.1 | balanced | Backing Vocals | 0.274 | 1 |
| 5.1 | intimate | Guitar | 0.396 | 2 |
| 5.1 | stage | Guitar | 0.396 | 1 |
| 5.1 | wide | Backing Vocals | 0.274 | 1 |
| 5.1 | immersive | Backing Vocals | 0.274 | 1 |
| 5.1 | live | Backing Vocals | 0.274 | 1 |
| 7.1.4 | intimate | Ride | 0.534 | 2 |
| 7.1.4 | stage | Ride | 0.534 | 2 |
| 7.1.4 | wide | Backing Vocals | 0.274 | 1 |
| 7.1.4 | immersive | Guitar | 0.396 | 2 |
| 7.1.4 | live | Piano | 0.138 | 2 |

Residual gains up to 0.534 — larger than many intentional sends. It affects
`balanced` on 5.1 too, because `DEFAULT_ROUTING` is the 7.1.4 realization and
the 5.1 realization re-projects elevation into width, so the two maps differ
in which channels they use. Stereo output is unaffected (the fold leaves only
FL/FR in the base). Not in any planned phase; see the verdicts.

## Re-scope verdicts

- **Phase 1 (rebalancer soft-clip)** — proceed, unmeasured here. The tanh is
  outside the routing path this kit covers; nothing in phase 0 argues against
  removing it.
- **Phase 2 (velvet decorrelator kernel)** — proceed, full scope. §1b
  confirms −20.00 dB notches every 27–43 Hz analytically and per bin; §1d
  shows the pair sums null at −32 to −238 dB. Judge the new kernel per bin
  (§1b, §1d), never by third-octave bands, which hide the defect entirely.
- **Phase 3 (send integration parity)** — proceed, full scope, and add the
  §1a broadband finding: the current sends are ~2.96 dB down purely from the
  blend, absorbed by `route`'s renormalization. A flat replacement kernel
  will shift the front/send balance even at identical send weights, so
  re-run §4 and expect the table to move.
- **Phase 4 (downmix height fold)** — **shrink**. The defect is real but
  smaller than the audit implied: 0.30–0.85 dB on the default preset's
  cymbals (§2c), 6.17 dB only on `wide`/Crash. Worth fixing for image
  consistency (§2a shows the two paths differ by −0.85 to +2.16 dB in level
  and, for surround-fed stems, in timbre), not as an urgent content-loss
  repair. Do the fold; skip anything elaborate built to recover "vanished"
  air.
- **Phase 5 (LFE calibration)** — **shrink substantially, one part killed**.
  The +10 dB compensation part is **already implemented** (`lfe_gain` =
  −10 dB per BS.775-4 Annex 7) and measures 9–10 dB *below* the mains, so
  "re-scale the preset sends for the missing +10 dB" must not ship — it would
  make LFE too loud. What survives is the phase-alignment half: −2.4 to
  −2.7 dB of cancellation at the crossover for Bass/Kick, addressable with
  the Linkwitz-Riley-aligned lowpass the plan already names.
- **Phase 6 (multichannel center)** — proceed, expectation corrected. The
  build-up is **+0.97 dB**, not +3 dB (§1c) — `0.707 × 0.5` already
  attenuates 9 dB. Subtractive extraction is still the right fix, but it
  buys about 1 dB, so keep it cheap. The larger `MultichannelUpmixer` defect
  is §1d's total mono nulls in SL+SR and BL+BR; if only one of the two gets
  done, do the decorrelator (phase 3's coverage of this path), not the
  center.
- **Phase 7 (elevation EQ band)** — proceed as optional, unchanged. §1a
  documents the current broad shelf (+0.48 dB by 12.8 kHz, transition from
  ~2.5 kHz) as the before-picture.
- **New, unplanned** — the §4c preset-merge residue. A preset switch leaves
  up to 0.534 of gain in channels the new preset does not request, on every
  layout including the default preset on 5.1. Not a DSP defect, a routing
  precedence one, and cheap to fix (replace rather than merge when the
  override is a complete preset realization). Worth a phase of its own before
  any phase re-measures §4, since it contaminates that table.

## Reproducing

Kit and all helpers are in `packages/core/tests/test_mix_measurement.py`; no
scratch scripts, no fixtures, no corpus. `-s` prints every table above in
markdown, ready to paste. Full suite after this phase: **1083 passed, 31
deselected** (`uv run pytest packages/core/tests apps/api/tests
apps/cli/tests -q`) — the plan's 846 baseline is stale, the suite grew since
that number was written; the four new tests are perf-marked and deselected by
default.

---

# Phase 1 — Rebalancer soft clip removed (2026-08-17)

`StemRebalancer.process` no longer soft-clips. The `gain_db > +3 dB` branch
(`tanh(arr / 0.95) * 0.95`) and both its constants are gone; the 10 ms
ramp-up and everything else about the stage are unchanged. Overload
protection was never this stage's job — the mastering chain's look-ahead
true-peak limiter runs on the routed bed in both pipelines.

## Measured THD, before → after

Third through fifth harmonic of a 441 Hz sine, measured past the ramp over
whole cycles (no window, so no leakage floor):

| Stem peak | Boost | Old THD | New THD |
|---|---|---|---|
| 0.1 (−20 dBFS) | +6 dB | 3.64e-03 (−48.8 dB) | 1.5e-16 |
| 0.1 (−20 dBFS) | +12 dB | 1.40e-02 (−37.1 dB) | 1.9e-16 |
| 0.3 | +6 dB | 3.01e-02 (−30.4 dB) | 2.1e-16 |
| 0.9 | +12 dB | 2.86e-01 (−10.9 dB) | 2.0e-16 |

The −20 dBFS rows are the point: tanh is nonlinear at *every* level, so a
quiet stem was being distorted by a stage that existed to catch peaks.
New THD is the float64 floor — the output is now exactly `input × 10^(dB/20)`
past the ramp (`atol=1e-12`).

## Parity result

The preview never mirrored the clip. `stream::engine` applies
`10^(rebalance_db/20) * route_scale` through a one-pole smoother
(`engine/mod.rs`, `engine/analysis.rs`) with no saturator anywhere on the
stem path; `tanh` in `dsp-core` appears only in `mastering::bass`'s exciter,
`match_reference::curve`'s knee, and `spatial::downmix`'s soft limit. So this
was a real, live parity gap for any mixer fader past +3 dB — preview clean,
export distorted — and removing the export-side clip **closes** it rather
than opening one. Recorded as **D32** in
`docs/contracts/preview_export_parity.md`. No contract re-hash: the doc has
no signature mechanism, and no `packages/dsp` code changed, so the golden and
`stream_equivalence` suites are untouched (`stream_equivalence.rs` already
covers `rebalance_db: 24.0`).

## Validation

`uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` →
**1085 passed, 31 deselected** (baseline 1083; net +2 from the test swap
below). No `apps/web` or `packages/dsp` change, so `npm test` /
`npm run build` / `npm run bench:engine` are not implicated.

`test_stem_rebalance.py::test_large_boost_soft_clips` asserted the old
behaviour and is replaced by three tests: exact linearity past the ramp at
+6 dB, THD at the numerical floor at +12 dB, and a +12 dB boost on a 0.9-peak
stem passing through to 3.58 unclamped.

## Note for later

If per-stem overload control is ever wanted, it is a proper look-ahead
limiter per stem, not a saturator. Not built — nothing currently asks for it,
and the bed limiter already covers the delivery path.
