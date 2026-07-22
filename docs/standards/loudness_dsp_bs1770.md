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

### Momentary and short-term loudness (informative)

- **Momentary:** 400 ms sliding window, no gating, updated continuously
- **Short-term:** 3 s sliding window, no gating, updated continuously

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
- [ ] True-peak: 4× oversampling at ≤48 kHz; 2× oversampling at 96 kHz
- [ ] True-peak result in dBTP (not dBFS)
- [ ] 0 dBFS 997 Hz sine on single L/R/C channel reads −3.01 LKFS (calibration check)
- [ ] Incomplete gating blocks at end of measurement interval not used
