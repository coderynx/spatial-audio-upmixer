# Phase 7 — Elevation EQ directional-band retune (optional)

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 3 merged (height sends must already be velvet-decorrelated
so listening comparisons isolate the EQ change). Optional: ship only if
the A/B clearly wins.

## Goal

`elevation_eq` (dsp-core `routing/sends.rs`, served to all height sends)
approximates the elevation cue as sub-bass rolloff + a broad +1.5 high
shelf at 3 kHz. Psychoacoustics (Blauert's directional bands) places the
"above" cue in a band around ~7–8 kHz; a band emphasis there produces
more convincing elevation with less overall brightness shift than a
3 kHz-up shelf.

## Deliverables

1. Add a peaking band stage to `elevation_eq` in `dsp-core`: center
   ~8 kHz, moderate Q (~1), gain a new parameter alongside the existing
   shelf parameters. Implement with the existing biquad kernels; do not
   add filter-design code if `kernels/biquad.rs` + existing design
   helpers cover a peaking EQ (check first; add the minimal design
   function if not).
2. Re-voice defaults by measurement + listening, candidate: shelf gain
   reduced toward unity, band gain carrying the cue. Config surface:
   extend the existing `height_*` config family
   (`height_directional_band_hz`, `height_directional_band_gain`)
   following the exact plumbing pattern of `height_crossover_hz`
   (config → manifest routing block in `routing/channel_router.py`'s
   `register_block` → CLI flag → engine constants).
3. All three consumers pick it up through the shared kernel:
   `StemRouter._height_send`, `MultichannelUpmixer` (via
   `utils.elevation_eq`), and the streaming engine. `HeightFilter` in
   `routing/channel_router.py` builds its own spectral mask — mirror the
   band there so STFT and time-domain height voicing agree (add a shared
   test comparing the two responses within tolerance).
4. Parity: height EQ parameters are engine constants; update endpoint,
   `engineParams.ts`, fixture, and re-hash
   `docs/contracts/preview_export_parity.md`.

## Tests

- Rust kernel: magnitude response has the band peak at the configured
  center/gain, existing rolloff/shelf behavior at defaults-without-band
  bit-identical to today (band gain 1.0 = today's output exactly —
  regression anchor).
- HeightFilter mask vs time-domain kernel response within 0.5 dB across
  third-octave bands.
- Full suites (Python, Rust, web) green; `npm run bench:engine`
  unchanged within noise (one extra biquad).

## Out of scope

- Per-listener HRTF personalization, frequency-dependent panning.
- Binaural renderer height handling (it has real HRTFs; untouched).

## Done when

- Blind-ish A/B (protocol
  `~/Projects/upmixer-knowledge/techniques/evaluation.md` §6) on two
  tracks: new voicing perceived as higher/more open without added
  harshness; if not clearly better, keep band gain default 1.0 (feature
  present, voicing unchanged) and record the result — that outcome still
  closes the phase.
- Numbers + listening note in the PR; parity contract re-hashed.
