# Phase 15 — Per-stem primary/ambient split

Read `docs/plans/mixing/README.md` first for context and ground rules.
Shipped 2026-08-23. Independent of phases 12–14.

## Why

A separated stem is not a dry signal. Separation splits a finished stereo
master, so every stem carries the room, plate and delay its source was mixed
with. Before this phase the only signal a surround or height send could carry
was the whole stem, which put reverb in front of the listener along with the
instrument that caused it, and made "more surrounds" mean "more direct sound
in the surrounds".

An earlier attempt to separate reverb with a model was rejected: slow, and it
made the mix worse. This is the DSP answer.

## What ships

`packages/dsp/crates/dsp-core/src/routing/ambient.rs` — `AmbientSplit`:

- inter-channel coherence mask, Avendaño & Jot, *Frequency Domain Techniques
  for Stereo to Multichannel Upmix* (AES 22nd). Smoothed cross- and
  auto-spectra per bin, ambience index `Φ = 1 − coherence`, `tanh` mapping
  with a floor (the floor is not optional — at zero it is spectral
  subtraction, with the musical noise that implies);
- an equal-energy guard multiplying the mask, because a hard-panned primary
  reads as incoherent for the same reason ambience does. Without it, a
  hard-panned tone sends its full power to the surrounds; with it, under
  1e-3 of it;
- a first-order power-complementary tilt at `AMBIENT_TILT_HZ = 2000`, splitting
  the ambient half between the rear and height sends. Heights take the bright
  half because elevation perception keys on the 6–9 kHz spectral cues
  (Blauert's directional bands) that `routing::sends::elevation_eq` already
  voices for.

Two per-stem amounts drive it: `mixing.stem_ambient_rear` and
`mixing.stem_ambient_height`, 0..1, keyed like `stem_rebalance`. They reach
every speaker of a class at `1/sqrt(n)`, independent of the stem's placement —
the point is to put ambience around the listener whatever the instrument is
doing in front — and what they take is subtracted from the dry pair first, so
the sends are a move rather than a copy.

## Decisions worth keeping

**No latency.** The split reads *ahead* of the block it serves rather than
delaying its output. Stems are resident in memory on both sides, and frames
are scheduled at absolute sample positions, which is also what keeps the
output independent of the block size it was rendered in
(`the_split_is_independent_of_the_block_size`).

**The stem EQ runs ahead too.** The export EQs a stem before it routes it, so
the split has to read the EQ'd signal — but a streaming convolver cannot be
read ahead of the block it is filling. `StemRouteState` now runs the stem EQ
into a small carried buffer ahead of the render position, which both the dry
path and the split read. This is the parity mechanism, not an optimization.

**The ambient sends carry no velvet decorrelator.** Their two sides are
already independent signals — that is what the split selected them for — so a
velvet pair would only smear them, and it measured as the largest single
per-sample cost in the stage.

**Route normalization matches the unsplit stem.** Measured on the split pair
instead, a stem gets quieter as its sends come up, since the sends are inside
the routed sum. Pinned by
`test_an_ambient_send_keeps_the_stem_at_its_own_loudness`.

**Smoothing is 0.1 per frame (~53 ms).** An independent pair passes 0.76 of
its power at that setting and 0.88 at half of it, but a slower estimate holds
the ambient gain open across a note boundary.

## Cost

Bench worst case (`ambientNative`: 9 stems, both sends at 0.8, full mastering
chain, 7.1.4): mean 0.32x, **p99 0.92x**, worst 1.32x of the 2.67 ms quantum
deadline, against 0.27x / 0.75x / 1.22x for the same case with the stage off.

Getting there needed three passes, and the first guess was wrong: FFT size
does not drive the cost. 256, 512 and 1024 all measured the same p99, so the
stage keeps 1024. What did move it:

| Change | p99 |
|---|---|
| First working version | 1.06x |
| Velvet dropped from the ambient sends | 1.00x |
| `tanh` tabulated, transforms on carried scratch, ring masked not divided | 0.92x |

## Measured output

A 6 s reverb-heavy stem routed to 7.1.4, `StemRouter` only:

| Sends | LUFS | dBTP | front | surround | height |
|---|---|---|---|---|---|
| off | −13.23 | −4.98 | 91.8% | 2.9% | 5.3% |
| rear 0.8 | −13.20 | −5.03 | 90.4% | 4.1% | 5.5% |
| rear + height 0.8 | −13.21 | −4.09 | 81.8% | 3.1% | 15.1% |

Loudness holds while energy leaves the front. The rear/height balance depends
on the tail's spectrum: this fixture's tail is white, so the tilt sends most
of it overhead. A real, darker tail lands further rearward.

## Known limits

- **A near-mono stem has almost no ambient half to send.** Coherence cannot
  tell one from its own dry signal, so its sliders are close to inert. Stated
  as a test (`test_a_mono_stem_has_almost_no_ambient_half_to_send`), not left
  to be discovered. The honest upgrade is a single-channel decay-model
  estimator (Lebart + a decision-directed gain), which is its own project.
- **No listening pass yet.** The defaults (`AMBIENCE_FLOOR` 0.1, threshold
  0.5, slope 4.0, tilt 2 kHz) are the paper's values and one crossover choice,
  not tuned by ear. That is the next thing this stage needs.
