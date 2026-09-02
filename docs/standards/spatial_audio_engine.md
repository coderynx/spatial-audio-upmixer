# Spatial Audio Engine — Binaural Rendering Contract

**Sibling target:** this document covers headphone (binaural) playback only.
For real stereo loudspeakers (standard hi-fi pairs, smart speakers, car
audio), see
[Transaural Speaker Rendering](transaural_speakers.md), which reuses this
engine's anechoic (`flat`) ear signals as its input and adds a crosstalk-
cancellation stage on top.

**Source:** Internal design, modeled on published descriptions of Apple Spatial
Audio and the Dolby Atmos binaural renderer (see References). The shipped
decode banks use the measured, Apache-2.0 SADIE II D1/KU100 HRIR source
described in §4.
**Scope:** Rendering a discrete multichannel bed to headphone-ready binaural
stereo. This document is the signed contract for the core engine's binaural
rendering pass (`packages/core/src/binaural/`, shared with the browser
preview through `packages/dsp`) — geometry, ambisonic convention, decode
filters, and voicing chain.

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
| `studio` | Monitor the mix as if in a treated spatial-audio mixing room | Subtle neutral early ambience | None |
| `listening` | Flattering "hi-fi system" consumer preview on headphones | Subtle warm early ambience | Hi-fi enhance (§5) |
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
delivery: BS.1770 loudness correction (small, bed-preserving — see §6) → soft-limit (safety net, last) → 2ch WAV
```

LFE is excluded from the ambisonic encode (no spatial position) and summed
in directly after decode, matching how the web preview treats its LFE bus.
The LFE sum is attenuated by `lfe_gain` (−10 dB, `UpmixConfig.lfe_gain`) —
it is fully correlated across both ears, so summing it at unity would double
its perceived weight relative to the HRTF-decoded bed.

The raw renderer has a measured, layout-specific bank for every real speaker
layout in `upmixer.formats.MEASURED_HRIR_LAYOUTS` (`stereo`, `5.1`, `7.1`,
`5.1.2`, `5.1.4`, `7.1.2`, `7.1.4`) — only the set of positional channels
encoded into the HOA bus changes. Delivery validation still limits binaural
and transaural exports to their respective bed-format lists.

"Bed channels" above is deliberate: reference matching
(`mastering/match_reference/`) runs as mastering step 0, entirely upstream
of this graph, on the discrete speaker bed — never on the binaural output.
A reference file that is itself a binaural render is an invalid reference
for that reason: its long-term spectrum carries the anechoic HRTF decode's
own diffuse-field coloration, so matching a speaker-bed master to it would
fold headphone-specific correction into content also delivered to speakers.

---

## 2. Virtual-loudspeaker geometry

The renderer uses the BS.2051/BS.2094 nominal DirectSpeakers positions for
the selected layout. Positive azimuth is left; LFE has no spatial position.

| Layout | SL/SR | BL/BR | TFL/TFR | TBL/TBR |
|---|---|---|---|---|
| stereo | — | — | — | — |
| 5.1 | ±110°, 0° | — | — | — |
| 7.1 | ±90°, 0° | ±135°, 0° | — | — |
| 5.1.2 | ±110°, 0° | — | ±30°, +30° | — |
| 5.1.4 | ±110°, 0° | — | ±30°, +30° | ±110°, +30° |
| 7.1.2 | ±90°, 0° | ±135°, 0° | ±90°, +30° | — |
| 7.1.4 | ±90°, 0° | ±135°, 0° | ±45°, +30° | ±135°, +30° |

FL/FR are always ±30° at 0° elevation and C is 0°. Source of truth:
`packages/core/src/direct_speakers.py`; the API serves its layout-specific
directions to the browser preview, and the Rust panner is pinned to the same
positions by its unit tests.

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
retune every decode filter.

---

## 4. Decode filter set contract

A decode filter set is **32 FIR filters**: 16 ACN channels × {L, R} ear.
Applying it to the HOA bus is a bank convolution:

```
ear[e] = Σ_acn  conv(hoa[acn], filter[acn][e])   for e in {L, R}
```

### File layout

Each profile/layout bank ships as **four 8-channel WAV files** (so the same
asset can be `fetch`-decoded in a browser, which caps native multichannel WAV
decode at 8 channels):

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
| `flat` | `flat_o3_decode_{layout_slug}` | none (anechoic) | 256-tap measured direct HRIR only |
| `studio` | `studio_o3_decode_{layout_slug}` | short early ambience | measured direct HRIR plus low-level neutral ambience, 1 ms pre-delay, bright tail (3500 Hz) |
| `listening` | `listening_o3_decode_{layout_slug}` | short early ambience | measured direct HRIR plus low-level warm ambience, 1 ms pre-delay, darker tail (2500 Hz); light polish (§5) layered on top |

### Provenance (measured)

Filters are generated by `scripts/build_binaural_filters.py` from the SADIE II
D1/KU100 48 kHz diffuse-field-compensated `SimpleFreeFieldHRIR` SOFA. The
Apache-2.0 source, attribution, citation, and reproducible generation command
are recorded in `docs/standards/measured_hrir_provenance.md`.

For each supported layout, the generator selects the exact nominal measured
directions (with spherical interpolation only as a fallback), excludes LFE,
and computes a full-column-rank left inverse of that layout's order-3 encoder.
The folded 16×{L,R} bank consequently reconstructs each fixed speaker feed's
measured HRIR exactly. `studio`/`listening` append a short, low-level
deterministic early-ambience tail (20 ms decay, 1 ms pre-delay); `flat` remains
anechoic. The SADIE diffuse-field calibration is
preserved without an arbitrary post-generation peak or RMS gain.

Core loader: `packages/core/src/binaural/decoder.py`. The web preview fetches the same
four-file-per-profile/layout set from `apps/web/public/hrir/` (copied
byte-for-byte by the build script).

`DECODE_FILTER_SET` keeps its profile-only names for public compatibility. The
corresponding legacy files are measured union-direction banks for callers that
do not provide a layout; new raw-renderer and web-preview calls include the
layout suffix explicitly.

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
It shares Studio's subtle early ambience (§4, with a warmer/darker tail) and layers an
obvious, consumer-style enhancement on top: a Harman-style tonal tilt
(+1 dB low-end warmth, +4 dB air, +2.0 dB presence for clarity), a
cinema-width soundstage (+15% side), and light crossfeed (0.10) for
externalization. It is **loudness-matched** to `studio` (no target of its
own — an earlier revision added a +2 dB lift, but that inflated perceived
bass via the equal-loudness effect and read as too hot, so the enhancement
now stands on tone and space alone). It is still deliberately *tone*-colored,
not neutral — use `studio`/`flat` for reference monitoring and `listening`
only for the enhanced consumer preview. The bass shelf sits at 100 Hz with
the ambience tail highpassed at 200 Hz (§4), so the warmth lift adds weight
without boomy ringing.

Crossfeed: each ear mixed with a low-passed copy of the opposite ear
(`out_L = L·(1−a) + lowpass(R)·a`), softening hard-panned harshness the way
headphone crossfeed does in general. Shelf/peak filters use the same
subtract/add biquad trick as `packages/core/src/utils.py::elevation_eq`.

Implementation: `packages/core/src/binaural/voicing.py` +
`packages/core/src/binaural/profiles.py::VOICING_PARAMS`, run through
`packages/dsp` for both the export pipeline and the browser preview.

---

## 6. Delivery format

Exposed as `UpmixConfig.output_type == "binaural"` (`upmixer.formats.BINAURAL`,
2 channels: `FL`, `FR`) — a delivery format alongside `"multichannel"` and `"adm-bwf"`,
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
ordering was fixed. The same ceiling and ordering runs in the preview through
the shared core. Object-authored previews keep speaker-master correction on
the authored sources for ADM parity and apply the independently measured
collapse correction only to the monitor output.

---

## References

- Apple Spatial Audio / Spatialize Stereo overview — Sweetwater InSync,
  Apple Support (Logic Pro binaural render modes). Apple's renderer convolves
  HRTF/BRIR filters against the full channel/object bed directly — no
  per-object distance-mode metadata — which is the model this engine's
  `listening` (warm early-ambience) profile follows.
- Dolby BRIR design and reverberation-generation patents (numerically
  optimized BRIRs; reverberation generation for headphone virtualization),
  USPTO 10,834,519 and 12,143,797 — general BRIR/virtualization concepts,
  not implemented verbatim here.
