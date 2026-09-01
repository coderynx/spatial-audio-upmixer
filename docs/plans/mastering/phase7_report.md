# Phase 7 report — Reference matching usability

Plan: `docs/plans/mastering/phase7_reference_match_usability.md`.
Date: 2026-08-19. Suites: Rust **231 → 235 passed**, Python **1215 → 1232
passed / 45 deselected**, web **292 → 298 passed**.

## What shipped

**Core (`packages/dsp`).**

- `match_reference::curve::realize_curve` + `RealizeParams` — the whole
  dB-domain realization in one function: strength scaling, Gaussian smoothing
  at the requested bandwidth, the range mask, the soft-knee max-correction
  clamp, and the ±2 dB sub-bass clamp, in that order. 30 lines on top of the
  primitives that were already there; `smooth_log_grid` and `soft_clamp` are
  reused, not re-written.
- `match_reference::curve::range_mask` — raised-cosine easing to unity gain
  outside `[low_hz, high_hz]`, spanning `mask_ease_oct` octaves either side on
  the *log* axis, so the ease is half an octave wherever the bound is put. A
  bound at or below zero leaves that side unmasked, which is how "no bound"
  reaches Rust without an `Option` on the wire.
- `correction_curve` lost its smoothing step and its decimation to 64
  breakpoints. It now returns the correction on the 1/24-octave log grid it
  already computed on — 240 points, raw. `CurveParams` lost `smooth_sigma_oct`
  and `n_breakpoints` with it.
- `dsp-py/src/reference.rs` — `upmixer_dsp.realize_curve(...)`.

**Python (`packages/core`).**

- `match_reference/curve.py`: `realize_curve()` wrapping the binding, and
  `build_curve_fir(curve, sr, n_taps, strength, max_db, smooth_octaves=None,
  low_hz=None, high_hz=None)`. The three new arguments are nullable —
  `None` means the served smoothing default and no mask at all, never a
  number the caller has to know.
- `SMOOTH_OCT_DEFAULT` (1/3), `SMOOTH_OCT_MIN` (1/12), `SMOOTH_OCT_MAX` (1),
  `_MASK_EASE_OCT` (1/2). The first three are served; the ease width is
  structural and stays here, like `routing::sends`'s `DIRECTIONAL_BAND_Q`.
- `ReferenceMatchProcessor` takes and forwards the three controls;
  `UpmixConfig` gains `mastering_match_ref_smooth_oct` / `_low_hz` /
  `_high_hz` (all `float | None`, default `None`); `MasteringChain` passes
  them through.
- Manifest: `mastering.match_reference.smooth_octaves` / `.low_hz` /
  `.high_hz`, bounded in `manifest/validate.py` (smoothing against the two
  served constants, the masks to 20 Hz–20 kHz).

**API and web.**

- `GET .../reference-match/{layout}/fir` takes `smooth_oct`, `low_hz`,
  `high_hz` beside `strength` and `max_db`, clamps each at the boundary, and
  hands them to the same `build_curve_fir` the bounce calls.
- `engine_constants()` serves `reference_match_smooth`
  (`default_oct`/`min_oct`/`max_oct`).
- `ReferenceMatchPanel.tsx` — three `NullablePotField`s (Smoothing, Match
  above, Match below) following the existing nullable-pot pattern, with the
  smoothing pot's default and range read from the served block and a
  `PRE_BOOTSTRAP_SMOOTH` literal standing in only until the configuration
  request lands, exactly as `PRE_BOOTSTRAP_DELIVERY` does for loudness.
- `withReferenceMatchParams` appends only the controls that are **set**. An
  unset control is absent from the URL rather than sent as a number: a value
  on the wire is an override, and the server's default has to stay the single
  source (the same rule the delivery-target pots follow).
- Stage-scoped matched audition: `monitorMastering`'s new `matchBypassed`
  strips the matcher alone — both `spectrum` and `rms` — and the engine
  measures that programme on its own key (`match-bypassed`) and applies the
  same compensating monitor scalar phase 3 built. `MatchBypassButton` sits
  beside the whole-chain one in the transport and renders only when the
  project has a reference-match asset for the selected layout.

Docs updated in the same phase: parity contract §2 (the served
`reference_match_smooth` block, the "unset controls stay off the URL" rule,
and the raw-curve statement) and §3 **P4** (extended to the stage-scoped
bypass); `web_ui_controls.md` (project header and loudness).

