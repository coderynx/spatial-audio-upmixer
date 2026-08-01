# Transaural Speaker Rendering — Crosstalk-Cancellation Contract

**Source:** Internal design, modeled on published crosstalk-cancellation
(XTC) literature — the Atal-Schroeder crosstalk canceller, the Cooper/Bauck
"shuffler" simplification for symmetric listener geometry, and Princeton
3D3A Lab's BACCH-filter work on frequency-dependent regularization to bound
spectral coloration (see References). No proprietary BACCH filter design or
other measured/licensed XTC dataset is reproduced here — the regularization
curve in §4 is this engine's own documented heuristic, built from the same
public *principles* those sources describe, not a transcription of any
paper's exact formula.
**Scope:** Rendering a discrete multichannel bed to speaker-ready,
crosstalk-cancelled stereo for real stereo loudspeakers. This document is
the signed contract between the core engine (`packages/core/src/crosstalk/`) and the
web preview (`apps/web/src/features/projects/previewGraph.ts`'s
`buildCrosstalkGraph`, `audioEngine.ts`) — both must implement it identically
at the parameter level (see §6 for what "identically" means). It assumes
familiarity with [Spatial Audio Engine — Binaural Rendering
Contract](spatial_audio_engine.md), whose anechoic (`flat`) ear-signal render
this engine reuses as its input.

---

## 1. Why crosstalk cancellation, and why five profiles

Binaural audio (§0 of the sibling doc) is built to be heard with the left
signal reaching only the left ear and the right signal only the right ear —
true on headphones, false on loudspeakers. Playing a binaural signal on two
speakers lets the left speaker leak into the right ear and vice versa
("crosstalk"), which destroys the interaural time/level differences the
binaural encode relies on and collapses the 3D image. **Transaural**
playback is binaural-over-speakers with that leakage pre-cancelled: each
speaker feed is filtered by the regularized inverse of the speaker-to-ear
acoustic path, so after the real acoustic crosstalk happens, the signal that
actually arrives at each ear is (approximately) the original uncancelled
binaural signal again.

Five profiles cover distinct real playback geometries:

| Profile | Purpose | Speaker span | XTC depth |
|---|---|---|---|
| `stereo` | Standard hi-fi speaker pair, symmetric listening position | Wide (±30°) | Deepest — a wide, symmetric span is the best-conditioned case for XTC |
| `smart_speaker` | Single cabinet, narrow dual-driver span (soundbar / smart speaker) | Narrow (±12°) | Shallow, by design — a narrow span makes deep low-frequency cancellation expensive (see §4); voicing leans on stereo widening to compensate the narrower physical image instead |
| `car` | Off-center driver-seat listening position | Wide, asymmetric (+22°/−42°) | Moderate — an asymmetric 2x2 matrix, not the symmetric shuffler simplification |
| `laptop` | Built-in chassis speakers near the front edge, near-field desk listening | Narrow (±14°) | Shallow — slightly less regularized than `smart_speaker` since the span is a bit wider, but still far short of `stereo`'s depth |
| `phone` | Built-in handset speakers, near-field handheld listening | Narrowest (±6°) | Shallowest of all five — the most ill-conditioned span, so cancellation depth is sacrificed hardest for coloration safety; voicing does the most compensating work |

Unlike the binaural engine's three profiles (which vary *room coloration*
on a fixed geometry), these five profiles vary *speaker geometry* — the
physical layout is the whole story a transaural render has to correct for.
`laptop` and `phone` follow the same narrow-span tradeoff `smart_speaker`
established: geometry alone can't be fixed, so voicing (§6) compensates
perceptually instead.

---

## 2. Signal graph

```
mastered bed (5.1.4 / 7.1.2 / 7.1.4)
  │
  ▼
render_binaural(bed, profile="flat")            [reuse — see spatial_audio_engine.md §1]
  (anechoic HRTF ear signals; LFE already folded in pre-voicing, no room tail)
  │
  ▼
ear_L, ear_R
  │
  ▼
2x2 crosstalk-cancellation FIR matrix            [§4]
  speaker_L = conv(ear_L, H_LL) + conv(ear_R, H_LR)
  speaker_R = conv(ear_L, H_RL) + conv(ear_R, H_RR)
  │
  ▼
profile voicing chain (bass/presence shelf → M/S widen)      [§5, reuses binaural's voicing chain unchanged]
  │
  ▼
delivery: BS.1770 loudness correction (small, bed-preserving — see §7) → soft-limit (safety net, last) → 2ch WAV
```

