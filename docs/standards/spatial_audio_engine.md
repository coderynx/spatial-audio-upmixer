# Spatial Audio Engine — Binaural Rendering Contract

**Source:** Internal design, modeled on published descriptions of Apple Spatial
Audio and the Dolby Atmos binaural renderer (see References). No measured
proprietary HRTF/BRIR data is used — all filters are synthesized (§4).
**Scope:** Rendering a discrete multichannel bed to headphone-ready binaural
stereo. This document is the signed contract between the core engine
(`upmixer/binaural/`) and the web preview (`web/src/features/projects/
useStemPreview.ts`, `masteringProfiles.ts`) — both must implement it
identically at the parameter level (see §6 for what "identically" means).

---

## 0. Why three profiles

Apple's Spatial Audio renders Dolby Atmos Music on headphones by convolving
directional HRTF/BRIR filters against an object/channel bed, with an
opinionated "enhance" stage layered on top for consumer playback. The Dolby
Atmos binaural renderer instead exposes distance-based BRIR modes
(Off/Near/Mid/Far) that change frequency response and reverb time per
object. Professional mixing needs a third mode neither offers: a **neutral
monitoring reference** with no consumer coloration. This engine models all
three as explicit, selectable profiles:

| Profile | Purpose | Room | Voicing |
|---|---|---|---|
| `studio` | Monitor the mix as if in a treated spatial-audio mixing room | Neutral measured-style room BRIR | None |
| `listening` | Preview what a listener hears on Apple Music Atmos-on-headphones | Consumer room BRIR | Full "enhance" chain (§5) |
| `flat` | Anechoic reference — verify the mix with zero added coloration | None (anechoic) | None |

---

## 1. Signal graph

```
bed channels (discrete, e.g. 7.1.4)
  │
  ├─ per positional channel: order-3 real-SH encode at its fixed (azimuth, elevation)  [§2, §3]
  │
  ▼
sum → 16-channel HOA bus (ACN/N3D)
  │
  ▼
convolve with profile decode filter set (16 ACN × {L,R} FIR bank)          [§4]
  │
  ▼
stereo (L, R)
  │
  ├─ + LFE (lowpass ~120 Hz, gain −10 dB, added post-decode, both ears equally — LFE has no position)
  │
  ▼
profile voicing chain (crossfeed → bass/air shelves → presence → M/S widen)  [§5]
  │
  ▼
delivery: BS.1770 loudness correction (small, bed-preserving — see §7) → soft-limit (safety net, last) → 2ch WAV
```

LFE is excluded from the ambisonic encode (no spatial position) and summed
in directly after decode, matching how the web preview treats its LFE bus.
The LFE sum is attenuated by `lfe_gain` (−10 dB, `UpmixConfig.lfe_gain`) —
it is fully correlated across both ears, so summing it at unity would double
its perceived weight relative to the HRTF-decoded bed.

This graph runs identically for every bed layout in
`upmixer.formats.BINAURAL_BED_FORMATS` (`5.1.4`, `7.1.2`, `7.1.4`) — only the
set of positional channels encoded into the HOA bus changes.

---

## 2. Virtual-loudspeaker geometry

Unit-sphere positions, listener at the origin facing −Z. `x` = left(−)/right(+),
`y` = floor(0)/height(+), `z` = front(−)/back(+). LFE has no position.

| Channel | x | y | z |
|---|---|---|---|
| FL | −0.5 | 0 | −0.87 |
| FR | 0.5 | 0 | −0.87 |
| C | 0 | 0 | −1 |
| SL | −0.94 | 0 | 0.34 |
| SR | 0.94 | 0 | 0.34 |
| BL | −0.7 | 0 | 0.7 |
| BR | 0.7 | 0 | 0.7 |
| TFL | −0.5 | 0.6 | −0.7 |
| TFR | 0.5 | 0.6 | −0.7 |
| TBL | −0.6 | 0.6 | 0.6 |
| TBR | 0.6 | 0.6 | 0.6 |

Azimuth/elevation conversion (positive azimuth = left):

```
azimuth  = atan2(-x, -z)             (degrees, 0 = front)
elevation = asin(y / |position|)     (degrees, 0 = horizon)
```

Source of truth: `upmixer/binaural/geometry.py` (core) and
`web/src/lib/spatial.ts` `speakerCoordinates` / `positionToAzimuthElevation`
(web). These two files must stay numerically identical.

---

## 3. Ambisonic convention

Order 3, **ACN** channel ordering, **N3D** normalization (the AmbiX
convention). 16 channels. `azimuth` = 0 front / positive = left; `elevation`
= 0 horizon / positive = up (both radians in the encode formulas).

