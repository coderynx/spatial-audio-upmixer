# Measured HRIR asset provenance

The checked-in binaural decode banks are derived from the SADIE II database,
subject D1 (Neumann KU100), using the 48 kHz, 256-tap,
`SimpleFreeFieldHRIR` diffuse-field-compensated SOFA file.  The source is
distributed by the AudioLab, University of York:

- Dataset record: <https://doi.org/10.5281/zenodo.10886409>
- Official database page: <https://www.york.ac.uk/sadie-project/database.html>
- Source archive: [`D1_HRIR_SOFA.zip`](https://zenodo.org/records/10886409/files/D1_HRIR_SOFA.zip/content),
  MD5 `4850c1eb8e63e2d4f605edcdb4d5c883`
- Official source file: `D1_48K_24bit_256tap_FIR_SOFA.sofa`
  (SHA-256 `e6c72a84dd947b5ef75438ab96a9c2a32ed10f033472b9c4c11a49aff00a8a31`)
- Build input: `D1_48K_24bit_256tap_FIR_SOFA_v21.sofa`, a v2.1
  NetCDF/HDF5 rewrap of that file (SHA-256
  `9af7cb19531e52fb7ae8ec92621e6ab62b1d5fe584b3742be36699a0ddb0ccd4`)
- Associated paper: C. Armstrong, L. Thresh, D. Murphy, and G. Kearney,
  “A Perceptual Evaluation of Individual and Non-Individual HRTFs: A Case
  Study of the SADIE II Database,” DOI
  <https://doi.org/10.3390/app8112029>

The source copyright notice is Copyright 2018, University of York. The SOFA
metadata declares the Apache License, Version 2.0 (SPDX: Apache-2.0;
<https://www.apache.org/licenses/LICENSE-2.0>). A full copy of that license is
included at [docs/third_party/SADIE-II-LICENSE.txt](../third_party/SADIE-II-LICENSE.txt).
Derived filter banks may therefore be redistributed under the license terms;
this attribution and citation are retained with the generated assets. The
original SOFA is a build-time input and is intentionally not committed. The
compact 0-degree plant fixture in
`packages/core/tests/measured_xtc_fixture.py` is a float32, Apache-2.0-derived
excerpt used to keep the measured-XTC regression checks self-contained. The v2.1
rewrap preserves the official file's source samples; the two hashes identify
the archive input and the exact build input separately.

## Generation

```text
uv run --with h5py python scripts/build_binaural_filters.py \
  --sofa /path/to/D1_48K_24bit_256tap_FIR_SOFA_v21.sofa
```

The generator selects the exact nominal BS.2051 positions from the SOFA (with
small spherical inverse-distance interpolation only as a fallback), excludes
LFE, and computes a full-column-rank left inverse of each layout's order-3
encoder.  The resulting 16 ACN × 2-ear bank therefore reconstructs each
layout speaker's measured HRIR exactly.  `flat` contains only the 256-tap
measured HRIR; `studio` and `listening` append a short, low-level
deterministic early-ambience tail (20 ms decay and 1 ms pre-delay). The flat
direct bank remains the transaural input.

Normalization preserves SADIE's diffuse-field-compensated calibration without
an additional arbitrary peak or RMS gain.  This identity policy is necessary
to retain the exact measured-HRIR reconstruction; generated values remain
floating-point WAV samples and are not peak-normalized to an arbitrary
coefficient.

Each profile/layout bank is named
`{profile}_o3_decode_{layout_with_dots_replaced_by_underscores}` for layouts
`stereo`, `5.1`, `7.1`, `5.1.2`, `5.1.4`, `7.1.2`, and `7.1.4`. The four
8-channel parts under
`packages/core/src/binaural/hrir/` and `apps/web/public/hrir/` are byte-for-byte
identical.

The original profile-only names are also retained as measured union-direction
compatibility banks for callers that do not provide a layout.

## Transaural XTC banks

The same measured SOFA is the speaker-to-ear plant for the five transaural
profiles.  `scripts/build_crosstalk_filters.py` reuses the loader and direction
selector above, keeps the existing regularized inverse, band blend, delay, and
1024-tap window, and writes one four-channel bank per profile to
`packages/core/src/crosstalk/xtc/`, then copies each file byte-for-byte to
`apps/web/public/xtc/`.

```text
uv run --with h5py python scripts/build_crosstalk_filters.py \
  --sofa /path/to/D1_48K_24bit_256tap_FIR_SOFA_v21.sofa
```

The current profile directions are all exact SOFA entries: 10 exact and 0
interpolated (`stereo` +30/−30°, `smart_speaker` +12/−12°, `car` +22/−42°,
`laptop` +14/−14°, and `phone` +6/−6°, all at 0° elevation).  Against that
measured plant over 300 Hz–6 kHz, the old parametric banks measured −2.5/4.1/
−4.7/2.8/6.5 dB leakage suppression for stereo/smart-speaker/car/laptop/phone;
the measured banks measure 39.2/16.5/38.7/23.9/13.3 dB, with ipsilateral
coloration ≤1.7 dB.

## Measured L/R tolerance

The KU100 measurements are intentionally not mirror-symmetrized.  Using the
same one-second seeded noise on every channel of each bed (`numpy` seed 0 at
48 kHz), the largest raw binaural center-bed imbalance is 1.896 dB (flat,
7.1.2); the largest symmetric transaural-profile imbalance after crosstalk
processing is 0.605 dB.  The focused regression checks therefore bound center
and mirror behavior to 2 dB.  This is a measured acceptance bound, not a
request to alter or normalize the shipped HRIR assets.