The base ear-signal render always uses binaural's **`flat`** (anechoic)
profile, never `studio`/`listening` — a real room and a real pair of speaker
cabinets already supply whatever reverberant coloration the listening space
has; baking in a synthetic room tail on top of that would double-color the
result. This is the one structural difference from the binaural engine's own
signal graph: everything from the HOA encode through the anechoic ear
signals is identical and literally reused (`render_binaural`), not
reimplemented.

Core entry point: `packages/core/src/crosstalk/renderer.py::render_crosstalk`. Web
mirror: `buildCrosstalkGraph` (`previewGraph.ts`), which internally calls
`buildBinauralGraph(ctx, "flat")` for the ear-signal stage and exposes the
same `preVoicing` LFE-injection contract the binaural graph does (Ledger
D11 in `docs/contracts/preview_export_parity.md`).

---

## 3. Speaker geometry

Each profile fixes two listener-relative speaker azimuths in degrees (0 =
dead ahead, positive = left — the same convention as
`packages/core/src/binaural/geometry.py`). Elevation is always 0 (ear-level speakers,
a documented simplification — see §8). Symmetric profiles set
`azimuth_right_deg == -azimuth_left_deg`; `car` does not, since an
off-center driver-seat position genuinely has four independent
speaker-to-ear paths, not a mirror pair.

| Profile | Left speaker azimuth | Right speaker azimuth |
|---|---|---|
| `stereo` | +30° | −30° |
| `smart_speaker` | +12° | −12° |
| `car` | +22° | −42° |
| `laptop` | +14° | −14° |
| `phone` | +6° | −6° |

Source of truth: `packages/core/src/crosstalk/profiles.py::XTC_PARAMS` (`XtcParams`
dataclass) and `packages/core/src/crosstalk/geometry.py::speaker_azimuths_rad`.

---

## 4. Speaker-to-ear model and XTC filter design

### 4.1 Speaker-to-ear transfer matrix

The acoustic path from each speaker to each ear is synthesized with the
**same parametric spherical-head HRTF model** the binaural engine's decode
filters use — Woodworth ITD + frequency-dependent head-shadow ILD lowpass —
now promoted to a shared module, `packages/core/src/binaural/head_model.py::synth_hrir`,
imported by both `scripts/build_binaural_filters.py` and
`scripts/build_crosstalk_filters.py` so the two spatial-audio targets never
drift onto two different head models. For a profile's left speaker at
azimuth `θ_L` and right speaker at `θ_R`:

```
C_LL, C_RL = synth_hrir(θ_L, 0, sr, n_taps)   # left speaker: (left ear=ipsi, right ear=contra)
C_LR, C_RR = synth_hrir(θ_R, 0, sr, n_taps)   # right speaker: (left ear=contra, right ear=ipsi)
```

giving the 2x2 (ear × speaker) impulse-response matrix `C`.

### 4.2 Regularized inverse

The crosstalk canceller `H` must satisfy `C @ H ≈ I` (feeding the cancelled
signal through the real acoustic path reconstructs the intended ear signal).
A naive inverse blows up wherever `C` is ill-conditioned — at low frequency
(where a small head produces almost no interaural difference) and for
narrow speaker spans generally — producing large, audible spectral
coloration on the speaker feeds. Per-frequency-bin **Tikhonov
regularization** bounds this:

```
H(f) = C(f)^H · (C(f)·C(f)^H + β(f)·I)^-1
```

