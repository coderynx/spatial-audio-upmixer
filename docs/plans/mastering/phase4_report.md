# Phase 4 report — Chain head and pre-limiter soft clip

Plan: `docs/plans/mastering/phase4_chain_head_tail.md`.
Date: 2026-08-19. Suites: Rust **204 → 214 passed**, Python **1176 → 1182
passed / 44 deselected**, web **285 → 286 passed**.

## What shipped

Two stages, both optional and both off in every existing profile.

**Chain head** (`mastering::head`, `mastering/head.py`). One shared 2nd-order
Butterworth high-pass at a 10–30 Hz corner (default 20) on every non-LFE
channel, and a 1st-order pole-zero DC blocker at 5 Hz on LFE. Both are the
same `butter_sos` design call with a different order and corner, so there is
no new filter kernel; the LFE corner is structural and lives in
`DC_BLOCK_HZ`. It runs first, ahead of reference matching, so nothing
downstream matches, shapes or measures rumble. In the preview it is the first
thing `CausalChain::pre_compressor` does.

**Soft clip** (`mastering::clip`, `mastering/clip.py`). One memoryless curve
on every non-LFE channel: identity below a threshold `clip_db` under the
delivery ceiling, and above it a blend from a hard clip at the ceiling
(`knee` 0.0) to a tanh whose slope at the threshold is exactly 1.0 (`knee`
1.0, the default), so the full-knee curve has no corner and nothing leaves it
above the ceiling. It runs after loudness normalization and directly before
the limiter.

Manifest and config: `mastering.highpass` (`enabled`, `cutoff_hz`) and
`mastering.clip` (`enabled`, `clip_db`, `knee`), registered by each stage
module and bounded in `manifest/validate.py` (cutoff 10–30 Hz, clip 0–6 dB,
knee 0–1). Two `EffectPanel`s in `MasteringSection.tsx` — the head at the top
of the section, the clipper at the bottom, which is where each sits in the
chain. `SliderField` now passes `aria-label`, which it never did; its sliders
had no accessible name at all before.

Docs updated in the same phase: parity contract §1 (order, the two new
algorithm-identity rows, and the commutation note the clipper deliberately
breaks) and §2 (why neither stage adds a served constant);
`standards/loudness_dsp_bs1770.md` (two new sections — subsonic content
against the RLB stage's ~38 Hz corner, and what a clipper ahead of the limiter
does and does not change about the dBTP guarantee).

## Where the clipper lands in the preview, and why not at the emit point

The obvious place for a memoryless stage is next to the limiter in `render`,
right before `StreamingLimiter::process`. That would be wrong: the limiter
reads a whole look-ahead *past* what it emits, and those samples would still
be unclipped, so its forward-window minimum would react to peaks the offline
chain has already shaved. The clipper therefore runs in `fill_post`, as
samples enter the queue — every sample the limiter can see has been through
it, which is what `streaming_mastering_matches_the_offline_chain` now pins
with both stages in the fixture.

## What the two stages are worth

All figures from `unit_mastering_head_clip.rs`, on synthetic fixtures.

**Head, on a 440 Hz tone at 0.75 carrying 15 Hz at 0.5:**

| | limiter peak GR | limiter duty |
|---|---|---|
| rumble in | 3.03 dB | 0.94 |
| head stage first | 0.00 dB | 0.00 |

The audible band is untouched: 1 kHz comes through within 4e-3 of the
amplitude it went in at, while 15 Hz comes back at 12 dB/oct and 10 Hz on the
LFE channel survives at better than 0.85 of its input.

**Clipper, on a 24-hit transient train over a 220 Hz bed, `clip_db` 1.0,
knee 1.0, ceiling −1 dBTP:**

| | limiter duty | limiter peak GR | short-term | PSR |
|---|---|---|---|---|
| limiter alone | 0.073 | 1.36 dB | −15.66 LKFS | 14.56 dB |
| clip → limiter | 0.020 | 0.10 dB | −15.60 LKFS | 14.50 dB |

**The plan's PSR prediction does not hold, and should not have.** It asked for
"PSR improves ≥ the shave amount"; PSR moved −0.06 dB. The reason is
arithmetic rather than a defect: PSR is true peak minus short-term loudness,
both sides are pinned at the same ceiling by the limiter, and PSR is
scale-invariant — so it is already loudness-matched and cannot report "more
loudness at the same peak". What the clipper actually buys shows up in the
other three columns: the limiter's duty drops to 27% of what it was, its
deepest reduction falls to the 0.1 dB duty floor, and the delivered short-term
loudness rises because the limiter no longer has to give the body away to hold
the transients. The test asserts those three and the report says what PSR did.

## Aliasing — measured, and a real limit

No oversampling in v1. A 5.31 kHz sine driven into the knee is the worst case
a memoryless clipper has: every odd harmonic past Nyquist folds back, and on a
pure tone none of it is masked.

| drive over the knee | worst folded partial |
|---|---|
| +0.5 dB | −63.4 dBc |
| +1 dB | −45.4 dBc |
| +2 dB | −32.7 dBc |
| +3 dB | −30.0 dBc |
| +6 dB | −32.1 dBc |