| ACN | Formula |
|---|---|
| 0 | 1 |
| 1 | √3 · cosδ · sinθ |
| 2 | √3 · sinδ |
| 3 | √3 · cosδ · cosθ |
| 4 | (√15/2) · cos²δ · sin2θ |
| 5 | √15 · sinδ·cosδ · sinθ |
| 6 | (√5/2) · (3sin²δ − 1) |
| 7 | √15 · sinδ·cosδ · cosθ |
| 8 | (√15/2) · cos²δ · cos2θ |
| 9 | √(35/8) · cos³δ · sin3θ |
| 10 | (√105/2) · sinδ·cos²δ · sin2θ |
| 11 | √(21/8) · cosδ·(5sin²δ−1) · sinθ |
| 12 | 0.5 · sinδ·(5sin²δ−3) |
| 13 | √(21/8) · cosδ·(5sin²δ−1) · cosθ |
| 14 | (√105/2) · sinδ·cos²δ · cos2θ |
| 15 | √(35/8) · cos³δ · cos3θ |

A speaker's contribution to the HOA bus is `gain[acn] * signal` summed
across all positional speakers. Source of truth:
`upmixer/binaural/ambisonics.py::encode_gains`.

**Parity note:** this is the standard published AmbiX ACN/N3D real-SH basis.
The web implementation may use a third-party ambisonic library's encoder as
long as it also implements ACN/N3D order 3 — bit-exact agreement with any
specific library's internal code path is not required, only agreement on
this table (see §6).

---

## 4. Decode filter set contract

A decode filter set is **32 FIR filters**: 16 ACN channels × {L, R} ear.
Applying it to the HOA bus is a bank convolution:

```
ear[e] = Σ_acn  conv(hoa[acn], filter[acn][e])   for e in {L, R}
```

### File layout

Each profile ships as **four 8-channel WAV files** (so the same asset can be
`fetch`-decoded in a browser, which caps native multichannel WAV decode at 8
channels):

```
<name>_01-08ch.wav   channels 0–7
<name>_09-16ch.wav   channels 8–15
<name>_17-24ch.wav   channels 16–23
<name>_25-32ch.wav   channels 24–31
```

Concatenated channel order: `[ACN0_L, ACN0_R, ACN1_L, ACN1_R, ..., ACN15_L,
ACN15_R]`. All four files share one sample rate; the WAV sample rate is the
filter's native rate (48 kHz) — both engines resample the taps to the
session's sample rate if it differs.

### Per-profile assignment

| Profile | Filter set name | Room | Notes |
|---|---|---|---|
| `flat` | `flat_o3_decode` | none (anechoic) | 128-tap direct HRIR only |
| `studio` | `studio_o3_decode` | RT60 ≈ 120 ms | neutral monitor room |
| `listening` | `listening_o3_decode` | RT60 ≈ 150 ms | consumer room; voicing (§5) layered on top |

### Provenance (synthesis, not measurement)

No proprietary or third-party-measured HRTF/BRIR dataset ships with this
repository. Filters are generated by `scripts/build_binaural_filters.py`
from:

1. A parametric spherical-head HRTF model per virtual-loudspeaker direction
   (Woodworth ITD formula + frequency-dependent head-shadow ILD lowpass).
2. For `studio`/`listening`: an exponentially-decaying filtered-noise room
   tail (independent per ear for decorrelation) convolved onto the direct
   HRIR, pre-delayed ~5 ms.
3. A pseudo-inverse (mode-matching) ambisonic decode matrix from 32
   Fibonacci-lattice virtual-loudspeaker directions, folded through the
   per-direction BRIRs into the 16×{L,R} FIR bank above.

This is a documented approximation, not a perceptually validated HRTF
measurement. Swapping in a measured dataset later only requires regenerating
the same file layout — no engine code changes.

Core loader: `upmixer/binaural/decoder.py`. The web preview fetches the same
four-file-per-profile layout from `web/public/hrir/` (copied byte-for-byte
by the build script).

---

## 5. Per-profile voicing chain

Applied **after** decode + LFE re-add, in this order: crossfeed → bass shelf
→ air shelf → presence peak → M/S stereo widen. All-zero parameters = bypass
(exactly `flat` and `studio`).

| Parameter | Flat | Studio | Listening |
|---|---|---|---|
| Crossfeed amount | 0 | 0 | 0.28 |
| Crossfeed cutoff | — | — | 700 Hz |
| Bass shelf | — | — | +1.0 dB @ 120 Hz (low-shelf) |
| Air shelf | — | — | +1.0 dB @ 9000 Hz (high-shelf) |
| Presence peak | — | — | +0.5 dB @ 3000 Hz, Q 0.9 |
| Stereo widen (M/S side scale) | 0 | 0 | +10% |
| Loudness target | −18.0 LKFS (config default) | −18.0 LKFS | **−16.0 LKFS** |

