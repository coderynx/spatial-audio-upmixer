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

## Validation Checklist

- [ ] BS.775-4: L/R nominal ±30° (tolerance ±10°); deviation stored in metadata
- [ ] BS.775-4: C nominal 0° (tolerance ±5°)
- [ ] BS.775-4: Ls/Rs nominal ±110°; acceptable range ±100°..±120° per BS.2051-3 Table 4
- [ ] LFE bandwidth limit = 120 Hz (not 80 Hz or 200 Hz) — per BS.775-4 Annex 7
- [ ] LFE level offset = −10 dB relative to full-scale channels — per BS.775-4 Annex 7 / BS.2051-3 Table 1 Note 5
- [ ] LFE excluded from downmix sum unless explicitly included
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
