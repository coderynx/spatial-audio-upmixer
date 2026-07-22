# Audio Definition Model — ITU-R BS.2076-3

**Source:** Recommendation ITU-R BS.2076-3 (02/2025)  
**Scope:** XML metadata model for describing audio tracks in BWF/BW64 files. Normative element hierarchy, ID scheme, typeDefinitions, audioBlockFormat sub-element tables, coordinate system, and time format.

---

## Core Objective

ADM provides a self-contained, renderer-agnostic XML description of every audio track in a file. Content part (audioProgramme → audioContent → audioObject → audioTrackUID) binds tracks to programme intent. Format part (audioPackFormat → audioChannelFormat → audioBlockFormat) describes spatial and technical encoding parameters. The two parts connect via audioObject referencing audioPackFormat and audioTrackUIDs.

Common channel/pack definitions (mono, stereo, 5.1) are defined externally in BS.2094 and may be referenced without embedding the XML.

---

## Element Hierarchy

```
audioProgramme (1..*)
  └── audioContent (1..*)
        └── audioObject (1..*)
              ├── audioPackFormatIDRef (1)
              └── audioTrackUIDRef (1..*)
                    └── audioTrackUID
                          ├── audioTrackFormatIDRef (0 or 1)
                          └── audioPackFormatIDRef (0 or 1)

audioPackFormat (1..*)
  └── audioChannelFormatIDRef (0..*)
  └── audioPackFormatIDRef (0..*)       ← nesting

audioChannelFormat (1..*)
  └── audioBlockFormat (1..*)

audioStreamFormat (0..*)
  ├── audioChannelFormatIDRef (0 or 1)  ← mutually exclusive with audioPackFormatIDRef
  └── audioPackFormatIDRef (0 or 1)     ← (per BS.2076-3; Dolby profile uses BOTH)
  └── audioTrackFormatIDRef (0..*)

audioTrackFormat (0..*)
  └── audioStreamFormatIDRef (1)
```

> For PCM audio, audioTrackFormat and audioStreamFormat SHOULD be omitted (§5.1). audioTrackUID references audioChannelFormat directly via audioTrackFormatIDRef chain.

---

## typeDefinitions

*(BS.2076-3 Tables A1-7, A1-10, A1-22)*

| typeDefinition | typeLabel | Use case |
|---|---|---|
| DirectSpeakers | 0001 | Channel-based; each channel maps to a loudspeaker |
| Matrix | 0002 | Matrixed signals (Mid/Side, Lt/Rt, downmix) |
| Objects | 0003 | Object-based; positional metadata per block |
| HOA | 0004 | Scene-based; Higher-Order Ambisonics coefficients |
| Binaural | 0005 | Binaural; for headphone playback |
| User Custom | 1yyy–Fyyy | Custom types |

> typeLabel in AP_yyyyxxxx and AC_yyyyxxxx IDs encodes typeDefinition as yyyy.

---

## ID Scheme

*(BS.2076-3 §6; UML Figure A1-1)*

| Element | Format | Example | Notes |
|---|---|---|---|
| audioProgramme | `APR_xxxx` | APR_1001 | 4 hex digits; sequential |
| audioContent | `ACO_xxxx` | ACO_1001 | 4 hex digits; sequential |
| audioObject | `AO_xxxx` | AO_1001 | 4 hex digits; sequential |
| audioPackFormat | `AP_yyyyxxxx` | AP_00010001 | yyyy=typeLabel; xxxx=sequential |
| audioChannelFormat | `AC_yyyyxxxx` | AC_00010001 | yyyy matches parent pack |
| audioBlockFormat | `AB_yyyyxxxx_zzzzzzzz` | AB_00010001_00000001 | zzzzzzzz=block index, starts at **0x00000001** |
| audioStreamFormat | `AS_yyyyxxxx` | AS_00010001 | yyyy+xxxx match referenced AC |
| audioTrackFormat | `AT_yyyyxxxx_zz` | AT_00010001_01 | zz=track index within stream; PCM always **01** |
| audioTrackUID | `ATU_xxxxxxxx` | ATU_00000001 | 8 hex digits; globally unique in file |