At the depth the stage is for — a 0.5–1 dB shave, i.e. transients arriving a
fraction of a dB into the knee — folded content sits at or below −60 dBc. Past
about +2 dB of drive it is at −33 dBc on a tone, which is audible, and the
knee number cannot be raised freely on tonal HF material because of it. The
plan anticipated this and said to scope an oversampled variant as a follow-up
rather than grow this phase; that is the recommendation. The test asserts
−55 dBc at +0.5 dB and −28 dBc at +6 dB as regression guards on the measured
curve, not as claims of inaudibility.

## Validation

```
cd packages/dsp && cargo test -p upmixer-dsp-core     # 214 passed, 0 failed
uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q
                                                      # 1182 passed / 44 deselected
cd apps/web && npm run build:wasm && npm test && npm run build
                                                      # 286 passed, build ok
cd apps/web && npm run bench:engine
```

New coverage: `unit_mastering_head_clip.rs` (10 tests — DC removal on both
channel kinds, the audible band surviving, LFE keeping its sub content, the
limiter-headroom comparison above, curve identity below the knee, the ceiling
bound and odd symmetry at three knee settings, C¹ continuity at full knee,
LFE untouched, the transient-train comparison, and the aliasing sweep);
`stream_equivalence.rs`'s offline-vs-streaming test extended with both stages
in the fixture; `test_mastering_head_clip.py` (6 tests — chain wiring, the
contracted position, manifest block round-trip, both defaults off);
`engineParams.test.ts` (both stages absent unless asked for; the clipper takes
the limiter's ceiling and survives a collapse mode, which drops the limiter);
`MasteringSection.test.tsx` (both panels ship off and keep their settings
across the switch).

`clearing_the_limiter_through_update_params_actually_removes_it` needed the
clip block stripped from its fixture: it boosts a stem 24 dB and asserts the
peak is freed once the limiter goes, and a clipper with a ceiling of its own
holds that peak down. Removing the stage the test is not about is the honest
fix.

## Realtime budget

`npm run bench:engine` after `npm run build:wasm`, three runs each side,
against a HEAD build measured the same way (`git stash -u`, rebuild, bench,
restore). Both stages benched **on** (`head`, `clip`), per §4. Means, the
statistic this host can resolve:

| case | HEAD (3 runs) | this branch (3 runs) |
|---|---|---|
| binaural (order-3 decode) | 1.031 / 0.993 / 0.903 ms | 0.966 / 0.919 / 0.915 ms |
| transaural | 1.048 / 0.967 / 0.890 ms | 1.039 / 0.913 / 0.907 ms |
| native 7.1.4 + limiter | 0.816 / 0.814 / 0.769 ms | 0.803 / 0.815 / 0.784 ms |
| stereo downmix | 0.677 / 0.630 / 0.629 ms | 0.634 / 0.636 / 0.635 ms |

The branch is at or below HEAD on every row: a per-sample biquad and a
per-sample `tanh` on eleven channels cost less than the run-to-run spread,
against a chain already running an order-3 decode, a 1023-tap EQ convolution
and a 4x-oversampled limiter. Mean (≤ 0.4x) and p99 (≤ 1x) hold with margin —
branch binaural p99 was 2.47–2.59 ms against a 2.67 ms deadline, where HEAD
was 2.44–3.44 ms.

**The `worst` row is over budget on this host, on both sides.** HEAD's
binaural worst was 3.08x / 2.43x / 1.71x across the same three runs, and one
HEAD run put `native` at 4.02x; the branch's binaural worst was 1.56x / 1.72x
/ 1.95x. `worst` is a single-sample statistic and this machine is not quiet
enough to resolve it today — the branch is *better* than HEAD on it, which is
how you can tell it is noise rather than a regression. The two
`measuring (…)` failures are the pre-existing ones from phases 2 and 3,
unchanged.

## A/B listening note — owed, not done

**No listening pass was run: this was an agent session with no audio output.**
Phase 3 built the loudness-matched A/B precisely so notes like this one could
be trusted, and it is the honest place to say the pass has not happened.

What needs ears, specifically:

- **The clipper's knee, on real programme.** The measurements above say the
  limiter works less; they cannot say whether the harmonics that replaced its
  gain reduction sound better or worse. The pass to run is a dense programme
  at matched loudness with `clip_db` at 0.5 and 1.0 against off, listening to
  cymbals and vocal sibilance rather than level, with the aliasing table above
  in mind — the fixtures say HF tonal material is where it will show first.
- **The head stage's corner.** 20 Hz is the default; 10 vs 30 Hz on
  bass-heavy immersive content is a taste call the measurements cannot make.
  What the measurements do settle is that 20 Hz costs nothing audible on a
  1 kHz tone and buys back real limiter headroom.

## Notes

- Knowledge base (`~/Projects/upmixer-knowledge/techniques/
  mastering_restoration.md`) was consulted. It has no entry on subsonic
  filtering, DC removal or clipping — its mastering material stops at the
  stages already in the chain — so nothing in it conflicts with or informs the
  above.
- No new dependency, no new kernel: the head stage is `butter_sos` at two
  orders and the clipper is `tanh`.
- File sizes: `chain.py` 442 → 470 lines, `master.rs` 507 → 526, both well
  inside the ~600 hard cap. The four new source files are 35–72 lines each.