`β(f)` is a smooth, profile-specific curve (`beta_mid` in the well-behaved
mid-band, raised at low frequency via `low_boost_hz`/`low_boost_factor` and
above the head-shadow onset via `high_boost_hz`/`high_boost_factor` — see
`packages/core/src/crosstalk/profiles.py::XtcParams`). Raising `β` trades cancellation
depth for flatness; the profile's speaker span sets how much depth is
achievable before that tradeoff bites (§1's per-profile summary). This
curve is this engine's own heuristic, tuned to keep ipsilateral (same-ear)
response within a few dB of flat while suppressing contralateral leakage —
it is not a reproduction of the BACCH filter's own (unpublished in detail)
regularization scheme; see §8 for the honest provenance note on this point.

`H(f)` is IFFT'd with a bulk delay (to keep the generally non-causal inverse
inside a causal, finite window) and windowed to the profile's tap count,
giving the four time-domain FIRs `H_LL, H_LR, H_RL, H_RR`.

### 4.3 Objective correctness check

Because this is a from-scratch regularization design (not a reproduction of
a validated published filter), no crosstalk-cancellation change ships
without confirming, per profile, both halves of the tradeoff on the
synthesized `C`/`H` pair: contralateral leakage energy is reduced relative
to no cancellation at all (300 Hz–6 kHz band), and ipsilateral level stays
within a bounded coloration window. See
`packages/core/tests/test_crosstalk.py::test_xtc_reduces_contralateral_leakage_within_coloration_bound`
— the same "no separation-quality change ships without a report" discipline
`docs/evaluation_harness.md` establishes for the separation engine, applied
here to crosstalk cancellation.

Build script: `scripts/build_crosstalk_filters.py` (dev-only, not imported
by production code).

---

## 5. Filter set contract

An XTC filter set is **4 FIR filters** (`H_LL`, `H_LR`, `H_RL`, `H_RR`),
baked to a **single 4-channel WAV file** — unlike the binaural decode
bank's 32 channels, 4 fits comfortably inside the browser's native
multichannel WAV decode cap (8 channels), so no multi-file split is needed.

```
<name>.wav   channel order: [H_LL, H_LR, H_RL, H_RR]
```

| Profile | Filter set name |
|---|---|
| `stereo` | `stereo_xtc` |
| `smart_speaker` | `smart_speaker_xtc` |
| `car` | `car_xtc` |
| `laptop` | `laptop_xtc` |
| `phone` | `phone_xtc` |

Sample rate is the filter's native rate (48 kHz); both engines resample the
taps to the session's sample rate if it differs (`resample_poly`). Core
loader: `packages/core/src/crosstalk/filters.py::load_xtc_filter_set`. The web preview
fetches the same file from `apps/web/public/xtc/` (copied byte-for-byte by
`scripts/build_crosstalk_filters.py`).

---

## 6. Per-profile voicing chain

Applied **after** the XTC matrix, using the exact same voicing primitives
and Web Audio topology as the binaural engine
(`packages/core/src/binaural/voicing.py::apply_voicing`,
`packages/core/src/binaural/profiles.py::VoicingParams` — see
`spatial_audio_engine.md` §5 for the parameter definitions and DSP topology,
not repeated here).

| Parameter | Stereo | Smart speaker | Car | Laptop | Phone |
|---|---|---|---|---|---|
| Bass shelf | — | +1.5 dB @ 150 Hz | +2.5 dB @ 120 Hz | +2.0 dB @ 160 Hz | +3.0 dB @ 180 Hz |
| Presence peak | — | — | +1.0 dB @ 2500 Hz, Q 0.9 | +1.0 dB @ 3000 Hz, Q 0.9 | +1.5 dB @ 3000 Hz, Q 0.9 |
| Stereo widen (M/S side scale) | 0 | +20% | +10% | +25% | +30% |

`stereo` is left neutral — its wide, well-conditioned span lets the XTC
matrix itself carry the spatial effect without extra tonal help.
`smart_speaker`'s narrow physical span limits achievable image width no
matter how the XTC matrix is tuned (§4), so its voicing leans on M/S
widening plus a bass lift (small cabinets typically roll off low end) to
compensate perceptually. `car`'s bass lift and mild presence peak compensate
typical car-speaker/cabin acoustics (weak bass response, road-noise masking
in the presence band); its widen is milder than `smart_speaker`'s since the
profile's own wide asymmetric span already contributes real width.
`laptop`'s chassis speakers are thin and bass-poor, so its bass lift plus
presence peak restore clarity and low end; its widen sits between
`smart_speaker` and `phone` since its span (§3) is slightly wider than
either. `phone`'s handset speakers are the narrowest and least capable of
any profile — almost no bass response and the tightest physical span — so
it carries the strongest bass lift and widen of the five, compensating
perceptually for what the geometry and driver size can't provide.

