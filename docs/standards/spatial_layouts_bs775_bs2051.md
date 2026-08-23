# Spatial Layouts — ITU-R BS.775-4 and BS.2051-3

**Sources:**  
- Recommendation ITU-R BS.775-4 (12/2022) — Multichannel stereophonic sound system  
- Recommendation ITU-R BS.2051-3 (05/2022) — Advanced sound system for programme production

---

## Core Objective

Define normative loudspeaker positions (azimuth/elevation), channel labels, LFE constraints, downmix matrices, and Sound System designators for multichannel and immersive audio programme production and delivery.

---

## BS.775-4 — 3/2 Multichannel Layout

*(BS.775-4 §3)*

### Nominal loudspeaker positions

| Channel | Label | Azimuth (°) | Elevation (°) | Tolerance |
|---|---|---|---|---|
| Left | L | +30 | 0 | ±10° |
| Right | R | −30 | 0 | ±10° |
| Centre | C | 0 | 0 | ±5° |
| Left Surround | Ls | +110 | 0 | ±10° |
| Right Surround | Rs | −110 | 0 | ±10° |
| LFE | LFE | — | — | see Annex 7 |

> Azimuth positive = left when facing front. Surround speakers nominally ±110°; acceptable range +100°..+120° per BS.2051-3 Table 4.

### LFE Channel Constraints

*(BS.775-4 Annex 7)*

| Parameter | Value |
|---|---|
| Bandwidth limit | **120 Hz** (not 80 Hz, not 200 Hz) |
| Level offset | **−10 dB** relative to full-scale channels |
| Downmix | Excluded from L/R downmix sum by default |

> The −10 dB offset means 0 dBFS on the LFE channel reproduces at −10 dBFS relative to the main channels. Referenced in BS.2051-3 Table 1 Note 5.

### LFE lowpass

*(Project convention — Annex 7 fixes the 120 Hz bandwidth limit but names no
filter. Implemented once in
`packages/dsp/crates/dsp-core/src/kernels/butter.rs::linkwitz_riley_lowpass_sos`
and reached from every LFE producer: `upmixer_dsp.lfe_lowpass` for
`StemRouter.route`, the ADM writer, and the binaural / transaural LFE feed;
`stream::routing::LfeBus` for the preview.)*

The bus lowpass is a **Linkwitz-Riley** of `lfe_filter_order` (default 4,
i.e. two cascaded 2nd-order Butterworths), so `|H|` is the Butterworth
magnitude squared: **−6 dB at `lfe_cutoff_hz`**, same 24 dB/octave asymptote
as the 4th-order Butterworth it replaced. **The order must be even and ≥ 2**,
which the Rust design asserts.

Two things this does *not* do, both deliberate:

- **The mains keep their bass unfiltered.** No complementary high-pass is
  applied — bass management belongs to the playback system (Atmos music
  practice), and BS.775 §Downmix keeps LFE out of the stereo sum, so a
  redirect here would leak into a path the standard defines without it.
- **It does not make the LFE phase-coherent with the mains at the crossover.**
  With the mains full-range, LFE and mains overlap through the transition and
  a causal lowpass rotates only one of them: LR4 and Butterworth-4 both sit at
  −180° at `f_c`. The measured coincident sum near 120 Hz improves from
  −2.4/−2.7 dB (Butterworth-4, Bass/Kick) to −1.6/−1.8 dB purely because LR's
  −6 dB point puts less correlated energy into the overlap. Closing the rest
  needs an allpass on the LFE bus, rejected with numbers in
  `docs/plans/mixing/phase5_report.md` §3.

Level calibration is `UpmixConfig.lfe_gain` = 0.3162 (−10 dB), the complement
of the Annex 7 monitoring gain above; the preset `lfe` send weights are
referenced to that, so they are already compensated.

---

## Bass management — LF unification and redistribution

*(Implemented by `packages/core/src/mastering/bass.py` →
`packages/dsp/crates/dsp-core/src/mastering/bass.rs`; contracted stage order
in `docs/contracts/preview_export_parity.md` §1.)*

The mastering bass stage treats the low band as **one signal with one owner**:
it is extracted from every non-LFE channel before redistribution, rather than
left wherever the router put it. The reference architecture is the Dolby Atmos
Renderer's bass management ("handle bass extraction from object signals before
they are rendered to speaker feeds", 45–200 Hz).

### The crossover

