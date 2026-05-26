# Dolby Atmos Master ADM Profile v1.1

**Source:** Dolby Atmos Master ADM Profile, Version 1.1 (5 January 2022)  
**Scope:** Strict subset of ITU-R BS.2076-0 for master ADM-BWF delivery of Dolby Atmos content.

---

## Core Objective

Define the precise ADM XML structure and BWF chunk requirements for a Dolby Atmos master file. Any element, attribute, or sub-element **not listed** in this profile causes non-compliance and **shall be rejected** by conformant tools.

---

## General Requirements

- ADM XML shall conform to **ITU-R BS.2076-0** (not BS.2076-2 or BS.2076-3)
- XML 1.0 specification, **UTF-8** character encoding
- Integers and floats: **decimal representation, no leading zeros**
- Presence of any unlisted XML elements/attributes/sub-elements = non-compliant

---

## Element Count Constraints

| Variable | Value | Meaning |
|---|---|---|
| MAX_AO_DSPKS | 64 | Max audio beds (DirectSpeakers audioObjects) |
| MAX_AO_OBJCT | 118 | Max dynamic audio objects |
| MAX_CHANNEL_COUNT | 128 | Max audio channels |
| MAX_ELEMENT_COUNT | 123 | Max audioObjects + beds combined |
| MIN_ELEMENT_COUNT | 1 | Minimum |

| XML element | Min | Max |
|---|---|---|
| audioTrackFormat | 1 | 128 |
| audioStreamFormat | 1 | 128 |
| audioChannelFormat | 1 | 128 |
| audioPackFormat | 1 | 123 |
| audioObject | 1 | 123 |
| audioContent | 1 | 123 |
| audioProgramme | 1 | **1** |
| audioTrackUID | 1 | 128 |

---

## ID Numbering Scheme

*(Dolby ADM Profile v1.1 §3)*

| Element | Format | yyyy | xxxx start | Notes |
|---|---|---|---|---|
| audioPackFormat | `AP_yyyyxxxx` | 0001 or 0003 | **0x1001** | per typeDefinition |
| audioChannelFormat | `AC_yyyyxxxx` | 0001 or 0003 | **0x1001** | independent counter per yyyy |
| audioBlockFormat | `AB_yyyyxxxx_zzzzzzzz` | matches parent AC | matches parent AP | zzzzzzzz starts at **0x00000001** |
| audioStreamFormat | `AS_yyyyxxxx` | 0001 or 0003 | **0x1001** | shared counter with AT |
| audioTrackFormat | `AT_yyyyxxxx_zz` | matches AS | shared with AS | zz fixed at **0x01** |
| audioProgramme | `APR_wwww` | — | 0x1001 | |
| audioContent | `ACO_wwww` | — | 0x1001 | |
| audioObject | `AO_wwww` | — | 0x1001 | |
| audioTrackUID | `ATU_vvvvvvvv` | — | — | see §3.6 |

> **Critical:** All xxxx counters start at `0x1001`, not `0x0001` or `0x0000`. Counters are independent per typeDefinition group.

### yyyy values by typeDefinition

| yyyy | typeLabel | typeDefinition |
|---|---|---|
| 0001 | 0001 | DirectSpeakers |
| 0003 | 0003 | Objects |

---

## audioTrackUID Constraints

*(Dolby ADM Profile v1.1 §2.10, Table 2-29)*

| Attribute | Constraint |
|---|---|
| UID | See §3.6 |
| sampleRate | Must equal `nSamplesPerSec` in `<fmt>` chunk. Valid: **48000 or 96000** |
| bitDepth | Must equal `wBitsPerSample` in `<fmt>` chunk. Only valid value: **24** |

Sub-elements required: `audioTrackFormatIDRef` (1), `audioPackFormatIDRef` (1). No others.

---

## audioTrackFormat Constraints

