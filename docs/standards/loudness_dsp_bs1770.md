# Loudness DSP — ITU-R BS.1770-5

**Source:** Recommendation ITU-R BS.1770-5 (11/2023)  
**Scope:** K-weighted integrated loudness (LUFS/LKFS) and true-peak level measurement.

---

## Core Objective

Measure subjective loudness of audio programmes using a two-stage pre-filter (K-weighting), channel-weighted mean-square summation, and 400 ms gating. Measure true-peak level via oversampled interpolation to catch inter-sample peaks invisible to sample-domain meters.

---

## K-Weighting Filter

Two cascaded biquad IIR sections. Both defined for 48 kHz; re-derive analytically for other sample rates to match the 48 kHz frequency response.

### Stage 1 — Pre-filter (spherical head model, high-shelf ≈ +4 dB @ ~1681 Hz)

Transfer function: Direct Form I, 2nd order IIR.

| Coefficient | Value (48 kHz) |
|---|---|
| b0 | 1.53512485958697 |
| b1 | −2.69169618940638 |
| b2 | 1.19839281085285 |
| a1 | −1.69065929318241 |
| a2 | 0.73248077421585 |

*(BS.1770-5 Annex 1, Table 1)*

### Stage 2 — RLB weighting (revised low-frequency B-curve, high-pass ≈ 38.1 Hz)

| Coefficient | Value (48 kHz) |
|---|---|
| b0 | 1.0 |
| b1 | −2.0 |
| b2 | 1.0 |
| a1 | −1.99004745483398 |
| a2 | 0.99007225036621 |

*(BS.1770-5 Annex 1, Table 2)*

The concatenation of Stage 1 + Stage 2 is designated **K-weighting**. Results are reported in **LKFS** (Loudness, K-weighted, relative to nominal Full Scale).

---

## Channel Weights

| Channel | Symbol | Weight (linear) | Weight (dB) |
|---|---|---|---|
| Left (L) | G_L | 1.0 | 0 dB |
| Right (R) | G_R | 1.0 | 0 dB |
| Centre (C) | G_C | 1.0 | 0 dB |
| Left Surround (Ls) | G_Ls | 1.41 | ≈ +1.5 dB |
| Right Surround (Rs) | G_Rs | 1.41 | ≈ +1.5 dB |
| LFE | — | **0.0** | **excluded** |

*(BS.1770-5 Annex 1, Table 3)*

> **Critical:** LFE is excluded from the LUFS sum. Surround weight is **1.41 (+1.5 dB)**, not 4 dB or 3 dB.

For advanced sound systems (BS.2051), weights for channels beyond 5-channel are defined in BS.1770-5 Annex 3.

---

## Measurement Programme

BS.1770 defines *how* to measure a programme, not *which* programme a
delivery specification asks for. For immersive deliveries these differ:

- **Dolby Atmos Music Master Delivery Specification** — integrated loudness
  is measured on the **5.1 re-render** of the mix, and that re-render is what
  distributor QC reads.
- **Netflix Atmos delivery** — same rule, fold-referenced, at −27 LKFS ±2 LU.

So a bed wider than 5.1 is folded before the meter, per the 5.1 re-render
matrix in `spatial_layouts_bs775_bs2051.md` §"5.1 re-render fold": front
heights onto the front pair, back surrounds and back heights onto the
surround pair. The folded programme is measured with the 5.1 weights above.
The full-bed number stays available as a secondary diagnostic
(`MasteringResult.full_bed_lkfs`), and the fold applies by **layout arity**,
not by output type — a stereo, binaural or transaural delivery already
measures its own two-channel programme.

Normalization drives off the same folded number. That needs no iteration:
the correction is one scalar gain across every channel, and a scalar gain
commutes with the fold.

**True peak is not fold-referenced.** The ceiling is what the limiter
guarantees on the channels actually written, so `measured_tp_dbtp` and the
per-channel peaks stay on the delivered bed.

Measured fold delta on synthetic 7.1.4 programmes: −0.32 dB on realistic
material, −2.35 dB on height-only content
(`docs/plans/mastering/phase0_report.md` § "Audit 1").

Implemented once in `dsp-core`'s `spatial::downmix::FoldTo51`, reached as
`upmixer_dsp.fold_to_51` by the export path (`loudness.py`'s
`measurement_programme`) and folded at the meter input by the preview's
`stream::measure`.

---

## Delivery Targets

Named targets in `upmixer.mastering.delivery`. A preset supplies both
numbers; `loudness_target_lkfs` / `loudness_max_tp` override it field by
field. Tolerance is `None` where the specification publishes a target
without one, and the compliance block then reports the measured number with
no pass/fail claim.