The low band is taken with a zero-phase 2nd-order Butterworth low-pass at
`unify_hz`, clamped to **40–120 Hz** so the bus never carries content outside
the LFE's own bandwidth limit. The high band is the **exact complement**,
`high = x − low`, taken by subtraction rather than by a second filter: a
complementary LR4 pair does *not* sum flat once run zero-phase
(|LP|² + |HP|² = 0.5 at f_c), whereas subtraction is exact for any low-pass.
The residual therefore keeps whatever the filter did not capture, per channel,
which is inherent to a soft crossover and not a defect.

### The Σa = 1 invariant

`lf_bus` is the **unweighted sum** of the extracted low bands, so returning it
over target weights that sum to 1 preserves the coherent low-frequency level
exactly. This is the invariant the whole stage rests on: N channels carrying
correlated bass sum as pressure at 20·log₁₀(N) dB — +16.9 dB across a 7.1.4
floor bed — so redistributing without it is a level error, not a spread.

Spread sets (`LF_SPREADS`), weighted 1/N over the channels a layout actually
has:

| `spread` | destinations |
|---|---|
| `front` | FL, FR |
| `bed` | FL, FR, C, SL, SR, BL, BR |
| `all` | bed + TFL, TFR, TBL, TBR |

Degenerate layouts are safe by construction: on System A (0+2+0), `bed`
resolves to {FL, FR} at 1/2 each, which is numerically a no-op.

### LFE modes

| mode | mains | LFE | downmix |
|---|---|---|---|
| `off` | Σa = 1 | untouched, keeps its router content | unaffected |
| `add` | Σa = 1 | `+ lfe_send · lf_bus · g_auth` | safe — mains bit-identical to `off` |
| `split` | Σa = 1 − `lfe_send` | `+ lfe_send · lf_bus · g_auth` | **LF-light** |

`g_auth` is the −10 dB authoring gain above (`UpmixConfig.lfe_gain`). Playback
applies the matching +10 dB replay gain, so `split` conserves the coherent sum
end to end:

```
mains(1 − w) + w · g_auth · 10^(10/20)  =  (1 − w) + w  =  1
```

`split` is LF-light on fold-down precisely because the downmix matrices below
carry **no LFE term** — that is the documented tradeoff, not a regression, and
it is why the mode is explicit rather than implied.

Since `unify_hz` ≤ 120 Hz, the bus is already inside the LFE band and needs no
separate band-limiting filter on the send. The harmonic exciter is deliberately
kept **off** the LFE path: tanh's third and fifth harmonics of a 40–80 Hz
fundamental land at 120–400 Hz, above what the channel may carry.

### Mid-bass decorrelation (100-300 Hz)

*(Implemented by `packages/dsp/crates/dsp-core/src/mastering/decorrelate.rs`,
mirrored for the preview by `stream::master::StreamingDecorrelator`.)*

Below ~80 Hz, decorrelating across channels is inaudible at best and comb
filters at worst (Welti & Devantier, JAES 54(5) 2006) — which is what the mono
`lf_bus` above is for. The "deep, enveloping" multichannel low end the same
literature describes comes from a *different* band: **100-300 Hz, decorrelated
per channel**. That is what this stage does, and it is the second half of the
bass-management effect.

Per non-LFE channel, a cascade of `DECORR_SECTIONS` (32) 2nd-order allpass
sections, seeded per channel index so every speaker gets its own pole set while
the whole stage stays reproducible:

- **Pole angles** are spaced at constant density on the Glasberg & Moore
  **ERB-rate** scale across 100-300 Hz, with a per-section jitter — roughly one
  pole per critical band rather than per hertz (Kermit-Canfield & Abel,
  "Signal Decorrelation Using Perceptually Informed Allpass Filters", DAFx-16).
  100-300 Hz spans only ~4.4 ERB units, which is why 8 sections suffice; the
  paper's 500/1000-section smearing thresholds are full-band figures and do not
  apply here.
- **Channels are staggered deterministically** across the group-delay budget
  (`DELAY_STAGGER`) rather than each drawing from one common distribution.
  Independent draws converge on the same average response over a band this
  narrow: measured mean pairwise |correlation| across eight channels got
  *worse* with more sections, 0.64 at 8 sections rising to 0.95 at 128.
- **Pole radius** is drawn in `[0.5, r_max)`, with `r_max` set from the
  `DECORR_MAX_DELAY_MS` (30 ms) ceiling via a section's peak group delay
  `2(1+r)/(1-r)`, and hard-capped at 0.95. Past ~30 ms the cascade reads as a
  room rather than as width.