| Attribute | Constraint |
|---|---|
| audioTrackFormatID | Per §3.2 |
| audioTrackFormatName | Must equal `audioStreamFormatName` of referenced stream |
| formatLabel | Shall be set to `0001` |
| formatDefinition | Shall be set to `PCM` |

---

## audioStreamFormat Constraints

| Attribute | Constraint |
|---|---|
| audioStreamFormatName | Concatenation of `'PCM_'` + `audioChannelFormatName` of referenced channel |
| formatLabel | Shall be set to `0001` |
| formatDefinition | Shall be set to `PCM` |

> Both `audioChannelFormatIDRef` AND `audioPackFormatIDRef` shall be present (Dolby deviation from BS.2076-0 which requires mutual exclusivity).

---

## audioChannelFormat Constraints

*(Table 2-8, Table 2-11)*

| Attribute | Constraint |
|---|---|
| typeLabel | `0001` or `0003` |
| typeDefinition | `DirectSpeakers` or `Objects` |

**DirectSpeakers** — use only custom definitions (no common definitions). audioChannelFormatName values:

| Channel | audioChannelFormatName |
|---|---|
| L | RoomCentricLeft |
| R | RoomCentricRight |
| C | RoomCentricCenter |
| LFE | RoomCentricLFE |
| Lss | RoomCentricLeftSideSurround |
| Rss | RoomCentricRightSideSurround |
| Lrs | RoomCentricLeftRearSurround |
| Rrs | RoomCentricRightRearSurround |
| Lts | RoomCentricLeftTopSurround |
| Rts | RoomCentricRightTopSurround |
| Ls | RoomCentricLeftSurround |
| Rs | RoomCentricRightSurround |

**Max audioBlockFormat per audioChannelFormat:**
- DirectSpeakers: max **1**
- Objects: no limit

---

## audioBlockFormat — DirectSpeakers

*(Table 2-13, Table 2-14)*

Required sub-elements:
- `cartesian`: **shall be set to 1** (min 1, max 1)
- `speakerLabel`: see Table 2-14 (min 1, max 1)
- `position`: exactly 3 elements, one per axis X/Y/Z (min 3, max 3)
- All other sub-elements: **shall not be present** (including `jumpPosition`)

Cartesian position values per channel (Table 2-14):

| Channel | speakerLabel | X | Y | Z |
|---|---|---|---|---|
| L | RC_L | −1.0 | 1.0 | 0.0 |
| R | RC_R | 1.0 | 1.0 | 0.0 |
| C | RC_C | 0.0 | 1.0 | 0.0 |
| LFE | RC_LFE | −1.0 | 1.0 | −1.0 |
| Lss | RC_Lss | −1.0 | 0.0 | 0.0 |
| Rss | RC_Rss | 1.0 | 0.0 | 0.0 |
| Lrs | RC_Lrs | −1.0 | −1.0 | 0.0 |
| Rrs | RC_Rrs | 1.0 | −1.0 | 0.0 |
| Lts | RC_Lts | −1.0 | 0.0 | 1.0 |
| Rts | RC_Rts | 1.0 | 0.0 | 1.0 |
| Ls | RC_Ls | −1.0 | −1.0 | 0.0 |
| Rs | RC_Rs | 1.0 | −1.0 | 0.0 |

---

## audioBlockFormat — Objects

*(Table 2-15)*

Required sub-elements:

| Sub-element | Constraint | Min | Max |
|---|---|---|---|
| `cartesian` | **Shall be set to 1** | 1 | 1 |
| `position` | coordinate attr only (X, Y, Z); value ∈ [−1.0, 1.0] | 3 | 3 |
| `jumpPosition` | **Mandatory** (min 1, max 1) | 1 | 1 |
| `gain` | optional | 0 | 1 |
| `importance` | optional; if 0 then gain shall also be present = 0.0 | 0 | 1 |
| `width`, `depth`, `height` | if any present, all three must be present; value ∈ [0.0, 1.0] | 0 | 1 |
| `diffuse` | shall be 0 or 1 | 0 | 1 |
| `channelLock` | optional; maxDistance attr shall not be present | 0 | 1 |
| `zoneExclusion` | see §2.5.1 | 0 | 1 |
| all other sub-elements | **shall not be present** | 0 | 0 |