> BS.2076-3 does not mandate a specific start offset for xxxx counters. The Dolby Atmos Master ADM Profile v1.1 mandates xxxx start at **0x1001** — see `dolby_atmos_profile.md`.

> Common definitions (BS.2094) use IDs with leading zeros: `AP_00010001` (stereo pack), `AC_00010001` (FrontLeft). Custom content starts at `AP_00011001` or higher to avoid collision.

---

## Time Format

*(BS.2076-3 §5.13)*

Primary format: `HH:MM:SS.SSSSS` (5 decimal places, hours can exceed 24).

Sample-accurate form: `HH:MM:SS.SSSSSSNnnnnn` where `S` denotes sample count suffix — e.g. `00:00:05.00000S48000` = 5 seconds at 48 kHz.

Default for absent `rtime`: `00:00:00.00000`.  
Default for absent `duration`: unbounded (lasts to end of audioObject).

---

## Element Attributes

### audioTrackFormat

*(BS.2076-3 §5.1, Table A1-2)*

| Attribute | Required | Example |
|---|---|---|
| audioTrackFormatID | Yes | AT_00010001_01 |
| audioTrackFormatName | Yes | PCM_FrontLeft |
| formatLabel | Optional | 0001 |
| formatDefinition | Optional | PCM |

Sub-elements: `audioStreamFormatIDRef` (1; tolerate absence in legacy files per Note in §5.1.2).

---

### audioStreamFormat

*(BS.2076-3 §5.2, Tables A1-4, A1-5)*

| Attribute | Required | Example |
|---|---|---|
| audioStreamFormatID | Yes | AS_00010001 |
| audioStreamFormatName | Yes | PCM_FrontLeft |
| formatLabel | Optional | 0001 |
| formatDefinition | Optional | PCM |

Sub-elements:
- `audioChannelFormatIDRef` (0 or 1)
- `audioPackFormatIDRef` (0 or 1) — **mutually exclusive** with audioChannelFormatIDRef per BS.2076-3 §5.2.2
- `audioTrackFormatIDRef` (0..*)

> **Dolby deviation:** Dolby Atmos Master ADM Profile v1.1 requires BOTH audioChannelFormatIDRef AND audioPackFormatIDRef in audioStreamFormat — see `dolby_atmos_profile.md`.

---

### audioChannelFormat

*(BS.2076-3 §5.3, Tables A1-6, A1-7, A1-8)*

| Attribute | Required | Notes |
|---|---|---|
| audioChannelFormatID | Yes | AC_yyyyxxxx |
| audioChannelFormatName | Yes | e.g. FrontLeft |
| typeLabel | At least one of typeLabel/typeDefinition | e.g. 0001 |
| typeDefinition | At least one of typeLabel/typeDefinition | e.g. DirectSpeakers |

Sub-elements:
- `audioBlockFormat` (1..*) — mandatory, at least one
- `frequency` (0..2) — optional; `typeDefinition="lowPass"` or `"highPass"`, value in Hz

---

### audioBlockFormat

*(BS.2076-3 §5.4, Table A1-9)*

| Attribute | Required | Default |
|---|---|---|
| audioBlockFormatID | Yes | — |
| rtime | Optional | 00:00:00.00000 |
| duration | Optional | unbounded |

> If only one audioBlockFormat in audioChannelFormat: rtime and duration may be omitted (static channel). If multiple blocks: both shall be present.

#### Common sub-elements — all typeDefinitions (Table A1-11)

| Sub-element | Attribute | Units | Quantity | Default |
|---|---|---|---|---|
| gain | gainUnit (`linear` or `dB`) | linear/dB | 0 or 1 | 1.0 (linear) |
| importance | — | 0–10 | 0 or 1 | 10 |
| jumpPosition | — | 1/0 flag | 0 or 1 | 0 |
| jumpPosition | interpolationLength | time | 0 or 1 | 0 (applies only when jumpPosition=1) |

> jumpPosition=1 with no interpolationLength → instantaneous jump. jumpPosition=1 with interpolationLength → interpolate over that duration.