**Only the sustained component is decorrelated.** A fast/slow envelope pair on
the band (30 ms / 300 ms) yields a sustain weight `clamp(slow/fast, 0, 1)`: an
onset drives the fast envelope above the slow one and gates the cascade out, so
transients pass unsmeared. Both envelopes are primed on the first sample —
started cold, the slow one's rise reads a programme's opening as one long onset.

### Why the band split is zero-phase

Reconstruction is `x - band + allpass(band)`, whose response is
`1 + B(w)*(A(w) - 1)`. Inside the pass band `|B| = 1`, and that collapses to
`|A| = 1` **only when `B` is real**. Run causally, `B` carries its own phase lag
and the rotated copy beats against the residual — a measured **3 dB dip at
200 Hz**. So the band is taken zero-phase, the same discipline `lf_unify` uses
for its low band.

Two consequences follow:

- The band-pass is **4th order**. A 2nd-order pass never reaches unity across
  only 1.6 octaves, which spreads ripple over the whole band instead of its
  edges.
- Its zero-phase pass needs its own, longer horizon than the unifier's:
  `DECORR_HORIZON_MS` = **300 ms** against the unifier's 100 ms. A 4th-order
  100-300 Hz band-pass is still 2e-6 of peak at 100 ms where the unifier's
  2nd-order low-pass is at 8e-20; truncating there showed up directly as a
  block-size dependence around 1e-8 in the preview.

At the two -3 dB skirts `|B| ~ 0.71`, so some ripple there is inherent to
replacing a band with a phase-rotated copy of itself. It scales with
`decorrelate` and is the documented cost of the stage, not a defect.

### What this stage can and cannot do

Decorrelating a band only ~180 Hz wide is physically bounded. Two channels
decorrelate when their group-delay difference satisfies `dtau * bandwidth >~ 1`,
which here means **~5.6 ms of separation per channel pair**. The 30 ms ceiling
therefore supplies enough separation for a handful of distinct classes, not for
eleven independent ones — `DELAY_STAGGER` has six, and a 7.1.4 bed wraps.

Measured on band-limited noise, mean pairwise |correlation| across eight
channels, and in-band level error at partial depth:

| sections | mean \|corr\| | level at depth 0.25 | level at depth 0.5 |
|---|---|---|---|
| 8 | 0.64 | −2.1 dB | −2.7 dB |
| 16 | 0.57 | +0.7 dB | +0.8 dB |
| 32 | 0.49 | +0.1 dB | +0.1 dB |
| 96 | 0.37 | −0.2 dB | −0.3 dB |

Below ~32 sections the cascade's output is still *anti-correlated* with the dry
band (−0.31 at 8 sections), so the blend cancels rather than spreads and the
in-band level drops. 32 is the smallest count at which the level holds. So the
stage **reduces** the coherent sum rather than eliminating it, and that is a
bound of the band, not a tuning shortfall.

### What it does to level

The cascade is unity-magnitude, so **each channel keeps its own level** (within
about a dB — the sustain gate settles near 0.97 on steady tones, and a partial
blend of two decorrelated signals sits a few tenths down). What drops is the
**coherent sum at the listening position**: two independently-allpassed copies
add at ~3 dB rather than 6. That reduction *is* the enveloping effect, and it is
the deliberate inverse of the `Sigma-a = 1` invariant the unified band below
holds to.

The band never reaches below the unifier: the low corner is clamped up to
`unify_hz`, and the stage disables itself outright if that leaves no band. So
`Sigma-a = 1` is untouched — the two bands are disjoint by construction, which
is also what lets the delta be derived from the pre-unification signal and the
preview read its look-ahead from the same queue the unifier uses.

Every shipped profile ships with `decorrelate = 0`. It is opt-in via
`mastering_bass_decorrelate` / `--mastering-bass-decorrelate` / the web panel's
Placement > Width control.

### Known asymmetry

The EQ and reference-match stages skip LFE for their spectral correction
(deliberate — a channel band-limited to 120 Hz has no meaningful ratio against
a full-range reference), and the bus compressor bypasses LFE entirely including
makeup gain. So the bed's low band reaches bass control EQ'd and compressed
while the LFE has not been. The Σa = 1 conservation still holds exactly *within*
the stage; this is pre-existing bed/LFE balance and is not compensated for in
the unifier.

---

## BS.775-4 — Downmix Matrices

*(BS.775-4 Annex D)*

### 3/2 → 2/0 (5.1 to stereo)

