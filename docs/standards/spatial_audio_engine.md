# Spatial Audio Engine — Binaural Rendering Contract

**Sibling target:** this document covers headphone (binaural) playback only.
For real stereo loudspeakers (standard hi-fi pairs, smart speakers, car
audio), see
[Transaural Speaker Rendering](transaural_speakers.md), which reuses this
engine's anechoic (`flat`) ear signals as its input and adds a crosstalk-
cancellation stage on top.

**Source:** Internal design, modeled on published descriptions of Apple Spatial
Audio and the Dolby Atmos binaural renderer (see References). No measured
proprietary HRTF/BRIR data is used — all filters are synthesized (§4).
**Scope:** Rendering a discrete multichannel bed to headphone-ready binaural
stereo. This document is the signed contract between the core engine
(`packages/core/src/binaural/`) and the web preview (`apps/web/src/features/projects/
useStemPreview.ts`, `masteringProfiles.ts`) — both must implement it
identically at the parameter level (see §6 for what "identically" means).

---

## 0. Why three profiles

Apple's Spatial Audio renders a discrete bed (not per-object distance
metadata) on headphones by convolving directional HRTF/BRIR filters against
the full channel layout, with a room emulation layered on top of the direct
HRTF. This engine follows that model — a single room decode applied to the
whole bed, no per-object near/mid/far modes. Professional mixing needs a
second mode Apple's consumer-facing renderer doesn't offer: a **neutral
monitoring reference** with no room coloration at all. This engine models
three explicit, selectable profiles:

| Profile | Purpose | Room | Voicing |
|---|---|---|---|
| `studio` | Monitor the mix as if in a treated spatial-audio mixing room | Neutral measured-style room BRIR | None |
| `listening` | Flattering "hi-fi system" consumer preview on headphones | Reference cinema room BRIR | Hi-fi enhance (§5) |
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

"Bed channels" above is deliberate: reference matching
(`mastering/match_reference/`) runs as mastering step 0, entirely upstream
of this graph, on the discrete speaker bed — never on the binaural output.
A reference file that is itself a binaural render is an invalid reference
for that reason: its long-term spectrum carries the anechoic HRTF decode's
own diffuse-field coloration, so matching a speaker-bed master to it would
fold headphone-specific correction into content also delivered to speakers.

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

Source of truth: `packages/core/src/binaural/geometry.py` (core) and
`apps/web/src/lib/spatial.ts` `speakerCoordinates` / `positionToAzimuthElevation`
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
`packages/core/src/binaural/ambisonics.py::encode_gains`.

**ACN 12 normalization note:** `encode_gains`'s ACN 12 (Y₃⁰, the order-3
vertical/zonal harmonic) deliberately omits the standard N3D `√7` factor
(`gains[12] = 0.5 · sinδ · (5sin²δ − 3)`, not `0.5·√7·sinδ·(5sin²δ − 3)`). The
decode filter bank (§4) was fit as the pseudo-inverse of this exact encoder,
so the omission is load-bearing, not a bug to "correct" — doing so would
retune every decode filter. The web preview's SH library
(`spherical-harmonic-transform::computeRealSH`) applies the standard `√7`
factor, so `useStemPreview.ts` scales its ACN 12 tap by `1/√7` before the
decode convolvers to match this encoder's convention. All other 15 ACN
channels already agree between the two encoders to within floating-point
tolerance.

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
| `studio` | `studio_o3_decode` | RT60 ≈ 120 ms | neutral monitor room, 5 ms pre-delay, bright tail (3500 Hz) |
| `listening` | `listening_o3_decode` | RT60 ≈ 120 ms | reference cinema room — same room amount as `studio`, warmer/darker tail (2500 Hz); light polish (§5) layered on top |

### Provenance (synthesis, not measurement)

No proprietary or third-party-measured HRTF/BRIR dataset ships with this
repository. Filters are generated by `scripts/build_binaural_filters.py`
from:

1. A parametric spherical-head HRTF model per virtual-loudspeaker direction
   (Woodworth ITD formula + frequency-dependent head-shadow ILD: a lowpass
   plus a 700 Hz high shelf on the contralateral path, so the interaural
   level difference falls to zero at low frequency — a head only shadows
   wavelengths shorter than itself). Shared with the transaural engine, which
   inverts the same model; see `transaural_speakers.md` §4.1.
2. For `studio`/`listening`: an exponentially-decaying filtered-noise room
   tail (independent per ear for decorrelation) convolved onto the direct
   HRIR, pre-delayed 5 ms. Both profiles share the same reverberant amount
   (RT60 ≈ 120 ms, same pre-delay and tail level); they differ only in tail
   high-frequency rolloff — `studio` keeps a bright 3500 Hz tail, `listening`
   darkens it to 2500 Hz so the same decay reads as a warmer, larger cinema
   room rather than a near-field monitor room.
3. A pseudo-inverse (mode-matching) ambisonic decode matrix from 32
   Fibonacci-lattice virtual-loudspeaker directions, folded through the
   per-direction BRIRs into the 16×{L,R} FIR bank above.

This is a documented approximation, not a perceptually validated HRTF
measurement. Swapping in a measured dataset later only requires regenerating
the same file layout — no engine code changes.

Core loader: `packages/core/src/binaural/decoder.py`. The web preview fetches the same
four-file-per-profile layout from `apps/web/public/hrir/` (copied byte-for-byte
by the build script).

---

## 5. Per-profile voicing chain