#### Common sub-elements — all except Binaural and Matrix (Table A1-12)

| Sub-element | Attribute | Quantity | Default |
|---|---|---|---|
| headLocked | — | 0 or 1 | 0 |
| headphoneVirtualise | bypass, DRR | 0 or 1 | 0 (bypass), 130 dB (DRR) |

---

#### audioBlockFormat sub-elements — DirectSpeakers, polar (Table A1-13)

| Sub-element | Attribute | Quantity | Notes |
|---|---|---|---|
| speakerLabel | — | 0..* | SP Label string (e.g. M-030) |
| position | coordinate="azimuth" | 1 | degrees, exact |
| position | coordinate="azimuth" + max | 0 or 1 | degrees, max range |
| position | coordinate="azimuth" + min | 0 or 1 | degrees, min range |
| position | coordinate="elevation" | 1 | degrees |
| position | coordinate="elevation" + max/min | 0 or 1 each | degrees |
| position | coordinate="distance" | 0 or 1 | normalized to 1; default 1.0 |
| position | screenEdgeLock | 0..2 | "left", "right", "top", "bottom" |

---

#### audioBlockFormat sub-elements — DirectSpeakers, Cartesian (Table A1-14)

| Sub-element | Attribute | Quantity | Notes |
|---|---|---|---|
| speakerLabel | — | 0..* | SP Label string |
| cartesian | — | 1 | 1/0 flag; 1=Cartesian |
| position | coordinate="X" | 1 | relative units; −1=right, +1=left |
| position | coordinate="Y" | 1 | relative units; −1=back, +1=front |
| position | coordinate="Z" | 0 or 1 | relative units; −1=bottom, +1=top |
| position | coordinate="X"/"Y"/"Z" + max/min | 0 or 1 each | |
| position | screenEdgeLock | 0..2 | |

---

#### audioBlockFormat sub-elements — Objects, polar (Table A1-17)

| Sub-element | Attribute | Units / Range | Quantity | Default |
|---|---|---|---|---|
| position | coordinate="azimuth" | degrees; [−180, 180] | 1 | — |
| position | coordinate="elevation" | degrees; [−90, 90] | 1 | — |
| position | coordinate="distance" | normalized [0, 1] | 0 or 1 | 1.0 |
| width | — | degrees [0, 360] | 0 or 1 | 0.0 |
| height | — | degrees [0, 360] | 0 or 1 | 0.0 |
| depth | — | ratio [0, 1] | 0 or 1 | 0.0 |
| objectDivergence | azimuthRange | 0–1.0; 0–180° | 0 or 1 | 0.0, 0.0 |
| zoneExclusion | zone sub-elements | see §10.3 | 0 or 1 | — |

---

#### audioBlockFormat sub-elements — Objects, Cartesian (Table A1-18)

| Sub-element | Attribute | Units / Range | Quantity | Default |
|---|---|---|---|---|
| position | coordinate="X" | relative [−1, 1]; −1=right, +1=left | 1 | — |
| position | coordinate="Y" | relative [−1, 1]; −1=back, +1=front | 1 | — |
| position | coordinate="Z" | relative [−1, 1]; −1=bottom, +1=top | 0 or 1 | 0.0 |
| width | — | relative [0, 1] | 0 or 1 | 0.0 |
| depth | — | relative [0, 1] | 0 or 1 | 0.0 |
| height | — | relative [0, 1] | 0 or 1 | 0.0 |
| objectDivergence | positionRange | 0–1.0; 0–1.0 | 0 or 1 | 0.0, 0.0 |
| zoneExclusion | zone sub-elements (minX/maxX/minY/maxY/minZ/maxZ) | see §10.3 | 0 or 1 | — |

#### audioBlockFormat sub-elements — Objects, coordinate-independent (Table A1-19)

| Sub-element | Attribute | Units | Quantity | Default |
|---|---|---|---|---|
| cartesian | — | 1/0 flag | 0 or 1 | 0 (spherical) |
| diffuse | — | 0.0–1.0 | 0 or 1 | 0 |
| channelLock | maxDistance | 1/0 flag; float [0, 2√3] | 0 or 1 | 0; ∞ |
| screenRef | — | 1/0 flag | 0 or 1 | 0 |