```
Lo = L + C·a₀ + Ls·b₀
Ro = R + C·a₀ + Rs·b₀
```

| Coefficient | Recommended value | dB |
|---|---|---|
| a₀ (centre) | 0.707 | −3.01 dB |
| b₀ (surround) | 0.707 | −3.01 dB |

Alternative b₀ = 0.500 (−6.02 dB) for surround content with heavy rear activity.

LFE handling in downmix: excluded from the default 2/0 sum unless explicitly combined.

### Height fold-down (project convention, outside BS.775)

BS.775-4 predates height channels and defines no coefficient for them, so a
literal reading of Annex 4 drops TFL/TFR/TBL/TBR from the 2/0 and 1/0
downmixes. This project does not: the routing presets put the majority of some
stems' energy overhead (Crash 0.86 of its routed energy on `wide`), and
dropping it made the written stereo downmix a different mix from the stereo
*render* of the same track, which folds heights.

The convention adopted here is the common Atmos re-render practice — front
heights fold onto the front pair, back heights onto the surround pair, each at
a height coefficient `k_h`:

```
Lo = FL + a₀·C + k_s·(SL + a₀·BL) + k_h·(TFL + k_s·TBL)
Ro = FR + a₀·C + k_s·(SR + a₀·BR) + k_h·(TFR + k_s·TBR)
Mo = a₀·(FL + FR + k_h·(TFL + TFR)) + C
     + k_s·(SL + SR + a₀·(BL + BR) + k_h·(TBL + TBR))
```

| Coefficient | Default | dB | Configurable as |
|---|---|---|---|
| k_h (height) | 0.7071 | −3.01 dB | `config.height_downmix_coeff`, `format.downmix.height_coeff`, `--downmix-height-coeff` |

`k_h = 0.0` reproduces the standard's height-free matrices exactly. Back
heights arrive through `k_s` as well, matching how BS.775 already treats back
surrounds relative to sides. Implemented once in `dsp-core`'s
`itu_downmix_stereo`/`itu_downmix_mono`, so the export path and the preview's
stereo monitoring path apply the same matrix.

This is a *level* law, unlike the render path's `fold_route_to_stereo`
(a pan law, see "Stem-route folding is a pan law, not a level law" below): the
two stereo paths now agree that heights are audible in stereo and differ only
in the per-stem renormalization the render path applies afterwards. Measured
residual per stem: `docs/plans/mixing/phase4_report.md`.

### 5.1 re-render fold (measurement programme, not a delivery format)

*(`dsp-core`'s `spatial::downmix::FoldTo51`. Used only to build the
programme integrated loudness is measured on — see
`loudness_dsp_bs1770.md` §"Measurement programme". Nothing writes this
fold to a file.)*

Back surrounds fold into the side pair at BS.775-4 Annex D's `b₀`; front
and back heights fold onto their base-layer channels at the project's
`k_h`, the same convention the 2/0 matrix above uses. Both are 1/√2:

```
FL' = FL + k_h·TFL
FR' = FR + k_h·TFR
C'  = C
SL' = SL + b₀·BL + k_h·TBL
SR' = SR + b₀·BR + k_h·TBR
```

| Coefficient | Value | dB |
|---|---|---|
| b₀ (back → side) | 0.7071 | −3.01 dB |
| k_h (height → base layer) | 0.7071 | −3.01 dB |

Unlike the 2/0 downmix coefficients these are **not configurable**: the
re-render they build is a fixed programme a delivery specification names,
not a monitoring choice. LFE has no fold contribution — BS.1770 weights it
zero, so it never reaches the measurement either way.

### Fold QC thresholds (project convention, measurement only)

*(`packages/core/src/mastering/foldqc.py`, run by `MasteringChain` after the
limiter. Reported as `MasteringResult.folds` / `UpmixResult.folds`.)*

The limiter's true-peak guarantee is **per channel and does not survive a
fold**: the downmixes above are linear mixes, so `Lo = FL + k_s·SL` on
correlated in-phase content is `1.7071·FL`, 4.65 dB of headroom nothing in
the chain budgeted for. Integrated loudness moves the other way — a fold
collapses channels the BS.1770 weighted sum was counting separately, so it
usually reads *quieter* than the bed.

Three folds are measured against the delivered bed's own integrated loudness:

| field | programme | measured when |
|---|---|---|
| `folds.stereo` | the 2/0 downmix above, at the configured `k_s`/`k_h` | bed wider than 2 channels |
| `folds.surround_51` | the 5.1 re-render below | bed wider than 5.1 |
| `folds.binaural` | the finished binaural render of this bed (`render_binaural_delivery`) | `config.qc_measure_binaural`, unset = the `BINAURAL_BED_FORMATS` beds |

Two conditions warn. **Nothing is corrected** — the mitigation is the mix or
the mastering settings, not a hidden second limiter, so a fold-referenced
re-limit is deliberately out of scope.

| condition | threshold |
|---|---|
| fold true peak over the delivery target's ceiling | the target's own `max_tp_dbtp` |
| fold integrated loudness away from the bed's | `FOLD_DIVERGENCE_LU` = **±1.5 LU** |

**Evidence for ±1.5 LU** (`docs/plans/mastering/phase0_report.md` audit 1,
`phase1_report.md` §"What the fold changes", `phase8_report.md` fold tables —
reproduce with `uv run pytest
packages/core/tests/test_master_measurement.py -m perf -s`):

- Realistic decorrelated programme material folds by at most **1.27 LU**
  (7.1.4 → stereo; 5.1 → stereo is 0.63–0.67 LU, 7.1.4 → 5.1 is a systematic
  0.32 LU). The threshold sits just above the worst realistic case, so normal
  content never warns.
- Material living entirely overhead folds by **2.35 LU** (5.1 re-render) to
  **3.98 LU** (stereo) — the case a height-heavy mix really can fail, and the
  reason the threshold has to be below 2 LU to catch it.
- The loosest published delivery tolerance any target in
  `mastering/delivery.py` carries is **±2 LU** (Netflix, ATSC A/85). Warning
  at 1.5 LU fires before a fold could put the master outside the tolerance of
  the spec it was mastered to; EBU R128's ±0.5 LU is tighter than the
  systematic 5.1-fold bias itself, so it is not a usable bound here.

The binaural row measures the **finished** render rather than the raw HOA
collapse. `render_binaural` applies no level calibration for the number of
speakers it collapses, so twelve decorrelated channels sum into two ears at
roughly their energy sum: +10.2 to +10.3 LU and +3.1 to +4.7 dBTP on both
phase 0 programmes. That number describes the renderer's constant, not the
master, so it would fire on every export. `render_binaural_delivery`'s
correction is capped at `BINAURAL_LOUDNESS_MAX_GAIN_DB` upward, which is what
makes the delivered row informative: a bed whose collapse lands quiet cannot
be brought back and warns (−6.55 LU on a correlated front-heavy fixture).

### 3/2 → 3/0 (5.1 to 3.0)

```
Lo = L + Ls·b₀
Ro = R + Rs·b₀
Co = C
```

Same b₀ coefficient as above.

---

## BS.2051-3 — Sound System Designators

*(BS.2051-3 Table 1, Tables 3–14)*

Format: **Upper + Middle + Bottom** loudspeaker counts.

| System | Designation | Channels | LFE |
|---|---|---|---|
| A | 0+2+0 | L R | — |
| B | 0+5+0 | L R C Ls Rs | LFE1 |
| C | 2+5+0 | L R C Ls Rs Ltf Rtf | LFE1 |
| D | 4+5+0 | L R C Ls Rs Ltf Rtf Ltr Rtr | LFE1 |
| E | 4+5+1 | L R C Ls Rs Ltf Rtf Ltr Rtr Cbf | LFE1 |
| F | 3+7+0 | C L R LH RH LS RS LB RB CH | LFE1 LFE2 |
| G | 4+9+0 | L R C Lss Rss Lrs Rrs Ltf Rtf Ltb Rtb Lsc Rsc | LFE1 |
| H | 9+10+3 | FC FL FR FLc FRc BC SiL SiR BL BR + 10 mid + 3 bottom | LFE1 LFE2 |
| I | 0+7+0 | L R C Lss Rss Lrs Rrs | LFE1 |
| J | 4+7+0 | L R C Lss Rss Lrs Rrs Ltf Rtf Ltb Rtb | LFE1 |
| Z | headphones | HPL HPR | — |

> Systems A, B, Z shall be used with audio-related metadata (BS.2051-3 §3).

---

## BS.2051-3 — SP Label Reference

*(BS.2051-3 Table 1)*

SP Labels encode layer + azimuth: `M` = middle layer (ear level), `U` = upper layer (+30° elevation), `B` = bottom layer (−30° elevation), `T` = top (zenith), `UH` = upper-high (+45° elevation).