## The design decision the plan asked for, and what it cost

The plan's rule is that "the stored analysis curve stays raw so no knob forces
re-analysis". Taken literally, that moves smoothing out of `correction_curve`
and into `build_curve_fir` — which is what shipped, and it is the only design
in which a 1/12-octave setting means anything. Two consequences follow, and
both are load-bearing:

**The persisted curve is now 240 points, not 64.** A 64-point curve is one
sample every 0.158 octave; smoothing it at 1/12 octave (σ = 0.53 bins) is the
near-identity three-tap kernel the module docstring already records as a fixed
bug. Storing the raw grid instead keeps σ = 2 bins at the finest setting,
which is a real kernel. The FIR design cost is unchanged — 6.90 ms at 64
breakpoints, 6.99 ms at 240, on this host, because `minimum_phase` at 1023
taps dominates both. The stored asset JSON grows ~4×, which is a few tens of
kilobytes per layout.

**The two analysis tapers now run before smoothing rather than after.**
`confidence_taper` needs the reference's own power spectrum, which realization
does not carry, so it stays in `correction_curve` along with `band_edge_taper`
— and realization-time smoothing therefore lands after both. This is the one
real behaviour change in the default configuration, and it is measured below.
Moving the tapers into realization too would require persisting
`ref_power_db` alongside the curve and a compatibility branch for assets
written without it; that is not worth it for the size of the effect on real
programme material.

## What moved

Two measurements, because the answer depends entirely on what the reference
is.

**Broadband reference (what the stage is for).** Pink-ish noise beds and
reference, 10 s, 48 kHz, comparing the old order (smooth → norm → tapers →
decimate) against the new one (norm → tapers → smooth) on the same spectra:

| metric | value |
|---|---|
| max \|Δ\| across 20 Hz–20 kHz | **0.093 dB** |
| RMS Δ | **0.021 dB** |

**Line-spectrum reference (the golden fixture).**
`test_render_metrics_golden`'s bed and reference are each three pure sinusoids,
so the confidence taper is a near-binary gate on an almost-empty spectrum, and
gating-then-smoothing is nothing like smoothing-then-gating. The realized FIR
moves by up to **5.96 dB** (RMS **1.99 dB**) and the pinned bed metrics move
with it:

| pin | before | after |
|---|---|---|
| `_GOLDEN_REFMATCH_LKFS_HEX` | −11.0127 LKFS | −10.0574 LKFS |
| `_GOLDEN_REFMATCH_TP_HEX` | −12.1483 dBTP | −11.6369 dBTP |

The golden was regenerated (`REGENERATE_GOLDEN=1`) and the channel-RMS table
with it. The honest reading: **this is a fixture pinning the stage on material
it was never designed to see** — a reference master is broadband, and there the
change is 0.02 dB RMS. The mechanism, stated plainly so it is not rediscovered
later: with a line-spectrum reference the 1/3-octave smoothing now runs after
the confidence gate and spreads a tone's correction into bands the gate had
zeroed. `packages/dsp`'s `mr_curve` golden was regenerated for the same reason;
per `packages/dsp/AGENTS.md` it is a regression pin rather than an independent
reference now, so its regeneration proves nothing on its own — the
property-based unit tests below are what actually check the new code.

## What the controls do, measured

**Smoothing.** A reference with a +9 dB, ~1/6-octave resonance at 2.5 kHz
against a flat-tilt bed, realized at five bandwidths. "kept" is the peak
against the correction half an octave below it:

| smoothing | 2.5 kHz | 1.25 kHz | resonance kept |
|---|---|---|---|
| 1/12 oct | +7.37 dB | −0.69 dB | **+8.05 dB** |
| 1/6 oct | +5.74 dB | −0.63 dB | +6.36 dB |
| 1/3 oct (default) | +3.47 dB | −0.48 dB | +3.95 dB |
| 1/2 oct | +2.31 dB | −0.11 dB | +2.42 dB |
| 1 oct | +0.92 dB | +0.33 dB | **+0.60 dB** |

That is the whole control in one table: 1/12 chases the reference's exact
resonances, 1 octave matches only tonal balance, and the shipped default sits
where it always did.

**Range masks.** A warm bed (−4.5 dB/oct) matched to a bright reference
(−2.0 dB/oct), strength 1.0, max 6 dB:

| realized FIR | 40 Hz | 80 Hz | 150 Hz | 300 Hz | 1 kHz | 6 kHz | 12 kHz |
|---|---|---|---|---|---|---|---|
| full range | −2.00 | −2.30 | −4.64 | −4.32 | −0.07 | +5.68 | +5.97 |
| `low_hz = 300` | **−0.00** | **+0.00** | **−0.00** | −3.77 | −0.07 | +5.68 | +5.97 |

The canonical use case, exactly as the plan describes it: the top-end match is
untouched (+5.68 / +5.97 dB either way) and the mix keeps its own low end
instead of losing 2.3 dB at 80 Hz and 4.6 dB at 150 Hz. The 300 Hz cell is
mid-ease — the raised cosine reaches unity gain at 212 Hz (half an octave
below the bound) and full correction at 300 Hz, so the mask never puts a
corner in the FIR.

## Unchanged, and deliberately so

Restated because the plan asks for it, and because both are easy to break from
here:

- **The LFE exemption stands.** LFE is excluded from the spectral stage's
  analysis and from its correction — a channel band-limited to 120 Hz by
  BS.775-4 has no meaningful ratio against a full-range reference above that
  frequency — and still takes the level-matching scalar so bed/LFE balance
  moves with the mix. Nothing in this phase touches `processor.py`'s
  `lfe_key` handling. A `low_hz` mask restricts the *shared* curve; it does
  not give LFE one.
- **One shared curve, never per channel.** Every non-LFE channel gets the same
  minimum-phase FIR, because per-channel curves would desynchronize the
  inter-channel phase that BS.775 fold-down and transaural crosstalk
  cancellation depend on (parity contract §1). The three new controls act on
  the curve, once, before it is designed — so a mask or a smoothing change
  cannot make one channel's filter differ from another's by construction, not
  by convention. `test_downmix_commutes_with_matching` still pins it.

## Validation

```
cd packages/dsp && cargo test                      # 235 passed, 0 failed
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
                                                   # 1232 passed / 45 deselected
                                                   # (baseline 1215 / 45)
cd apps/web && npm run build:wasm && npm test && npm run build
                                                   # 298 passed, build ok
cd apps/web && npm run bench:engine
```

New coverage:

- `unit_match_reference.rs::range_mask_is_unity_inside_and_eases_to_zero_outside`
  — unity inside the range, exactly zero a full ease beyond it, and the
  raised cosine at exactly 0.5 at the ramp's mid-point.
- `unit_match_reference.rs::a_masked_curve_is_flat_outside_the_range` — the
  whole realization, not just the mask: every grid point below the ease is
  exactly 0.0 dB and every point above the bound carries the full curve.
- `unit_match_reference.rs::smoothing_bandwidth_decides_whether_a_notch_survives`
  — a one-grid-point −12 dB notch, kept below −2 dB at 1/12 octave and above
  −0.3 dB at 1 octave. This is the plan's smoothing fixture, stated as a
  property rather than a stored vector.
- `golden_match_reference.rs::curve_realization_matches_python` over three new
  fixtures (`mr_realize_fine`, `mr_realize_coarse`, `mr_realize_mask`) to
  1e-11.
- `test_project_storage.py::test_the_served_fir_is_the_export_path_fir_for_the_same_curve_and_knobs`
  — **the parity bit-compare the plan asked for**, over four knob sets: the
  bytes the FIR endpoint serves the preview against `build_curve_fir`'s own
  output for the same stored curve, compared as raw float32 rather than
  `allclose`. It passes trivially, and that is the point: the two sides are
  one function called twice, not two implementations held to a tolerance.
- `test_match_reference_controls.py` (12 tests) — the smoothing and mask
  behaviour through `realize_curve` and through a designed FIR, that unset
  controls equal the full-range/default realization *bit for bit*, that the
  masks ease monotonically rather than stepping, that the persisted curve is
  identical for two processors with completely different controls, plus the
  manifest round-trip and four out-of-range rejections.
- `test_match_reference.py::test_breakpoints_are_the_log_grid` replaces
  `test_breakpoints_count`'s `== 64`.
- `audioEngine.test.ts` — `withReferenceMatchParams` leaving unset controls
  off the URL and appending only the set ones, and `monitorMastering`'s four
  cases (nothing bypassed, whole chain, stage only, and the whole chain
  winning over the stage).

### Wasm and the realtime budget