These are deliberately subtle — Listening only lightly voices what the
mastered bed and baked room decode already deliver (the bed's own EQ/bass
handling and the room-tail decode filters already add warmth; layering a
second full "enhance" pass on top of both reads as boomy/hot, the failure
mode this profile previously had).

Crossfeed: each ear mixed with a low-passed copy of the opposite ear
(`out_L = L·(1−a) + lowpass(R)·a`), reducing hard-panned harshness the way
consumer "enhance" processing does. Shelf/peak filters use the same
subtract/add biquad trick as `upmixer/utils.py::elevation_eq` so the Web
Audio `BiquadFilterNode` chain can match parameter-for-parameter.

Core implementation: `upmixer/binaural/voicing.py` +
`upmixer/binaural/profiles.py::VOICING_PARAMS`. Web mirror:
`web/src/features/projects/masteringProfiles.ts` (listening voicing
constants) applied in `useStemPreview.ts`.

---

## 6. Parity policy

The **spatial model** — geometry (§2), SH convention (§3), decode filter
files (§4), and voicing **parameters** (§5) — is specified exactly and must
match bit-for-bit between core and web (same numbers, same files).

The **DSP realization** of the voicing chain is *not* required to be
sample-identical: the core uses SciPy `sosfilt` IIR sections, the web uses
Web Audio `BiquadFilterNode`s. These differ in numerical precision and
exact phase response, the same accepted gap that already exists between
`upmixer/mastering/*.py` and the web's `buildMasteringTopology` mirror.
Parity is verified at the **parameter table** level (§5) plus a bounded
audible-difference tolerance on a reference render (see the acceptance test
in `tests/test_binaural.py` and its web-side counterpart), not through
guaranteed identical output samples.

The **decode convolution** itself (§4) is a plain linear FIR bank applied to
the same files on both sides, so it *is* expected to match closely (within
floating-point/resampling tolerance) — any drift there indicates a real bug,
not an acceptable implementation difference.

---

## 7. Delivery format

Exposed as the `binaural` output format (`upmixer.formats.FORMAT_MAP
["binaural"]`, 2 channels: `FL`, `FR`). Selecting it does not upmix directly
to 2 channels — the pipeline first upmixes/masters to an intermediate
discrete bed (`UpmixConfig.binaural_bed`, one of `5.1.4` / `7.1.2` / `7.1.4`),
then this engine collapses that mastered bed to stereo. Incompatible with
`output_type = "adm-bwf"` (ADM-BWF requires a discrete channel-based bed by
definition). Manifest keys: `mixing.channel_layout: binaural`,
`mixing.binaural.profile`, `mixing.binaural.bed`. CLI: `--format binaural
--binaural-profile {studio,listening,flat} --binaural-bed {5.1.4,7.1.2,7.1.4}`.

**Gain staging.** The intermediate bed is already BS.1770-normalized by
`MasteringChain` before collapse. The binaural delivery stage does **not**
re-run a full loudness match on top of that — the 12→2 channel collapse
concentrates energy from many channels into two, so a second full match
would read louder than the mastered bed rather than matching it. Instead it
applies a small bounded correction for the collapse's own level shift
(`BINAURAL_LOUDNESS_MAX_GAIN_DB = 6.0` dB ceiling, `upmixer/binaural/
renderer.py`) and true-peak limits linearly to `-1.0 dBTP`. The soft-limit
safety net (`soft_limit`, tanh above `peak_limit_threshold`) runs **last**,
after this gain correction — limiting the raw pre-gain HRTF sum instead bakes
in audible saturation no later stage can undo, which is what produced
distorted output on all three profiles (including `flat`) before this
ordering was fixed. The web mirror applies the same ceiling and ordering
(`BINAURAL_LOUDNESS_MAX_GAIN_DB` in `masteringProfiles.ts`, graph order in
`useStemPreview.ts`'s `buildMasteringTopology`).

---

## References

- Apple Spatial Audio / Spatialize Stereo overview — Sweetwater InSync,
  Apple Support (Logic Pro binaural render modes).
- Dolby Atmos binaural renderer distance modes (Off/Near/Mid/Far) —
  Audient "The essential guide to binaural simulation for Dolby Atmos".
- Dolby BRIR design and reverberation-generation patents (numerically
  optimized BRIRs; reverberation generation for headphone virtualization),
  USPTO 10,834,519 and 12,143,797 — general BRIR/virtualization concepts,
  not implemented verbatim here.