### jumpPosition — interpolationLength rules

*(Table 2-15, §2.5.2)*

- `jumpPosition` value: always **1**
- `interpolationLength`:
  - For blocks where `zzzzzzzz` in `AB_yyyyxxxx_zzzzzzzz` = `0x00000001` (first block): **`0.000000`** (0 samples)
  - For all other blocks: **`0.005208`** (≈ 250 samples @ 48 kHz; ≈ 500 samples @ 96 kHz)

> Renderer treats all audioBlock instances as discrete metadata events. interpolationLength is static, not a ramp.

---

## Zone Exclusion

*(§2.5.1, Tables 2-16, 2-17)*

When `zoneExclusion` is present, `zone` sub-elements can only use values from the following sets:

**Basic zone sets:**

| Value | Description | minX | maxX | minY | maxY | minZ | maxZ |
|---|---|---|---|---|---|---|---|
| ZM1 | Back zone disabled | −1.0 | 1.0 | −1.0 | −0.41934 | −0.499 | 0.499 |
| ZM2L | Side zone disabled (left) | −1.0 | −0.75806 | −0.41934 | 0.83871 | −0.499 | 0.499 |
| ZM2R | Side zone disabled (right) | 0.75806 | 1.0 | −0.41934 | 0.83871 | −0.499 | 0.499 |
| ZM3L | Center-back left | −1.0 | −0.16129 | 0.5 | 1.0 | −0.499 | 0.499 |
| ZM3Lss | Center-back Lss | −1.0 | −0.51611 | −0.707 | 0.49999 | −0.499 | 0.499 |
| ZM3R | Center-back right | 0.16129 | 1 | 0.5 | 1.0 | −0.499 | 0.499 |
| ZM3Rss | Center-back Rss | 0.51611 | 1 | −0.707 | 0.49999 | −0.499 | 0.499 |
| ZM4 | Screen zone enabled | −1.0 | 1.0 | −1.0 | 0.83871 | −0.499 | 0.499 |
| ZM5 | Surround zone enabled | −1.0 | 1.0 | 0.5 | 1.0 | −0.499 | 0.499 |

**Elevation zone sets (optional, combined with one basic zone):**

| Value | Description | minZ | maxZ |
|---|---|---|---|
| ZB | Elevation (floor) disabled | −1.0 | −0.4995 |
| ZT | Elevation (ceiling) disabled | 0.4995 | 1.0 |

---

## audioObject Constraints

*(Table 2-22, Table 2-23)*

| Attribute | Constraint |
|---|---|
| audioObjectID | per §3.5 |
| audioObjectName | 1–64 chars UTF-8 |
| `start` | **shall be set to `00:00:00.00000`** |
| `duration` | shall equal (audioProgramme.end − audioProgramme.start) |
| all other attributes | **shall not be present** |

> **Known issue:** Some older Dolby Atmos master ADM BWF files use `startTime` instead of `start`. See §4 Known Issues. Writers shall use `start`.

Sub-elements:
- `audioPackFormatIDRef`: exactly 1
- `audioTrackUIDRef`: min 1, max depends on yyyy (0001→max 10, 0003→max 1)
- all other sub-elements: shall not be present

---

## audioContent Constraints

| Attribute | Required | Constraint |
|---|---|---|
| audioContentID | Yes | per §3.4 |
| audioContentName | Yes | 1–64 chars UTF-8 |
| all other attributes | — | shall not be present |

Sub-elements: `audioObjectIDRef` (min 1), `dialogue` (exactly 1), no others.

`dialogue` value: **2**; `mixedContentKind` attribute: **0**.

*(Note: dialogue/mixedContentKind values are structural boilerplate for ADM schema compliance; they do not carry meaningful content metadata.)*