| Preset | Integrated | Ceiling | Tolerance | Source |
|---|---|---|---|---|
| `atmos-music` | −18.0 LKFS | −1.0 dBTP | — | Dolby Atmos Music Master Delivery Specification (fold-referenced) |
| `netflix-atmos` | −27.0 LKFS | −2.0 dBTP | ±2 LU | Netflix Atmos delivery specification (fold-referenced) |
| `ebu-r128` | −23.0 LUFS | −1.0 dBTP | ±0.5 LU | EBU R 128 |
| `atsc-a85` | −24.0 LKFS | −2.0 dBTP | ±2 LU | ATSC A/85 |
| `streaming-stereo` | −14.0 LUFS | −1.0 dBTP | — | Common streaming-stereo practice |
| `apple-music` | −16.0 LUFS | −1.0 dBTP | — | Apple Music sound-check target |

Deviation from Netflix's specification, recorded rather than implemented:
its −27 LKFS is **dialog-gated**, and this chain has no dialog gate — the
number is the ungated BS.1770 integrated loudness of the fold.

---

## Loudness Measurement Algorithm

### Per-channel mean square

```
z_i = (1/T) ∫₀ᵀ y_i²(t) dt
```

where y_i = K-weighted signal for channel i.

### Instantaneous loudness (ungated)

```
L_K = −0.691 + 10·log10( Σᵢ Gᵢ · zᵢ )   [LKFS]
```

The constant −0.691 cancels K-weighting gain at 997 Hz so a 0 dBFS 997 Hz sine on L/R/C reads −3.01 LKFS.

### Gated loudness blocks

- Block duration: **T_g = 400 ms** (to the nearest sample)
- Overlap: **75%** → hop size = 100 ms
- j ∈ {0, 1, 2, …, floor((T − T_g) / (T_g · step))} where step = 0.25

### Two-stage gating

**Stage 1 — Absolute gate:**
```
Γ_a = −70 LKFS
J_g = { j : l_j > Γ_a }
```

**Stage 2 — Relative gate:**
```
Γ_r = −0.691 + 10·log10( Σᵢ Gᵢ · (1/|J_g|) · Σ_{J_g} z_{ij} ) − 10  [LKFS]
J_g = { j : l_j > Γ_r  AND  l_j > Γ_a }
```

**Integrated loudness:**
```
L_KG = −0.691 + 10·log10( Σᵢ Gᵢ · (1/|J_g|) · Σ_{J_g} z_{ij} )   [LKFS]
```

*(BS.1770-5 Annex 1, equations 2–7)*

### Momentary and short-term loudness

- **Momentary:** 400 ms sliding window, no gating, updated continuously
- **Short-term:** 3 s sliding window, no gating, updated continuously

EBU Tech 3341 asks a meter for at least ten updates per second, so both
windows are evaluated on the standard's own 100 ms grid: the mean of the last
4 non-overlapping 100 ms blocks is the momentary window, the last 30 the
short-term one. Same K-weighting, same per-block mean square, same channel
weights as the integrated meter — only the gating is absent, which is what the
two windows are.

Two places measure them. Offline, `measure_loudness_stats` reports the maxima
over a finished render, which is what the compliance kit and the delivery
report read. Live, `WindowLoudnessMeter` slides the same windows over what the
preview has just emitted, at the emit position, and reads the *measurement
programme* — the 5.1 re-render for a native bed wider than 5.1, the delivered
pair otherwise — so the meter and the compliance number describe the same
signal.

### Crest metrics (PLR / PSR)

Neither is in BS.1770; both are ordinary mastering practice, reported by the
preview from numbers the standard does define.

- **PLR** (peak-to-loudness ratio): max true peak − integrated loudness, over
  the whole programme. Both terms come from the measurement pass.
- **PSR** (peak-to-short-term ratio): the highest peak in the current
  short-term window − short-term loudness. The live meters carry **sample**
  peak, not true peak, so the preview's PSR is a sample-peak crest and reads
  slightly low against a true-peak meter; PLR, which comes off the
  measurement pass, is true-peak throughout.

---

## True-Peak Measurement

*(BS.1770-5 Annex 2)*

True-peak is the maximum absolute value of the **continuous-time waveform**, not the sample-domain peak.

### Processing stages

1. **Attenuate:** −12.04 dB (2-bit shift) — headroom for integer arithmetic; skip in floating-point
2. **Oversample:** insert zeros to expand rate
3. **Low-pass filter:** FIR interpolation filter
4. **Absolute value**
5. **Convert:** 20·log10(|peak|) dBTP
6. **Compensate:** +12.04 dB

### Oversampling factor by input sample rate