### Middle layer (M, elevation = 0°)

| SP Label | Azimuth (°) | Channel |
|---|---|---|
| M+000 | 0 | C (Centre) |
| M+030 | +30 | L (Left) |
| M-030 | −30 | R (Right) |
| M+022 | +22.5 | — |
| M-022 | −22.5 | — |
| M+045 | +45 | — |
| M-045 | −45 | — |
| M+060 | +60 | FL (System H) |
| M-060 | −60 | FR (System H) |
| M+090 | +90 | Lss / SiL |
| M-090 | −90 | Rss / SiR |
| M+110 | +110 | Ls (Left surround) |
| M-110 | −110 | Rs (Right surround) |
| M+135 | +135 | Lrs / LB |
| M-135 | −135 | Rrs / RB |
| M+180 | +180 | BC (Back centre) |
| M+SC | Left screen edge | Lsc |
| M-SC | Right screen edge | Rsc |

### Upper layer (U, elevation = +30°)

| SP Label | Azimuth (°) | Channel |
|---|---|---|
| U+030 | +30 | Ltf (Left top front) |
| U-030 | −30 | Rtf (Right top front) |
| U+045 | +45 | LH / Ltf |
| U-045 | −45 | RH / Rtf |
| U+090 | +90 | TpSiL (System H) |
| U-090 | −90 | TpSiR (System H) |
| U+110 | +110 | Ltr (Left top rear) |
| U-110 | −110 | Rtr (Right top rear) |
| U+135 | +135 | Ltb (Left top back) |
| U-135 | −135 | Rtb (Right top back) |
| U+180 | +180 | TpBC (System H) |
| UH+180 | +180 | CH (Centre height, elevation +45°) |

### Bottom layer (B, elevation = −30°)

| SP Label | Azimuth (°) | Channel |
|---|---|---|
| B+000 | 0 | BtFC / Cbf |
| B+045 | +45 | BtFL |
| B-045 | −45 | BtFR |

### Special positions

| SP Label | Position | Channel |
|---|---|---|
| T+000 | Zenith (+90° elevation) | TpC (top centre) |
| LFE1 | System-dependent (see Tables 3–12) | LFE / LFE1 |
| LFE2 | System-dependent | LFE2 (Systems F, H) |
| HP_L | N/A | HPL (headphone left) |
| HP_R | N/A | HPR (headphone right) |

---

## System B (0+5+0) — Per-Channel SP Labels

*(BS.2051-3 Table 4 — matches BS.775-4 3/2 layout)*

| SP Label | Channel Label | Name | Azimuth Range | Elevation Range |
|---|---|---|---|---|
| M+030 | L | Left | +30 | 0 |
| M-030 | R | Right | −30 | 0 |
| M+000 | C | Centre | 0 | 0 |
| LFE1 | LFE | Low frequency effects | — | — |
| M+110 | Ls | Left surround | +100..+120 | 0..+15 |
| M-110 | Rs | Right surround | −100..−120 | 0..+15 |

---

## System D (4+5+0) — Per-Channel SP Labels

*(BS.2051-3 Table 6)*

| SP Label | Channel Label | Name | Azimuth Range | Elevation Range |
|---|---|---|---|---|
| M+030 | L | Left | +30 | 0 |
| M-030 | R | Right | −30 | 0 |
| M+000 | C | Centre | 0 | 0 |
| LFE1 | LFE | Low frequency effects | — | — |
| M+110 | Ls | Left surround | +100..+120 | 0 |
| M-110 | Rs | Right surround | −100..−120 | 0 |
| U+030 | Ltf | Left top front | +30..+45 | +30..+55 |
| U-030 | Rtf | Right top front | −30..−45 | +30..+55 |
| U+110 | Ltr | Left top rear | +100..+135 | +30..+55 |
| U-110 | Rtr | Right top rear | −100..−135 | +30..+55 |

---

## System G (4+9+0) — Per-Channel SP Labels

*(BS.2051-3 Table 9 — closest to Dolby Atmos 7.1.4)*

