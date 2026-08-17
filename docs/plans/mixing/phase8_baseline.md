# Phase 8 baseline — measurement kit re-run

The phase 0 report's tables predate phases 3-5 (velvet decorrelator sends,
height fold-down, LFE calibration) and phase 7 (elevation directional band).
**Phases 9-11 cite this file, not `phase0_report.md`.**

Kit: `packages/core/tests/test_mix_measurement.py`, perf-marked. Run with

```
uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s
```

2.5 s, no models, no audio files, bit-identical across runs. Every number
below is that run's output verbatim, at `feature/mixing-revamp` with phase 8
applied. Nothing in phase 8 touches the export path, so the shift from phase
0 is entirely phases 1-7; this run is the record of where they left it.

Signals: 2 s @ 48 kHz, impulse response for the LTI chains, seeded unit-RMS
pink noise (`_pink`, seed 20260817) for energy accounting — see the phase 0
report for why, and for the §4 spectrum caveat.

## What moved since phase 0

| Measurement | Phase 0 | Now | Cause |
|---|---|---|---|
| Send decorrelation, worst notch | −20.00 dB, every 27-43 Hz, on every send | −44 to −53 dB at isolated bins, no periodic spacing | Phase 2/3 replaced the single-tap `diffuse_send` blend with the velvet-noise pair |
| Mono fold-down of a decorrelated pair | comb, by construction | +0.00 to +0.15 dB vs the power sum | Phase 2's downmix-flat construction |
| `MultichannelUpmixer` C | −3.01 dB, +0.97 dB build-up | +3.01 dB, −0.00 dB build-up, fold-down error −300 dB | Phase 6's subtractive centre |
| Stereo downmix loss, worst preset | 1.99 dB mean (heights dropped) | 0.80 dB mean (heights folded) | Phase 4's height fold |
| LFE vs mains, Bass/Kick (balanced) | −9.96 / −9.12 dB | −10.13 / −9.28 dB | Phase 5's +10 dB in-band compensation and send re-scale |
| Crossover sum vs power, Bass (balanced) | −2.41 dB | −1.62 dB | Phase 5's Linkwitz-Riley alignment |
| Height band level, 5-13 kHz | phase 7 predates | +2.6 to +3.5 dB | Phase 7's directional band at 8 kHz |

Two things worth carrying into phases 9-11:

- **Zone accounting still sums to 1.0000 for every stem** (§4, "non-LFE
  total"), so nothing in phases 1-7 introduced a routing energy leak. Phase
  9's renormalization question is about *loudness*, not lost energy.
- **§4c residual channels are unchanged from phase 0** — the merged route
  still keeps channels the preset never asked for, up to 0.534 gain. Phase 11
  owns whether that is a bug.

### 1a. Send chain response, third-octave band level (dB, full chain)

| Band Hz | surround L (HP250 → velvet L) | surround R (HP250 → velvet R) | height L (elev EQ → velvet L) | height R (elev EQ → velvet R) |
|---|---|---|---|---|
| 25 | -47.25 | -38.58 | -9.68 | -19.84 |
| 31 | -44.86 | -35.26 | -8.73 | -17.15 |
| 40 | -38.34 | -31.78 | -7.84 | -15.41 |
| 50 | -30.45 | -28.32 | -6.75 | -13.29 |
| 63 | -24.43 | -25.52 | -5.61 | -8.11 |
| 79 | -19.80 | -25.58 | -4.73 | -5.22 |
| 100 | -16.05 | -33.31 | -2.36 | -4.57 |
| 126 | -10.12 | -19.22 | -4.06 | -3.37 |
| 159 | -6.61 | -10.04 | -14.44 | -0.59 |
| 200 | -3.38 | -5.44 | -3.90 | -1.59 |
| 252 | -1.98 | -4.00 | -1.29 | -2.49 |
| 317 | -2.68 | -0.76 | -3.44 | -1.28 |
| 400 | -2.94 | -1.84 | -2.06 | -2.20 |
| 504 | +0.80 | +2.24 | -3.14 | +1.24 |
| 635 | +0.34 | -0.09 | -2.21 | +0.93 |
| 800 | +0.94 | +0.66 | +1.00 | -1.73 |
| 1008 | -1.77 | -0.58 | +1.48 | -1.60 |
| 1270 | -0.91 | -0.79 | -0.82 | -1.20 |
| 1600 | +0.87 | +0.43 | -2.56 | +0.31 |
| 2016 | +0.67 | +0.10 | -0.88 | -1.75 |
| 2540 | -1.38 | +0.09 | +0.38 | +0.37 |
| 3200 | +0.23 | -0.54 | -0.13 | +1.82 |
| 4032 | +0.55 | +0.47 | +2.37 | +1.57 |
| 5080 | -0.21 | -0.10 | +2.58 | +2.84 |
| 6400 | -0.21 | -0.01 | +3.19 | +2.82 |
| 8063 | +0.29 | +0.07 | +3.12 | +2.82 |
| 10159 | -0.23 | +0.00 | +3.19 | +3.48 |
| 12800 | -0.11 | -0.10 | +3.53 | +3.48 |

### 1b. Decorrelator response, isolated from the send EQ

| Chain | worst notch (dB) | dips < −10 dB | band ripple p-p (dB) | broadband gain (dB) | worst notch, full chain |
|---|---|---|---|---|---|
| surround L (HP250 → velvet L) | -48.44 @ 11040 Hz | 77 | 11.09 | +0.00 | -48.44 @ 11040 Hz |
| surround R (HP250 → velvet R) | -44.60 @ 8430 Hz | 72 | 18.85 | +0.01 | -44.60 @ 8430 Hz |
| height L (elev EQ → velvet L) | -44.79 @ 4868 Hz | 69 | 15.08 | -0.00 | -42.28 @ 4868 Hz |
| height R (elev EQ → velvet R) | -52.56 @ 4360 Hz | 87 | 8.84 | +0.00 | -50.38 @ 4360 Hz |

### 1c. MultichannelUpmixer derived channels, stereo (FL=FR=impulse) in

| Channel | broadband gain (dB) | band ripple p-p (dB) | worst notch (dB) |
|---|---|---|---|
| C build-up (FL+FR+C vs input pair) | -0.00 | - | - |
| C fold-down error (FL+0.707C vs input FL) | -300.00 | - | - |
| C | +3.01 | 0.00 | 3.01 @ 300 Hz |
| BL | -6.80 | 22.50 | -106.51 @ 11040 Hz |
| BR | -7.32 | 34.90 | -98.82 @ 8430 Hz |
| SL | -4.44 | 11.09 | -52.88 @ 11040 Hz |
| SR | -4.44 | 18.85 | -49.03 @ 8430 Hz |
| TFL | -7.90 | 15.83 | -52.80 @ 4868 Hz |
| TFR | -7.87 | 21.29 | -60.54 @ 4360 Hz |
| TBL | -11.95 | 24.91 | -64.42 @ 14826 Hz |
| TBR | -12.43 | 25.83 | -62.23 @ 4360 Hz |

### 1d. Mono fold-down of each decorrelated pair (L+R)

| Pair | sum vs power sum (dB) | worst notch rel. (dB) | per-bin ripple σ (dB) |
|---|---|---|---|
| StemRouter surround (velvet L/R) | +0.01 | -48.04 @ 10047 Hz | 5.47 |
| StemRouter height (velvet L/R) | +0.01 | -55.34 @ 13577 Hz | 5.46 |
| MultichannelUpmixer SL+SR | +0.00 | -48.29 @ 10047 Hz | 5.47 |
| MultichannelUpmixer BL+BR | +0.04 | -55.98 @ 3120 Hz | 7.40 |
| MultichannelUpmixer TFL+TFR | +0.04 | -45.46 @ 4326 Hz | 5.27 |
| MultichannelUpmixer TBL+TBR | +0.15 | -50.50 @ 14957 Hz | 7.02 |
### 2a. Downmix (7.1.4 → BS.775 stereo) vs direct stereo render, balanced

| Stem | level offset, heights dropped (dB) | level offset, heights folded (dB) | band ripple p-p (dB) | per-bin ripple σ (dB) | worst notch rel. (dB) |
|---|---|---|---|---|---|
| Crowd | -2.27 | -2.14 | 8.50 | 5.70 | -41.48 @ 3102 Hz |
| Other | +0.80 | +0.83 | 1.67 | 1.09 | -7.22 @ 15807 Hz |
| Crash | -1.54 | -0.98 | 5.94 | 3.68 | -51.64 @ 9102 Hz |
| Hi-Hat | +0.45 | +0.59 | 2.96 | 1.78 | -9.89 @ 14498 Hz |
| Backing Vocals | +1.51 | +1.58 | 2.22 | 1.33 | -6.30 @ 14498 Hz |
| Drums | +2.16 | +2.16 | 0.07 | 0.04 | -0.58 @ 13806 Hz |

### 2b. Stem energy the stereo downmix fails to carry, per preset (7.1.4)

| Preset | mean loss, heights dropped (dB) | mean loss, heights folded (dB) | worst stem, folded (dB) | max height fraction |
|---|---|---|---|---|
| balanced | 0.34 | 0.18 | Crowd 1.11 | 0.301 |
| intimate | 0.11 | 0.06 | Crowd 0.27 | 0.106 |
| stage | 0.48 | 0.23 | Crash 1.62 | 0.621 |
| wide | 1.19 | 0.46 | Crash 2.44 | 0.861 |
| immersive | 1.99 | 0.80 | Crowd 2.78 | 0.813 |
| live | 0.76 | 0.33 | Crash 1.59 | 0.613 |

### 2c. Height fraction and downmix loss per stem, balanced (7.1.4)

| Stem | height fraction | loss dropped (dB) | loss folded (dB) |
|---|---|---|---|
| Lead Vocals | 0.000 | -0.00 | -0.00 |
| Vocals | 0.000 | -0.00 | -0.00 |
| Backing Vocals | 0.089 | 0.41 | 0.20 |
| Bass | 0.000 | -0.00 | -0.00 |
| Kick | 0.000 | -0.00 | -0.00 |
| Snare | 0.000 | -0.00 | -0.00 |
| Toms | 0.001 | 0.00 | 0.00 |
| Drums | 0.000 | 0.00 | 0.00 |
| Hi-Hat | 0.122 | 0.57 | 0.27 |
| Ride | 0.205 | 0.99 | 0.47 |
| Crash | 0.299 | 1.54 | 0.70 |
| Guitar | 0.009 | 0.04 | 0.02 |
| Piano | 0.014 | 0.06 | 0.03 |
| Other | 0.040 | 0.18 | 0.09 |
| Instrumental | 0.017 | 0.07 | 0.04 |
| Crowd | 0.301 | 1.56 | 1.11 |
### 3. LFE in-band (<120 Hz) energy with +10 dB playback weighting

| Preset | Stem | LFE vs mains (dB) | crossover sum vs power (dB) |
|---|---|---|---|
| balanced | Bass | -10.13 | -1.62 |
| balanced | Kick | -9.28 | -1.82 |
| balanced | Toms | -21.53 | -0.40 |
| balanced | Drums | -18.17 | -0.59 |
| balanced | Other | -23.21 | -0.35 |
| balanced | Instrumental | -15.22 | -0.89 |
| intimate | Bass | -11.08 | -1.43 |
| intimate | Kick | -9.73 | -1.71 |
| intimate | Toms | -22.92 | -0.33 |
| intimate | Drums | -19.04 | -0.53 |
| intimate | Other | -26.29 | -0.23 |
| intimate | Instrumental | -17.07 | -0.70 |
| stage | Bass | -10.13 | -1.62 |
| stage | Kick | -9.28 | -1.82 |
| stage | Toms | -21.89 | -0.38 |
| stage | Drums | -18.17 | -0.59 |
| stage | Other | -23.64 | -0.34 |
| stage | Instrumental | -15.22 | -0.89 |
| wide | Bass | -10.11 | -1.63 |
| wide | Kick | -9.28 | -1.82 |
| wide | Toms | -21.58 | -0.39 |
| wide | Drums | -18.44 | -0.57 |
| wide | Other | -22.58 | -0.44 |
| wide | Instrumental | -14.48 | -1.06 |
| immersive | Bass | -10.13 | -1.62 |
| immersive | Kick | -9.28 | -1.82 |
| immersive | Toms | -21.74 | -0.39 |
| immersive | Drums | -18.48 | -0.58 |
| immersive | Other | -20.61 | -0.68 |
| immersive | Instrumental | -13.94 | -1.24 |
| live | Bass | -10.13 | -1.62 |
| live | Kick | -9.28 | -1.82 |
| live | Toms | -21.77 | -0.38 |
| live | Drums | -18.33 | -0.60 |
| live | Other | -21.97 | -0.47 |
| live | Instrumental | -14.47 | -1.04 |
Measurement 4's tables below are **superseded by phase 9**, which made
`route_scale` match loudness rather than raw energy — the non-LFE total is no
longer 1.0 for send-routed stems. See `phase9_report.md` §2 for the current
7.1.4 numbers.

### 4. Zone energy fraction of stem input energy — balanced, stereo

| Stem | front | surround | height | LFE | non-LFE total |
|---|---|---|---|---|---|
| Lead Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Backing Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Bass | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Kick | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Snare | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Toms | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Drums | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Hi-Hat | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Ride | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Crash | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Guitar | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Piano | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Other | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Instrumental | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Crowd | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |

### 4. Zone energy fraction of stem input energy — balanced, 5.1

| Stem | front | surround | height | LFE | non-LFE total |
|---|---|---|---|---|---|
| Lead Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Backing Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Bass | 1.000 | 0.000 | 0.000 | 0.014 | 1.0000 |
| Kick | 1.000 | 0.000 | 0.000 | 0.018 | 1.0000 |
| Snare | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Toms | 0.999 | 0.001 | 0.000 | 0.001 | 1.0000 |
| Drums | 1.000 | 0.000 | 0.000 | 0.002 | 1.0000 |
| Hi-Hat | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Ride | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Crash | 0.972 | 0.028 | 0.000 | 0.000 | 1.0000 |
| Guitar | 0.932 | 0.068 | 0.000 | 0.000 | 1.0000 |
| Piano | 0.980 | 0.020 | 0.000 | 0.000 | 1.0000 |
| Other | 0.928 | 0.072 | 0.000 | 0.001 | 1.0000 |
| Instrumental | 0.993 | 0.007 | 0.000 | 0.004 | 1.0000 |
| Crowd | 0.009 | 0.991 | 0.000 | 0.000 | 1.0000 |

### 4. Zone energy fraction of stem input energy — balanced, 7.1.4

| Stem | front | surround | height | LFE | non-LFE total |
|---|---|---|---|---|---|
| Lead Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Vocals | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Backing Vocals | 0.911 | 0.000 | 0.089 | 0.000 | 1.0000 |
| Bass | 1.000 | 0.000 | 0.000 | 0.014 | 1.0000 |
| Kick | 1.000 | 0.000 | 0.000 | 0.018 | 1.0000 |
| Snare | 1.000 | 0.000 | 0.000 | 0.000 | 1.0000 |
| Toms | 0.999 | 0.001 | 0.001 | 0.001 | 1.0000 |
| Drums | 1.000 | 0.000 | 0.000 | 0.002 | 1.0000 |
| Hi-Hat | 0.878 | 0.000 | 0.122 | 0.000 | 1.0000 |
| Ride | 0.795 | 0.000 | 0.205 | 0.000 | 1.0000 |
| Crash | 0.701 | 0.000 | 0.299 | 0.000 | 1.0000 |
| Guitar | 0.924 | 0.067 | 0.009 | 0.000 | 1.0000 |
| Piano | 0.980 | 0.006 | 0.014 | 0.000 | 1.0000 |
| Other | 0.947 | 0.013 | 0.040 | 0.001 | 1.0000 |
| Instrumental | 0.983 | 0.000 | 0.017 | 0.004 | 1.0000 |
| Crowd | 0.000 | 0.699 | 0.301 | 0.000 | 1.0000 |

### 4b. Zone fraction averaged over all 16 stems, per preset (7.1.4)

| Preset | front | surround | height | LFE |
|---|---|---|---|---|
| balanced | 0.882 | 0.049 | 0.069 | 0.002 |
| intimate | 0.915 | 0.062 | 0.023 | 0.002 |
| stage | 0.868 | 0.049 | 0.084 | 0.002 |
| wide | 0.785 | 0.046 | 0.169 | 0.002 |
| immersive | 0.692 | 0.028 | 0.280 | 0.002 |
| live | 0.828 | 0.041 | 0.131 | 0.002 |

### 4c. Channels the preset does not request but the merged route keeps

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

## Reproducing

```
uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s
```

Deterministic: same numbers on every run, no model downloads, no fixtures.