| Input sample rate | Required oversampling | Output rate |
|---|---|---|
| ≤ 48 kHz | **4×** | 192 kHz |
| 96 kHz | **2×** | 192 kHz |

> Higher oversampling ratios are preferred and always acceptable.

**What this project runs.** `TRUE_PEAK_FIR_4X` is applied at 4× for every
output rate, 96 kHz included. This is compliant: 4× at 96 kHz reaches 384 kHz,
above the 192 kHz the table asks for, and the standard permits higher ratios.
The kernel is specified in normalized frequency, so its behaviour is a function
of `f/fs` alone — measured error at 96 kHz is identical to 48 kHz at the same
fractional frequency, and *lower* at any fixed physical frequency, because that
tone sits at half the fractional frequency.

Measured against exact band-limited interpolation of a periodic sine
(`packages/core/tests/test_master_measurement.py`, audit 3):

| f/fs | Detector error |
|---|---|
| 0.02 | +0.01 dB |
| 0.10 | +0.17 dB |
| 0.24 | +0.20 dB |
| 0.45 | +0.64 dB |

The error is always positive — the detector over-reads near Nyquist rather than
under-reading, so a limiter built on it stays conservative. Full numbers:
`docs/plans/mastering/phase0_report.md`.

### FIR Coefficients — order-48, 4-phase interpolating filter (≤48 kHz)

*(BS.1770-5 Annex 2, page 18)*

| Phase 0 | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| 0.0017089843750 | −0.0291748046875 | −0.0189208984375 | −0.0083007812500 |
| 0.0109863281250 | 0.0292968750000 | 0.0330810546875 | 0.0148925781250 |
| −0.0196533203125 | −0.0517578125000 | −0.0582275390625 | −0.0266113281250 |
| 0.0332031250000 | 0.0891113281250 | 0.1015625000000 | 0.0476074218750 |
| −0.0594482421875 | −0.1665039062500 | −0.2003173828125 | −0.1022949218750 |
| 0.1373291015625 | 0.4650878906250 | 0.7797851562500 | 0.9721679687500 |

Each phase has 6 coefficients (total: 24 taps per polyphase filter, 48-tap prototype). The full prototype FIR is obtained by interleaving the four phases.

### LFE and true-peak

LFE is **excluded** from LUFS measurement but **included** in true-peak scanning. Scan all channels including LFE for true-peak.

So a change to how the LFE bus is built moves a render's measured loudness only
through inter-channel masking, never directly — but it does move true peak. The
bus's filter and level calibration are specified in
`docs/standards/spatial_layouts_bs775_bs2051.md` § "LFE lowpass".

**Limiting LFE is not the same question as scanning it.** Scanning is
per-channel by the standard; what a limiter *couples* is a delivery choice the
standard does not make. This project derives one shared gain curve from the
mains' envelopes — that is what preserves imaging, since an identical
time-varying gain on every channel leaves inter-channel phase and level ratios
alone — and gives LFE its own curve from its own envelope. Both are capped at
the same ceiling, so every channel is still TP-compliant; only the coupling
between them is gone.

Coupling LFE into the shared curve makes an LFE-only peak duck the entire bed
one-for-one: with a `cinema` bass send putting 50% of the low bus into LFE, a
+6 dBFS LFE swell took 7.1 dB off the mains for 21% of the programme
(`docs/plans/mastering/phase0_report.md` § "Audit 2"). Concentrated in the
loud fifth of the programme and synchronised to the bass, that is audible
pumping while barely moving an RMS meter. Current immersive limiter practice
agrees: FLUX Elixir exposes channel-link as a control, Pulsar P21 Atlas shares
gain reduction across the bed with LFE excluded, and McDSP's surround limiters
group channels rather than linking all of them.

Implementation: `packages/dsp/crates/dsp-core/src/mastering/limiter.rs`
(`lookahead_limit`, and `stream/master.rs::StreamingLimiter` for the preview);
the deepest reduction on each curve is reported separately as
`MasteringResult.limiter_gr_peak_db` and `limiter_gr_lfe_peak_db`.

### Subsonic content and the measurement

The RLB stage is a ~38 Hz high-pass, so sub-20 Hz rumble and DC reach the
gating blocks attenuated but not removed, and they cost true-peak headroom at
full weight. The chain's optional head stage removes both before anything else
runs (`mastering/head.py`, `mastering::head`): a 12 dB/oct Butterworth
high-pass at a 10–30 Hz corner on every non-LFE channel, and a first-order
pole-zero DC blocker at 5 Hz on LFE, which is band-limited upstream and whose
sub content is the delivery. On a 440 Hz tone carrying a 15 Hz component at
−6 dB the head stage takes 3.0 dB off the limiter's peak gain reduction and
drops its duty to zero, with the audible band unchanged
(`unit_mastering_head_clip.rs`).