| SP Label | Channel Label | Name | Azimuth Range | Elevation Range |
|---|---|---|---|---|
| M+030 | L | Left | +30..+45 | 0 |
| M-030 | R | Right | −30..−45 | 0 |
| M+000 | C | Centre | 0 | 0 |
| LFE1 | LFE | Low frequency effects | — | — |
| M+090 | Lss | Left side surround | +85..+110 | 0 |
| M-090 | Rss | Right side surround | −85..−110 | 0 |
| M+135 | Lrs | Left rear surround | +120..+150 | 0 |
| M-135 | Rrs | Right rear surround | −120..−150 | 0 |
| U+045 | Ltf | Left top front | +30..+45 | +30..+55 |
| U-045 | Rtf | Right top front | −30..−45 | +30..+55 |
| U+135 | Ltb | Left top back | +100..+150 | +30..+55 |
| U-135 | Rtb | Right top back | −100..−150 | +30..+55 |
| M+SC | Lsc | Left screen | Left screen edge | 0 |
| M-SC | Rsc | Right screen | Right screen edge | 0 |

---

## System A (0+2+0) as an output layout

`FORMAT_MAP["stereo"]` (`packages/core/src/formats.py`, `STEREO_OUT`) makes
System A a selectable delivery target alongside the surround beds, so the
stem-separation, rebalance, EQ and mastering chain can produce a two-channel
master. `OutputFormat.bs2051_system` returns `"A"` for it.

It is a *layout*, not a rendering pass: unlike `BINAURAL`/`TRANSAURAL` it lives
in `FORMAT_MAP` and its two channels are the real writer slots. Consequences
enforced by `formats.validate_delivery`:

- `format.type` must be `multichannel`. ADM-BWF requires a surround bed (see
  [ADM metadata](adm_metadata_bs2076.md)), and `binaural`/`transaural` require a
  height-bearing bed (`BINAURAL_BED_FORMATS`/`TRANSAURAL_BED_FORMATS`).
- The BS.775 stereo companion (`format.downmix`) is skipped: the companion of a
  stereo master is the master.

### Input folding

`can_upmix` accepts any input for a two-channel output. Mono duplicates to
FL/FR, stereo passes through, and anything wider folds through
`itu_downmix_stereo` (BS.775-4 Annex 4 Table 2, above) before processing, at
the front of `StemUpmixPipeline`'s separation, so a folded run separates a
single `front` zone. The fold marks the stem cache identity (`|stereo`), since
the same file otherwise yields `@zone`-keyed stems.

### Stem-route folding is a pan law, not a level law

`stem_router.fold_route_to_stereo` collapses a speaker map onto FL/FR: `C`
splits at 1/√2 into both sides, `LFE` is dropped, and every other left/right
channel sums at unity into its own side — deliberately *not* BS.775's `k_s`.
`StemRouter.route` renormalizes each stem to its own loudness afterwards
(`route_scale`, BS.1770-weighted since mixing phase 9), so only the resulting
L/R ratio survives; the surround/height coefficients would be discarded anyway.

Without the fold a 2-channel format silently drops audio: the `surround`,
`back` and `height_*` zone routes carry no FL/FR entries at all, and
`DEFAULT_ROUTING["Crowd"]` has no FL/FR/C either.

### Pan law

`stem_router.apply_stem_pan` positions a stem between its two speakers with a
constant-power law, preserving the FL/FR pair's combined magnitude:

```
FL = m·cos(pan·π/2),  FR = m·sin(pan·π/2),  m = hypot(FL, FR)
pan = atan2(FR, FL) / (π/2)
```

Pan is derived from `mixing.stem_routing`, not stored separately. The web
mirror is `stemPan`/`panWeights` in `apps/web/src/lib/spatial.ts`; the CLI
surface is `--stem-pan STEM=VALUE`.

---

## Stem placement and the routing presets

Routing presets are not per-layout gain tables. Each preset in
`packages/core/src/separation/stem_placement.py` holds one canonical
`StemPlacement` per stem — image centre `azimuth_deg`/`elevation_deg` in the
geometry convention above (0° = front, positive azimuth = left, positive
elevation = up), an image `width_deg`, a per-source blur `spread_deg`, and an
LFE send. `preset_routing` realizes that table on one `FORMAT_MAP` layout.

### Realization rules