`npm run build:wasm` produces a **byte-identical** artifact
(`fa4b2ef449a91aa7bc45ac47aa7e73ef` before and after). Nothing in this phase
is reachable from the wasm crate: the preview does not realize the curve at
all — it fetches a FIR the server realized, which is the parity mechanism (see
the bit-compare above) and a stronger one than a second implementation would
be. A wasm export of `realize_curve` would be dead code, and
`packages/core/AGENTS.md`'s rule against unreferenced modules applies, so the
plan's "PyO3/wasm" deliverable shipped as PyO3 only. This is the one deviation
from the plan.

`npm run bench:engine` was run anyway, since the plan asks for numbers:

| case | mean | p99 | worst |
|---|---|---|---|
| binaural (order-3 decode) | 0.959 ms (0.36x) | 2.685 ms (1.01x) | 4.092 ms (1.53x) |
| transaural | 0.962 ms (0.36x) | 2.667 ms (1.00x) | 3.922 ms (1.47x) |
| native 7.1.4 + limiter | 0.830 ms (0.31x) | 2.453 ms (0.92x) | 3.483 ms (1.31x) |
| stereo downmix | 0.670 ms (0.25x) | 2.267 ms (0.85x) | 3.281 ms (1.23x) |
| mix edit (playing) | 0.052 ms (0.02x) | 0.112 ms (0.04x) | 0.336 ms (0.13x) |
| measuring (exact, paused) | 2.117 ms (0.79x) | 3.001 ms (1.13x) | 3.421 ms (1.28x) |
| measuring (fast excerpt, playing) | 1.729 ms (0.65x) | 5.494 ms (2.06x) | 8.102 ms (3.04x) |

The binaural/transaural rows sit **marginally over** the p99 budget (1.01x,
1.00x) and the two `measuring` rows FAIL — but the artifact is byte-identical
to HEAD's, so these are this host's numbers under load, not this phase's. The
two measuring rows are the same pre-existing FAILs phase 3 reported. Nothing
in this phase runs on the audio thread; the realization runs once per knob
change, on the server, in ~7 ms.

## A/B listening note — owed, not done

**No listening pass was run: this was an agent session with no audio output.**

The canonical case the plan names is the one measured above and the one to
listen to: a warm mix matched against a bright reference, toggling `low_hz`
between unset and 300 Hz. The numbers say the low end is returned exactly
(0.00 dB at 40/80/150 Hz) while the +5.7 dB top-end match is untouched; what
needs ears is whether the result still reads as "the reference's tonality"
once the bottom two octaves are the mix's own, or as two records spliced at
300 Hz. The ease width (half an octave) is the knob to question if it is the
latter.

Two more that only ears can settle:

- **1/12 versus 1/3 octave on a real reference.** The table above says 1/12
  keeps 8 dB of a resonance the default reduces to 4 dB. Whether chasing a
  reference's own resonances sounds like a closer match or like ringing is
  exactly the judgement the control exists to leave with the user, and why
  1/3 remains the default.
- **The stage-scoped A/B.** It is now level-matched by construction (the same
  measurement machinery as phase 3, both stages of the matcher stripped on the
  bypassed side). What needs checking is that pressing it reads as a tonal
  comparison rather than a level one on real programme material — the same
  open question phase 3 left, now scoped to one stage.

## Notes

- Knowledge base (`~/Projects/upmixer-knowledge/techniques/
  mastering_restoration.md`) was consulted. It carries exactly one line on
  this stage — "Reference matching / sound cloner … Already implemented" —
  and nothing at all on smoothing bandwidth, range masks, or matched
  auditioning, so nothing in it conflicts with or informed the above. Its
  chain-order line ("Match/EQ before compression") is unchanged by this phase.
- No new Python, JS or Rust dependency.
- File sizes: `curve.rs` 200 → 250, `dsp-py/reference.rs` 192 → 224,
  `curve.py` 156 → 186, `processor.py` 240 → 258. Two files needed splitting
  along the way: `MasteringSection.tsx` (599 → 644 → **507**, with the
  reference panel lifted into a new `ReferenceMatchPanel.tsx` at 184, the
  same seam `DynamicEqPanel.tsx` already uses) and `test_match_reference.py`
  (543 → 652 → **545**, with the new control tests in
  `test_match_reference_controls.py` at 131).
  `audioEngine.ts` is 677, over the hard cap — it was already 661 at HEAD
  (phase 3 split its calibration state machine out and left it there); this
  phase added 16 lines and did not fix it.