Applied **after** decode + LFE re-add, in this order: crossfeed → bass shelf
→ air shelf → presence peak → M/S stereo widen. All-zero parameters = bypass
(exactly `flat` and `studio`).

| Parameter | Flat | Studio | Listening |
|---|---|---|---|
| Crossfeed amount | 0 | 0 | 0.10 |
| Crossfeed cutoff | — | — | 700 Hz |
| Bass shelf | — | — | +1.0 dB @ 100 Hz (low-shelf) |
| Air shelf | — | — | +4.0 dB @ 10000 Hz (high-shelf) |
| Presence peak | — | — | +2.0 dB @ 3000 Hz, Q 0.9 |
| Stereo widen (M/S side scale) | 0 | 0 | +15% |
| Loudness target | −18.0 LKFS (config default) | −18.0 LKFS | −18.0 LKFS (config default) |

Unlike `studio` (a neutral monitoring reference), `listening` is a
deliberately **flattering "hi-fi enhance"** voicing — its job is to make
headphone playback sound like an impressive hi-fi system, not to be neutral.
It shares Studio's room amount (§4, warmer cinema tail) and layers an
obvious, consumer-style enhancement on top: a Harman-style tonal tilt
(+1 dB low-end warmth, +4 dB air, +2.0 dB presence for clarity), a
cinema-width soundstage (+15% side), and light crossfeed (0.10) for
externalization. It is **loudness-matched** to `studio` (no target of its
own — an earlier revision added a +2 dB lift, but that inflated perceived
bass via the equal-loudness effect and read as too hot, so the enhancement
now stands on tone and space alone). It is still deliberately *tone*-colored,
not neutral — use `studio`/`flat` for reference monitoring and `listening`
only for the enhanced consumer preview. The bass shelf sits at 100 Hz with
the room tail highpassed at 200 Hz (§4), so the warmth lift adds weight
without boomy ringing.

Crossfeed: each ear mixed with a low-passed copy of the opposite ear
(`out_L = L·(1−a) + lowpass(R)·a`), softening hard-panned harshness the way
headphone crossfeed does in general. Shelf/peak filters use the same
subtract/add biquad trick as `packages/core/src/utils.py::elevation_eq` so the Web
Audio `BiquadFilterNode` chain can match parameter-for-parameter.

Core implementation: `packages/core/src/binaural/voicing.py` +
`packages/core/src/binaural/profiles.py::VOICING_PARAMS`. Web mirror:
`apps/web/src/features/projects/masteringProfiles.ts` (listening voicing
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
`packages/core/src/mastering/*.py` and the web's `buildMasteringTopology` mirror.
Parity at the **parameter table** level (§5) is structural: the voicing and
gain constants are single-sourced from core and served to the web at runtime
(see `docs/contracts/preview_export_parity.md` §4), so there is no second copy
to drift. The actual cross-engine render is held within a bounded
audible-difference tolerance by `packages/core/tests/test_preview_export_golden.py`.
(Constant-level parity has long been pinned independently on each side by
`packages/core/tests/test_binaural.py` and `masteringProfiles.test.ts`; the served single
source above is what ties the two
together and what the cross-engine render diff actually verifies, closing a
gap this document previously described as already covered when it was not.)

The **decode convolution** itself (§4) is a plain linear FIR bank applied to
the same files on both sides, so it *is* expected to match closely (within
floating-point/resampling tolerance) — any drift there indicates a real bug,
not an acceptable implementation difference.

---

## 7. Delivery format

Exposed as `UpmixConfig.output_type == "binaural"` (`upmixer.formats.BINAURAL`,
2 channels: `FL`, `FR`) — a delivery format alongside `"wav"` and `"adm-bwf"`,
selected the same way they are (`format.type` in the manifest, `--output-type`
on the CLI), not a channel layout. Selecting it does not change what layout is
upmixed — the pipeline upmixes/masters `UpmixConfig.output_format` as normal
(which must be one of `5.1.4` / `7.1.2` / `7.1.4` for binaural to be valid),
then this engine collapses that mastered bed to stereo. Mutually exclusive
with `"adm-bwf"` by construction (both live in the same `output_type` field;
ADM-BWF requires a discrete channel-based bed by definition). Manifest keys:
`mixing.channel_layout` (the bed, unchanged), `format.type: binaural`,
`format.binaural.profile`. CLI: `--format {5.1.4,7.1.2,7.1.4} --output-type
binaural --binaural-profile {studio,listening,flat}`.

**Gain staging.** The intermediate bed is already BS.1770-normalized by
`MasteringChain` before collapse. The binaural delivery stage does **not**
re-run a full loudness match on top of that — the 12→2 channel collapse
concentrates energy from many channels into two, so a second full match
would read louder than the mastered bed rather than matching it. Instead it
applies a small bounded correction for the collapse's own level shift
(`BINAURAL_LOUDNESS_MAX_GAIN_DB = 6.0` dB ceiling, `packages/core/src/binaural/
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
  Apple Support (Logic Pro binaural render modes). Apple's renderer convolves
  HRTF/BRIR filters against the full channel/object bed directly — no
  per-object distance-mode metadata — which is the model this engine's
  `listening` (reference cinema room) profile follows.
- Dolby BRIR design and reverberation-generation patents (numerically
  optimized BRIRs; reverberation generation for headphone virtualization),
  USPTO 10,834,519 and 12,143,797 — general BRIR/virtualization concepts,
  not implemented verbatim here.