---

## audioProgramme Constraints

| Attribute | Constraint |
|---|---|
| audioProgrammeID | per §3.3 |
| audioProgrammeName | 1–64 chars UTF-8 |
| `start` | time difference matches PCM audio essence start |
| `end` | time difference matches PCM audio essence end |
| all other attributes | **shall not be present** |

Exactly **1** audioProgramme per file.

Optional sub-element: `audioProgrammeReferenceScreen` (0 or 1); if present, only `screenWidth` + attribute `X` shall be present.

---

## Channel Configuration Sets

*(Table 2-21)*

Only the following channel configurations are allowed, with the specified channel order:

| Config | Channel Order |
|---|---|
| 2.0 | L R |
| 3.0 | L R C |
| 5.0 | L R C Ls Rs |
| 5.1 | L R C LFE Ls Rs |
| 7.0 | L R C Lss Rss Lrs Rrs |
| 7.1 | L R C LFE Lss Rss Lrs Rrs |
| 7.0.2 | L R C Lss Rss Lrs Rrs Lts Rts |
| 7.1.2 | L R C LFE Lss Rss Lrs Rrs Lts Rts |

---

## BWF Chunk Requirements

*(Dolby ADM Profile v1.1 §7)*

| Chunk | Required | Notes |
|---|---|---|
| `<fmt >` | Mandatory | wBitsPerSample=24; nSamplesPerSec∈{48000,96000} |
| `<data>` | Mandatory | PCM audio essence |
| `<chna>` | Mandatory | Track UID / Track Format ID mapping |
| `<axml>` | Mandatory | Contains ADM XML |
| `<dbmd>` | Mandatory | Dolby metadata (EBU Tech 3285 Supplement 6) |

> `<dbmd>` is commonly omitted by non-Dolby implementations — its absence causes rejection by Dolby validation tools.

---

## ADM Version Compatibility

*(§4 — ADM versions and known issues)*

Profile targets BS.2076-0. Known divergences in existing files:

| Issue | Detail |
|---|---|
| `startTime` vs `start` | Older files use `startTime` on audioObject. Writers: use `start`. Readers: tolerate `startTime`. |
| Namespace URN | BS.2076-0 namespace; some tools emit BS.2076-2 ebuCore_2014 namespace — causes `startTime`/`start` attribute divergence |
| Both audioChannelFormatIDRef and audioPackFormatIDRef in audioStreamFormat | Dolby files contain both even though BS.2076-0 specifies mutual exclusivity |

---

## Validation Checklist

- [ ] sampleRate ∈ {48000, 96000} in both `<fmt>` chunk and audioTrackUID attributes
- [ ] bitDepth = 24 in both `<fmt>` chunk (wBitsPerSample) and audioTrackUID
- [ ] Exactly 1 audioProgramme element
- [ ] All ID counters start at 0x1001 (not 0x0001)
- [ ] audioBlockFormat zzzzzzzz counter starts at 0x00000001
- [ ] audioTrackFormat.zz = 0x01
- [ ] All DirectSpeakers audioBlockFormat: cartesian=1, no jumpPosition
- [ ] All Objects audioBlockFormat: cartesian=1, jumpPosition present (min 1, max 1)
- [ ] jumpPosition.interpolationLength = 0.000000 for first block (zzzzzzzz=0x1), 0.005208 for all others
- [ ] audioObject.start = "00:00:00.00000" (attribute name "start", not "startTime")
- [ ] audioContent.dialogue = 2, mixedContentKind = 0
- [ ] `<dbmd>` chunk present in BWF file
- [ ] `<chna>` chunk present in BWF file
- [ ] No unlisted XML attributes or sub-elements anywhere in the ADM tree
- [ ] Channel configuration matches one of the 8 allowed sets (Table 2-21)
- [ ] DirectSpeakers speakerLabel values from RC_* set only (Table 2-14)
- [ ] DirectSpeakers position coordinates match Table 2-14 exactly