### Clipping ahead of the limiter

A memoryless clipper generates harmonics but no peak above its own ceiling, so
it cannot break the dBTP guarantee the limiter downstream makes: the limiter
still runs last, on the 4x-oversampled envelope of whatever the clipper
produced. What the clipper changes is how much work is left — it takes the
transients that would otherwise be met with deep, short gain reduction, so the
limiter's duty falls and less of the programme body is given away to reach the
same ceiling. The stage is off by default and its numbers are in
`docs/plans/mastering/phase4_report.md`, including the aliasing it costs: it
does not oversample in v1, so its odd harmonics past Nyquist fold back.

---

## Export Tail

The dBTP ceiling is only a delivery guarantee if nothing after the limiter can
raise a peak or move a sample. Three rules make that so, in this order:

**1. Master at the delivery rate.** Sample-rate conversion runs *before*
`MasteringChain`, never after (`pipeline.py`, and the stem path separates at
the delivery rate so the question does not arise). A resampler after the
limiter would reconstruct new inter-sample peaks above the ceiling the limiter
just enforced, and the loudness measurement written to the BWF `bext` chunk
would describe a programme the file no longer contains. Pinned by
`packages/core/tests/test_export_tail.py`.

The conversion itself is `upmixer.resample`: a polyphase stage whose
anti-imaging FIR is a Kaiser design at a 120 dB stopband with the transition
spanning 10% of the lower of the two rates. SciPy's `resample_poly` default
window is not adequate for delivery — measurements for both in
`docs/plans/mastering/phase6_report.md`.

**2. Quantize last, with dither.** Bit-depth reduction is the final operation
before samples leave the process, and it is the only place rounding happens.
`io.writer.dither_channels` maps float64 onto the target subtype's integer
lattice with ±1 LSB TPDF dither (`dsp-core/src/dither.rs`), for integer PCM
subtypes only; float and lossy subtypes pass through. `output_dither` selects
`off` (round to nearest), `tpdf` (the default) or `shaped` (TPDF plus
second-order `(1 - z⁻¹)²` error feedback, which trades ~7 dB of total noise
power for a much quieter low band).

Undithered reduction is not merely noisy: without dither the error correlates
with the signal, which is distortion rather than noise, and libsndfile's own
float→integer conversion truncates rather than rounds, adding a −½ LSB DC
offset and 6 dB of avoidable error power. Non-subtractive TPDF costs √3 of the
round-to-nearest error RMS and removes both.

The dither is below the ceiling by construction — ±1 LSB at 24-bit is about
−138 dBFS, at 16-bit about −90 dBFS — so true peak is **not** re-measured
after quantization.

**3. Nothing after.** No gain, no normalization, no fade may run once the bed
is quantized; a scalar applied afterwards moves every sample off the code
lattice and libsndfile re-quantizes it undithered. `write_audio` therefore
performs the quantization itself, as its last act before handing bytes to the
container, and `AdmBwfWriter` uses the same helper for its PCM payload.

Dither streams are independent per channel (seeded from
`output_dither_seed + channel_index`), so two identical channels do not
deliver identical samples — correlated dither would place its noise in the
centre image. The seed is fixed in config, so re-rendering the same job is
byte-identical.

---

## Validation Checklist

- [ ] Stage 1 coefficients match Table 1 values exactly at 48 kHz
- [ ] Stage 2 coefficients match Table 2 values exactly at 48 kHz
- [ ] For 96 kHz input: coefficients re-derived to match 48 kHz frequency response shape
- [ ] Gating block = 400 ms; hop = 100 ms (75% overlap)
- [ ] Absolute gate threshold = −70 LKFS applied before relative gate
- [ ] Relative gate threshold = gated mean − 10 LU
- [ ] Surround channel weight = 1.41 (not 1.0, not sqrt(2) rounded to 1.414 — use 1.41 per spec Table 3)
- [ ] LFE weight = 0 in LUFS sum
- [ ] LFE included in true-peak scan
- [ ] LFE capped on its own limiter curve, outside the mains' shared gain link
- [ ] True-peak: at least 4× oversampling at ≤48 kHz, at least 2× at 96 kHz
      (this project runs 4× at every rate — see "What this project runs")
- [ ] True-peak result in dBTP (not dBFS)
- [ ] 0 dBFS 997 Hz sine on single L/R/C channel reads −3.01 LKFS (calibration check)
- [ ] Incomplete gating blocks at end of measurement interval not used
- [ ] Sample-rate conversion runs before mastering, never after the limiter
- [ ] Bit-depth reduction is dithered, happens once, and is the last operation