| stage | rule |
|---|---|
| projection | A layout with no height pair cannot carry an elevated placement. Zeroing the elevation alone would pull the stem *inward* onto the front wall, so the lost elevation is spent on image width instead (`HEIGHT_FLATTEN_WIDTH_FACTOR`) and overhead content wraps to the sides and rear. Azimuth is never clamped — the panner already projects a direction the layout does not span onto its hull edge. |
| panning | MDAP (`stem_panner.py`). The image renders as virtual sources every `VIRTUAL_SOURCE_STEP_DEG` across `azimuth ± width/2` (one when `width` is 0), each blurred to either side by `SPREAD_RING_FACTOR · spread_deg`. Every source is panned by VBAP onto a facet of the speakers' convex hull; the gain vectors sum and the map is L2-normalized (constant power). Sends below `MINIMUM_SEND` are dropped and the rest renormalized. |
| out of hull | Elevation is clamped to what the layout spans — nothing below the horizontal plane, nothing above the height layer. A direction no facet holds takes the facet with the least negative gain, negatives clamped to zero, which projects it onto the nearest hull edge; a two-speaker bed's rear half, which has no such edge, falls back to cosine-similarity weighting so the placement stays audible and symmetric. |
| flat facets | A rear wall or height layer of four coplanar speakers admits both diagonals as hull facets. Every facet holding the direction contributes, averaged — each reproduces the direction exactly, so the mean does too, and the gains stay continuous where the choice would otherwise flip. |
| two-channel | `stereo` resolves against `7.1.4` and is folded by `fold_route_to_stereo` — see "Stem-route folding is a pan law, not a level law" above. |
| LFE | Positional math never sees it: the send is copied from the placement, unscaled. |

The same panner serves a dragged scene position
(`apps/api/.../projects/routing.py`, a zero-width placement at
`SCENE_PLACEMENT_SPREAD_DEG`), so a hand-placed stem and a preset-placed one
are positioned by identical maths. The browser has no panner of its own: the
preview reads the routing maps the core produced.

`DEFAULT_ROUTING` — the fallback route when nothing else supplies one — is the
default preset realized on `7.1.4`, not a separate table.

---

## Validation Checklist

- [ ] BS.775-4: L/R nominal ±30° (tolerance ±10°); deviation stored in metadata
- [ ] BS.775-4: C nominal 0° (tolerance ±5°)
- [ ] BS.775-4: Ls/Rs nominal ±110°; acceptable range ±100°..±120° per BS.2051-3 Table 4
- [ ] LFE bandwidth limit = 120 Hz (not 80 Hz or 200 Hz) — per BS.775-4 Annex 7
- [ ] LFE level offset = −10 dB relative to full-scale channels — per BS.775-4 Annex 7 / BS.2051-3 Table 1 Note 5
- [ ] LFE excluded from downmix sum unless explicitly included
- [ ] Bass management: LF redistribution weights sum to 1 (coherent level preserved, no N-channel buildup)
- [ ] Bass management: `unify_hz` clamped to 40–120 Hz so the bus stays inside the LFE bandwidth limit
- [ ] Bass management: an LFE share carries the −10 dB authoring gain, so playback's +10 dB restores it exactly
- [ ] Bass management: `split` is flagged as downmix-lossy; `add` leaves the mains bit-identical to `off`
- [ ] Bass management: decorrelation stays at or above `unify_hz`, so the mono LF bus and its Sigma-a = 1 invariant are untouched
- [ ] Bass management: the decorrelator's band split is zero-phase (a causal split combs ~3 dB at 200 Hz)
- [ ] Bass management: allpass group delay is capped at 30 ms, past which the cascade reads as reverb rather than width
- [ ] Fold QC warns, never corrects: no fold-referenced re-limiting
- [ ] Fold QC divergence threshold = ±1.5 LU, below the loosest published delivery tolerance (±2 LU) and above the worst realistic fold (1.27 LU)
- [ ] Downmix centre coefficient a₀ = 0.707 (−3.01 dB) default
- [ ] Downmix surround coefficient b₀ = 0.707 (−3.01 dB) default; alternative 0.500
- [ ] Sound system designator uses U+M+B format (e.g. 4+5+0 for System D)
- [ ] SP labels use correct prefix: M= middle (0° elev), U= upper (+30°), B= bottom (−30°), T= zenith (+90°)
- [ ] Azimuth convention: positive = left when facing front
- [ ] Elevation convention: positive = up from horizontal plane
- [ ] System B SP label for Ls = M+110 (±100..±120° range, not fixed ±110°)
- [ ] LFE1/LFE2 positions per per-system tables (Tables 3–12), not fixed azimuth
- [ ] Upper-layer height channels: U+030/U-030 = Ltf/Rtf; U+110/U-110 = Ltr/Rtr (Systems C, D, E, J)
- [ ] For Systems G, J: upper-layer channels are U+045/U-045 = Ltf/Rtf; U+135/U-135 = Ltb/Rtb
- [ ] System Z (headphones): SP labels HP_L, HP_R; no azimuth/elevation