Source of truth: `packages/core/src/crosstalk/profiles.py::VOICING_PARAMS`. Web
mirror: `TRANSAURAL_VOICING_PARAMS` in `masteringProfiles.ts`.

---

## 7. Parity policy

Same three-tier structure as the binaural contract (`spatial_audio_engine.md`
§6), applied to this engine's own Tier-1 set: speaker geometry (§3), the XTC
filter files (§5), and voicing parameters (§6) — bit-for-bit, single-sourced
from core and served at runtime (`docs/contracts/preview_export_parity.md`
§4). One exception to the usual Tier-2 DSP-realization gap: the **XTC
convolution** itself, like the binaural decode convolution, is a plain
linear FIR bank applied to the same files on both sides, so it *is* expected
to match closely (within floating-point/resampling tolerance) — any drift
there indicates a bug, not an accepted implementation difference. Held
within tolerance by `packages/core/tests/test_preview_export_golden.py`.

---

## 8. Delivery format and honest limitations

Exposed as `UpmixConfig.output_type == "transaural"`
(`upmixer.formats.TRANSAURAL`, 2 channels: `FL`, `FR`) — a delivery format
alongside `"wav"`, `"adm-bwf"`, and `"binaural"`, mutually exclusive with
all three (one `output_type` field). Manifest keys: `format.type:
transaural`, `format.transaural.profile`. CLI: `--format {5.1.4,7.1.2,7.1.4}
--output-type transaural --transaural-profile
{stereo,smart_speaker,car,laptop,phone}`.
Gain-staging (collapse-stage loudness ceiling, soft-limit-last ordering)
mirrors the binaural delivery stage exactly — see `spatial_audio_engine.md`
§7 — with `CROSSTALK_LOUDNESS_MAX_GAIN_DB = 6.0` dB in place of
`BINAURAL_LOUDNESS_MAX_GAIN_DB`.

**What this engine does not model**, stated plainly rather than left
implicit: elevation is fixed at 0 (real speakers are assumed ear-level;
tilt/height is not corrected for); there is no head-tracking, so the XTC
sweet spot is a single fixed listening position per profile (a real
listener's head movement narrows or breaks cancellation, most severely
above ~8 kHz where the cancellation wavelength is only centimeters — a
known, physical limit of XTC in general, not specific to this
implementation); and the regularization curve (§4.2) is this engine's own
heuristic tuned against the objective check in §4.3, not a validated
perceptual measurement or a reproduction of any commercial system's
(e.g. BACCH's) specific, unpublished design. Swapping in a measured
HRTF/BRIR dataset or a different regularization scheme later only requires
regenerating the same file layout (§5) — no engine code changes.

---

## References

- Atal, B. S. and Schroeder, M. R. — the original crosstalk-cancellation
  scheme for stereo speaker playback of binaural signals.
- Cooper, D. H. and Bauck, J. L. — the "shuffler" (sum/difference) filter
  simplification for symmetric listener/speaker geometry, and the
  ipsilateral/contralateral transfer-function formulation this engine's §4
  follows in spirit.
- Choueiri, E. — Princeton 3D3A Lab, BACCH filters: optimized crosstalk
  cancellation with minimized spectral coloration via frequency-dependent
  regularization. General principle (regularize more where naive inversion
  colors the sound) informs this engine's §4.2; the specific published
  BACCH regularization formula is not reproduced here (not available in a
  form this project could parse/verify) — see §8's provenance note.
- General XTC literature on off-center-listener asymmetric 2x2 crosstalk
  matrices (informing the `car` profile) and on the ~8 kHz head-shadow
  limit for high-frequency XTC performance (informing this engine's choice
  not to force cancellation depth above that band).