---

#### audioBlockFormat sub-elements — HOA (Table A1-20)

| Sub-element | Units | Quantity | Required |
|---|---|---|---|
| order | integer | 0 or 1 | Yes |
| degree | integer | 0 or 1 | Yes |
| normalization | string (N3D, SN3D, FuMa) | 0 or 1 | Optional; default SN3D |
| nfcRefDist | metres | 0 or 1 | Optional |
| screenRef | 1/0 flag | 0 or 1 | Optional |
| equation | string | 0 or 1 | Informative only |

---

### audioPackFormat

*(BS.2076-3 §5.5, Tables A1-21, A1-22, A1-23)*

| Attribute | Required | Notes |
|---|---|---|
| audioPackFormatID | Yes | AP_yyyyxxxx |
| audioPackFormatName | Yes | e.g. stereo |
| typeLabel | At least one | e.g. 0001 |
| typeDefinition | At least one | e.g. DirectSpeakers |
| importance | Optional | 0–10; default 10 |

Sub-elements:
- `audioChannelFormatIDRef` (0..*) — references to audioChannelFormat elements
- `audioPackFormatIDRef` (0..*) — nesting; pack contains sub-packs
- `absoluteDistance` (0 or 1) — metres; for distance-based binaural rendering

---

### audioObject

*(BS.2076-3 §5.6)*

| Attribute | Required | Notes |
|---|---|---|
| audioObjectID | Yes | AO_xxxx |
| audioObjectName | Yes | UTF-8 string |
| start | Optional | time; default 00:00:00.00000 |
| duration | Optional | time; default = full programme duration |
| importance | Optional | 0–10 |
| interact | Optional | 1/0 flag |
| disableDucking | Optional | 1/0 flag |

Sub-elements:
- `audioPackFormatIDRef` (1) — exactly one per audioObject
- `audioTrackUIDRef` (1..*) — one per audio track used by this object
- `audioObjectIDRef` (0..*) — references to other audioObjects (nesting)
- `audioComplementaryObjectGroupLabelIDRef` (0..*) — personalisation groups

---

### audioContent

*(BS.2076-3 §5.7)*

| Attribute | Required | Notes |
|---|---|---|
| audioContentID | Yes | ACO_xxxx |
| audioContentName | Yes | UTF-8 string |
| audioContentLanguage | Optional | BCP-47 language tag |

Sub-elements:
- `audioObjectIDRef` (1..*) — references to audioObjects
- `loudnessMetadata` (0..*) — carries LUFS, true-peak, etc.
- `dialogue` (0 or 1) — value: 0=non-dialogue, 1=dialogue, 2=mixed

---

### audioProgramme

*(BS.2076-3 §5.8)*

| Attribute | Required | Notes |
|---|---|---|
| audioProgrammeID | Yes | APR_xxxx |
| audioProgrammeName | Yes | UTF-8 string |
| audioProgrammeLanguage | Optional | BCP-47 language tag |
| start | Optional | time matching audio essence start |
| end | Optional | time matching audio essence end |
| maxDuckingDepth | Optional | dB |

Sub-elements:
- `audioContentIDRef` (1..*) — references to audioContent elements
- `loudnessMetadata` (0..*) — programme-level loudness
- `audioProgrammeReferenceScreen` (0 or 1) — screen dimensions

---

### audioTrackUID

*(BS.2076-3 §5.9)*

| Attribute | Required | Notes |
|---|---|---|
| UID | Yes | ATU_xxxxxxxx |
| sampleRate | Optional | Hz; e.g. 48000 |
| bitDepth | Optional | bits; e.g. 24 |

Sub-elements (choose one chain):
- `audioTrackFormatIDRef` (0 or 1) + `audioPackFormatIDRef` (0 or 1) — for PCM via audioTrackFormat chain
- `audioChannelFormatIDRef` (0 or 1) — direct reference (PCM shortcut)

