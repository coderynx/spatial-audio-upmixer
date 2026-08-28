# Transaural Speaker Rendering — Crosstalk-Cancellation Contract

**Source:** Internal design, following published crosstalk-cancellation
(XTC) literature — the Atal-Schroeder crosstalk canceller, the Cooper/Bauck
"shuffler" simplification for symmetric listener geometry, and Choueiri's
analysis of frequency-dependent regularization for optimal XTC (see
References), whose published optimization criterion §4.2 implements. No
proprietary BACCH product filter set or measured/licensed XTC dataset is
reproduced here; the speaker-to-ear model those filters are inverted from is
this engine's own parametric head model (§4.1), not a measured HRTF.
**Scope:** Rendering a discrete multichannel bed to speaker-ready,
crosstalk-cancelled stereo for real stereo loudspeakers. This document is
the signed contract for the core engine's transaural rendering pass
(`packages/core/src/crosstalk/`, shared with the browser preview through
`packages/dsp`'s `stream::output`). It assumes familiarity with [Spatial
Audio Engine — Binaural Rendering Contract](spatial_audio_engine.md), whose
anechoic (`flat`) ear-signal render this engine reuses as its input.

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
| `stereo` | Standard hi-fi speaker pair, symmetric listening position | Wide (±30°) | Deepest — a wide, symmetric span is the best-conditioned case for XTC, so the largest coloration budget buys the most depth |
| `smart_speaker` | Single cabinet, narrow dual-driver span (soundbar / smart speaker) | Narrow (±12°) | Shallow, by design — a narrow span makes deep low-frequency cancellation expensive (see §4); voicing leans on stereo widening to compensate the narrower physical image instead |
| `car` | Off-center driver-seat listening position | Wide, asymmetric (+22°/−42°) | Moderate — an asymmetric 2x2 matrix, not the symmetric shuffler simplification |
| `laptop` | Built-in chassis speakers near the front edge, near-field desk listening | Narrow (±14°) | Shallow — a slightly larger budget than `smart_speaker` since the span is a bit wider, but still far short of `stereo`'s depth |
| `phone` | Built-in handset speakers, near-field handheld listening | Narrowest (±6°) | Shallowest of all five — the most ill-conditioned span, so it gets the tightest coloration budget; voicing does the most compensating work |

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
profile voicing chain (bass/presence shelf → M/S widen)      [§6, reuses binaural's voicing chain unchanged]
  │
  ▼
2x2 crosstalk-cancellation FIR matrix            [§4]
  speaker_L = conv(ear_L, H_LL) + conv(ear_R, H_LR)
  speaker_R = conv(ear_L, H_RL) + conv(ear_R, H_RR)
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

Core entry point: `packages/core/src/crosstalk/renderer.py::render_crosstalk`,
shared with the browser preview through `packages/dsp`'s `stream::output`
transaural path, which reuses the binaural `flat` ear-signal stage and the
same `preVoicing` LFE-injection contract.

"Mastered bed" above is deliberate: reference matching
(`mastering/match_reference/`) runs as mastering step 0, entirely upstream
of this graph, on the discrete speaker bed — never on ear signals or the
crosstalk-cancelled output. A reference file that is itself a binaural or
transaural *render* is an invalid reference for that reason: its long-term
spectrum carries the anechoic HRTF/XTC filter's own coloration (and, for
XTC, the 2x2 matrix's), so matching a speaker-bed master to it would fold
headphone- or one-listener-position-specific correction into content played
back on arbitrary speakers.

---

## 3. Speaker geometry

Each profile fixes two listener-relative speaker azimuths in degrees (0 =
dead ahead, positive = left — the same convention as
`packages/core/src/binaural/geometry.py`). Elevation is always 0 (ear-level speakers,
a documented simplification — see §7). Symmetric profiles set
`azimuth_right_deg == -azimuth_left_deg`; `car` does not, since an
off-center driver-seat position genuinely has four independent
speaker-to-ear paths, not a mirror pair.

| Profile | Left speaker azimuth | Right speaker azimuth | `gamma_db` | `xtc_lo_hz` | `xtc_hi_hz` |
|---|---|---|---|---|---|
| `stereo` | +30° | −30° | 7.0 | 150 | 6000 |
| `smart_speaker` | +12° | −12° | 4.0 | 180 | 6000 |
| `car` | +22° | −42° | 6.0 | 150 | 6000 |
| `laptop` | +14° | −14° | 5.0 | 180 | 6000 |
| `phone` | +6° | −6° | 3.0 | 200 | 6000 |

`gamma_db` is the profile's spectral-coloration budget (§4.2); `xtc_lo_hz` /
`xtc_hi_hz` bound the band over which cancellation is applied at all (§4.3).
Narrower spans get tighter budgets and a higher low corner — their `C` is
ill-conditioned over a wider low-frequency range, so spending more there
would buy coloration rather than depth. All three are bake-time only: the
web preview never sees them, only the resulting WAV (§7).

Source of truth: `packages/core/src/crosstalk/profiles.py::XTC_PARAMS` (`XtcParams`
dataclass) and `packages/core/src/crosstalk/geometry.py::speaker_azimuths_rad`.

---

## 4. Speaker-to-ear model and XTC filter design

### 4.1 Speaker-to-ear transfer matrix

The acoustic path from each speaker to each ear is synthesized with the
**same parametric spherical-head HRTF model** the binaural engine's decode
filters use — piecewise Woodworth ITD for front and rear hemispheres plus
frequency-dependent head-shadow ILD (a lowpass plus a
`SHADOW_SHELF_HZ = 700 Hz` high shelf) —
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

The shelf matters more here than it does for headphone rendering. A head only
shadows wavelengths shorter than itself, so a real interaural level difference
collapses below a few hundred Hz; a frequency-flat ILD would make `C` look far
better conditioned at low frequency than it physically is, and the filters
inverted from it would be designed for a listener who does not exist.

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

`β(f)` is **not** a fixed floor. A constant `β` is provably suboptimal —
it is optimal only at a handful of discrete frequencies, and elsewhere it
either overspends (needless loss of cancellation) or underspends (audible
coloration peaks); it also splits the coloration peaks into doublets and
turns the perfect filter's bass boost into a bass roll-off. Instead each bin
gets **the least regularization that holds the coloration envelope under the
profile's budget**, following Choueiri's frequency-dependent prescription:

```
γ = 10^(gamma_db/20)                      # coloration ceiling at the speakers
for each singular value s of C(f):
    β must satisfy   s / (s² + β)  ≤  γ   # that axis's gain through H
β(f) = max(0, max_s( s/γ − s² ))
```

Both singular values must clear the ceiling, not only the smaller one — at a
deep null both are small and capping one axis alone lets the other breach the
budget. Where the geometry is well-conditioned enough that no cap is needed,
`β = 0` and the bin gets exact, uncolored cancellation. So `gamma_db` is a
single perceptual knob per profile (§3's table) replacing a hand-tuned curve:
it *is* the maximum spectral coloration, in dB, that the speaker feeds are
allowed to carry.

### 4.3 Band limiting

Outside an active band the matrix blends to identity, `H = w·H + (1−w)·I`
with `w` a raised-cosine ramp (one octave up from `xtc_lo_hz`, and down over
`[xtc_hi_hz, 1.5·xtc_hi_hz]`):

- **Below `xtc_lo_hz`** there are no usable localization cues to protect and
  inversion is hopeless for any real span — the band is passed through.
- **Above `xtc_hi_hz`** (6 kHz) the head already separates the ears; forcing
  cancellation there buys nothing perceptually and shrinks the sweet spot,
  since the cancellation wavelength is only centimeters.

The blend is applied **before** the bulk delay, so both branches carry the
same delay and the crossover cannot comb. No post-design gain normalization
is applied: the identity branch remains unity-gain.

### 4.4 Windowing

`H(f)` is IFFT'd with a bulk delay (to keep the generally non-causal inverse
inside a causal, finite window), windowed to the profile's tap count centered
on that delay (`taps//2` of room on each side, since the anti-causal part of
a regularized inverse is substantial), and edge-tapered — giving the four
time-domain FIRs `H_LL, H_LR, H_RL, H_RR`.

### 4.5 Objective correctness check

Because the filters are inverted from a synthetic head rather than a measured
one, no crosstalk-cancellation change ships without confirming, per profile,
all of the following on the synthesized `C`/`H` pair — the same "no
separation-quality change ships without a report" discipline
`docs/evaluation_harness.md` establishes for the separation engine
(`packages/core/tests/test_crosstalk.py`):

| Check | Test |
|---|---|
| Contralateral leakage falls vs. no cancellation, 300 Hz–6 kHz, with ipsilateral level inside a bounded coloration window | `test_xtc_reduces_contralateral_leakage_within_coloration_bound` |
| Both halves of that tradeoff also hold *per sub-band* (300 Hz–1 k, 1–3 k, 3–6 k), so a good total can't hide a dead or colored octave | `test_xtc_per_band_depth_and_coloration` |
| Filters still work on a head they were not designed for (±10 % head radius) — catches a design that scores well only by overfitting the model head | `test_xtc_survives_head_size_mismatch` |
| Below the active band the delayed filter matrix remains identity (unity diagonal, negligible crossfeed) | `test_xtc_filter_set_is_delayed_identity_below_active_band` |
| The §4.3 crossover passes low frequencies flat and adds no comb notch | `test_xtc_passes_low_frequencies_without_a_crossover_notch` |

Measured depth on the design head (300 Hz–6 kHz leakage suppression, with
ipsilateral coloration ≤ 0.9 dB in every profile): `stereo` 26.6 dB, `car`
23.8 dB, `laptop` 18.5 dB, `smart_speaker` 17.0 dB, `phone` 15.5 dB. Under a
±10 % head-radius mismatch these fall to 7–14 dB, which is the honest
real-world expectation — a fixed-geometry XTC filter cannot hold its design
depth on an arbitrary listener.

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

Each filter is 1024 taps. Sample rate is the filter's native rate (48 kHz);
both engines resample the taps to the session's sample rate if it differs
(`resample_poly`). Core
loader: `packages/core/src/crosstalk/filters.py::load_xtc_filter_set`. The web preview
fetches the same file from `apps/web/public/xtc/` (copied byte-for-byte by
`scripts/build_crosstalk_filters.py`).

---

## 6. Per-profile voicing chain

Applied to the ear signals **before** the XTC matrix, using the exact same voicing primitives
as the binaural engine
(`packages/core/src/binaural/voicing.py::apply_voicing`,
`packages/core/src/binaural/profiles.py::VoicingParams` — see
`spatial_audio_engine.md` §5 for the parameter definitions and DSP topology,
not repeated here).

Order matters: voicing shapes the ear signals the canceller is then asked to
deliver. Run afterwards, the M/S widen would re-introduce crosstalk the matrix
had just removed — harmless for the symmetric profiles, whose `C` commutes
with an M/S matrix, but not for `car`, whose asymmetric geometry does not.

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

Source of truth: `packages/core/src/crosstalk/profiles.py::VOICING_PARAMS`.

---

## 7. Delivery format and honest limitations

Exposed as `UpmixConfig.output_type == "transaural"`
(`upmixer.formats.TRANSAURAL`, 2 channels: `FL`, `FR`) — a delivery format
alongside `"multichannel"`, `"adm-bwf"`, and `"binaural"`, mutually exclusive with
all three (one `output_type` field). Manifest keys: `format.type:
transaural`, `format.transaural.profile`. CLI: `--format {5.1.4,7.1.2,7.1.4}
--output-type transaural --transaural-profile
{stereo,smart_speaker,car,laptop,phone}`.
Gain-staging (collapse-stage loudness ceiling, soft-limit-last ordering)
mirrors the binaural delivery stage exactly — see `spatial_audio_engine.md`
§6 — with `CROSSTALK_LOUDNESS_MAX_GAIN_DB = 6.0` dB in place of
`BINAURAL_LOUDNESS_MAX_GAIN_DB`.

**What this engine does not model**, stated plainly rather than left
implicit:

- **The listener's actual head.** `C` comes from a parametric spherical-head
  model (§4.1), not a measured HRTF, so the design depth in §4.5 is an upper
  bound no real listener reaches; the ±10 % head-radius figures there are the
  honest expectation. Pinnae, torso, and cabinet/room response are absent.
- **Elevation**, fixed at 0 — real speakers are assumed ear-level, and tilt
  or height is not corrected for.
- **Head movement.** There is no head-tracking, so the sweet spot is a single
  fixed listening position per profile. Movement narrows or breaks
  cancellation, worst at high frequency where the cancellation wavelength is
  centimeters — a physical limit of two-speaker XTC in general, and part of
  why §4.3 stops forcing cancellation above 6 kHz rather than chasing depth
  the listener cannot hold still enough to receive.
- **Perceptual validation.** The per-profile `gamma_db` budgets are engineering
  judgment checked against the objective harness in §4.5, not listening-test
  results.

Swapping in a measured HRTF/BRIR dataset later only requires regenerating the
same file layout (§5) — no engine code changes.

---

## References

- Atal, B. S. and Schroeder, M. R. — the original crosstalk-cancellation
  scheme for stereo speaker playback of binaural signals.
- Cooper, D. H. and Bauck, J. L. — the "shuffler" (sum/difference) filter
  simplification for symmetric listener/speaker geometry, and the
  ipsilateral/contralateral transfer-function formulation this engine's §4
  follows in spirit.
- Choueiri, E. (Princeton 3D3A Lab) — *Optimal Crosstalk Cancellation for
  Binaural Audio with Two Loudspeakers*. The source for §4.2: constant-β
  regularization is optimal only at discrete frequencies and produces doublet
  coloration peaks plus a bass roll-off, while capping the coloration envelope
  per frequency band yields maximum cancellation for a chosen coloration
  budget Γ. §4.2 implements that criterion directly, generalized to an
  arbitrary (including asymmetric) `C` via its singular values rather than the
  paper's closed-form free-field two-point-source solution. §4.3's 6 kHz upper
  bound follows the same paper's §V.D.
- Méaux, E. and Marchand, S. — *Synthetic Transaural Audio Rendering (STAR)*,
  DAFx-19. Source of §4.3's low-frequency bypass: below ~150–200 Hz there is
  nothing to spatialize and the system inversion is unstable, so the band is
  passed through rather than cancelled.
- General XTC literature on off-center-listener asymmetric 2x2 crosstalk
  matrices, informing the `car` profile.