> ATU_00000000 is the special zero UID used to indicate a missing/silent track in the `<chna>` chunk.

---

## Coordinate System

*(BS.2076-3 §8)*

### Polar (spherical) — default

| Coordinate | Attribute | Range | Convention |
|---|---|---|---|
| azimuth | coordinate="azimuth" | [−180°, +180°] | 0=front, positive=left |
| elevation | coordinate="elevation" | [−90°, +90°] | 0=horizontal, positive=up |
| distance | coordinate="distance" | [0, 1] normalized | 1.0 = on unit sphere |

### Cartesian — when `<cartesian>1</cartesian>`

| Coordinate | Attribute | Range | Convention |
|---|---|---|---|
| X | coordinate="X" | [−1, +1] | −1=right, +1=left |
| Y | coordinate="Y" | [−1, +1] | −1=back, +1=front |
| Z | coordinate="Z" | [−1, +1] | −1=bottom, +1=top |

> Cartesian cube: [−1, +1]³. Position (0, 1, 0) = dead front. Position (−1, 1, 0) = front-right (Dolby uses this for RC_R).

---

## BWF Integration — `<chna>` Chunk

*(BS.2076-3 §7)*

The `<chna>` chunk maps BWF tracks to ADM IDs. Each row: `trackNum` (1-based), `UID` (ATU_xxxxxxxx), `trackRef` (AT_yyyyxxxx_zz), `packRef` (AP_yyyyxxxx).

Zero UID (`ATU_00000000`) = track absent or silent — use when a bed channel position exists in the pack but has no corresponding audio track.

---

## Known Compatibility Issues

*(BS.2076-3 Annex 3 — changes from BS.2076-2)*

| Issue | Detail |
|---|---|
| `audioStreamFormatIDRef` in audioTrackFormat | Quantity changed from "0 or 1" to "1" — readers should tolerate absent sub-element |
| `audioTrackFormatIDRef` in audioStreamFormat | Quantity changed from "1" to "0..*" — some legacy files have exactly 1 |
| `LeftEar`/`RightEar` vs `leftEar`/`rightEar` | Binaural channel name capitalization changed in BS.2076-3 — readers should tolerate both |
| `outputChannelIDRef` vs `outputChannelFormatIDRef` | Matrix element name changed — readers should accept both |

---

## Validation Checklist

- [ ] Each audioProgramme references ≥1 audioContent via audioContentIDRef
- [ ] Each audioContent references ≥1 audioObject
- [ ] Each audioObject references exactly 1 audioPackFormat and ≥1 audioTrackUID
- [ ] audioTrackUID UID values are unique within the file
- [ ] ATU_00000000 not used except for absent/silent track placeholder in chna
- [ ] audioBlockFormatID zzzzzzzz index starts at 00000001 (not 00000000)
- [ ] All audioBlockFormats within one audioChannelFormat share the same yyyy digits as parent audioChannelFormat
- [ ] If multiple audioBlockFormats: rtime and duration present on all
- [ ] If single audioBlockFormat: rtime and duration may be omitted
- [ ] audioStreamFormat contains audioChannelFormatIDRef OR audioPackFormatIDRef, not both (unless Dolby ADM profile — see dolby_atmos_profile.md)
- [ ] typeLabel and typeDefinition match between audioPackFormat and its audioChannelFormats
- [ ] Polar azimuth 0=front, positive=left; NOT the opposite convention
- [ ] Cartesian X: positive=left (not right); Y: positive=front; Z: positive=up
- [ ] Time format HH:MM:SS.SSSSS (5 decimal places); sample suffix Snnnnn if needed
- [ ] LFE channel audioChannelFormat: add `<frequency typeDefinition="lowPass">120</frequency>` sub-element per BS.775-4 Annex 7
- [ ] Common definitions (BS.2094) referenced externally when used; do not re-define stereo/5.1 custom if identical to common defs
- [ ] HOA audioBlockFormat: both `order` and `degree` present; `normalization` default is SN3D
- [ ] Binaural audioChannelFormat name: "LeftEar" or "RightEar" (BS.2076-3); tolerate "leftEar"/"rightEar" when reading
