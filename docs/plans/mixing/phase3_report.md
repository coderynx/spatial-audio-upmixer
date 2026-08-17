# Phase 3 report — velvet sends wired into every mixing path (2026-08-17)

Plan: `docs/plans/mixing/phase3_send_integration_parity.md`. Baselines it is
judged against: `phase0_report.md` §1a, §1b, §1c, §1d, §4; `phase2_report.md`
§2.

Shipped: the velvet pair now runs every surround and height send in
`StemRouter.route`, every derived channel in `MultichannelUpmixer`, and the
streaming engine's four per-stem sends. `diffuse_send` and `haas_decorrelate`
are deleted — Rust kernels, PyO3 bindings, Python wrappers and constants —
and so are `SURROUND_HAAS_DELAY_MS_*`, `HEIGHT_HAAS_DELAY_MS_*` and
`DIFFUSE_SEND_BLEND`. Nothing references them anywhere in the five packages.

## 1. What replaced what

| Path | Before | After |
|---|---|---|
| `StemRouter.route` surround | `diffuse_send` at 31 / 37 ms | velvet pair, seed `VELVET_SEED` |
| `StemRouter.route` height | `diffuse_send` at 23 / 29 ms | velvet pair, seed `VELVET_SEED_HEIGHT` |
| `MultichannelUpmixer` SL/SR | `diffuse_send`, R also delayed 23 ms | surround pair, one side each |
| `MultichannelUpmixer` BL/BR | `diffuse_send`, R also delayed 19 ms | surround pair, one side each |
| `MultichannelUpmixer` TFL/TFR | TFL dry, TFR delayed 17 ms | height pair, one side each |
| `MultichannelUpmixer` TBL/TBR | TBL dry, TBR delayed 13 ms | height pair, one side each |
| `stream::routing::Send` | `DelayLine` + dry/wet blend | `VelvetLine`, block-processed |

The one-sided-delay bias of phase 0 finding 2 is gone by construction: no
derived channel is a plain copy of its source any more, and the two sides of
every pair are drawn from the same tap set, so neither leads.

## 2. The second seed

The plan asks for distinct seeds per zone class. Reusing `VELVET_SEED` for
both would leave a stem's surround and height sends **0.98 correlated** —
the same signal arriving from two directions, which images as one hard
phantom rather than as diffusion. Measured on a routed stem (`Other`, SL and
TFL only):

| seeds | surround/height correlation |
|---|---|
| one (`VELVET_SEED` for both) | +0.9803 |
| two (`VELVET_SEED_HEIGHT` for height) | **+0.0046** |

`VELVET_SEED_HEIGHT = 18861` comes from the same 400k-seed search phase 2
used, re-run with the same 16384-point third-octave score (it reproduces
2.472 dB for seed 260797, so it is the same scorer). The flattest candidate
was not chosen:

| seed | worst third-octave (dB) | tap inner product vs the surround pair |
|---|---|---|
| 278162 | **2.528** | 0.1201 |
| 143459 | 2.541 | 0.1249 |
| 370590 | 2.631 | 0.0732 |
| **18861** | 2.678 | **0.0000** |
| 112086 | 2.690 | 0.0015 |

18861's taps never land on a surround tap, so the two zone classes are
exactly orthogonal at zero lag. That is worth 0.15 dB of third-octave
flatness, which sits inside the 5.5 dB per-bin Rayleigh floor phase 2 §3
established as irreducible anyway.

## 3. Measured, phase 0's kit re-run unchanged

`uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s`, 1.9 s.
§1b's columns changed with the defect: notch *spacing* is meaningless for an
aperiodic filter, so it is replaced by the dip count and the broadband gain,
which are the two numbers phase 2 §3 showed actually separate a comb from a
sparse FIR.

### 3a. The decorrelator alone (phase 0 §1b → new §1b)

| Chain | worst notch (dB) | dips < −10 dB | broadband gain (dB) |
|---|---|---|---|
| surround L, **before** | −20.00, every 32.3 Hz | 490 | −2.97 |
| surround R, **before** | −20.00, every 27.0 Hz | 585 | −2.97 |
| height L, **before** | −20.00, every 43.5 Hz | ~490 | −2.97 |
| height R, **before** | −20.00, every 34.5 Hz | ~585 | −2.97 |
| surround L, after | −48.44 @ 11040 Hz | **77** | **+0.00** |
| surround R, after | −44.60 @ 8430 Hz | **72** | **+0.01** |
| height L, after | −44.79 @ 4868 Hz | **69** | **−0.00** |
| height R, after | −52.56 @ 4360 Hz | **87** | **+0.00** |

The dips are deeper and roughly 7x rarer, and — the point — aperiodic, so no
pitch is attached to them. The −2.97 dB the blend took off every send is
gone.

### 3b. Mono fold-down of each pair (phase 0 §1d)

| Pair | sum vs power sum, before → after (dB) | worst notch rel., before → after (dB) |
|---|---|---|
| StemRouter surround | +1.46 → **+0.01** | −31.97 → **−48.04** |
| StemRouter height | +1.46 → **+0.01** | −40.98 → **−55.34** |
| Multichannel SL+SR | +0.00 → +0.00 | **−238.58 → −48.29** |
| Multichannel BL+BR | +0.00 → +0.04 | **−234.58 → −55.98** |
| Multichannel TFL+TFR | −0.00 → +0.04 | −41.37 → −45.46 |
| Multichannel TBL+TBR | −0.00 → +0.15 | −55.00 → −50.50 |

Phase 0's worst result is fixed: `MultichannelUpmixer`'s complete periodic
nulls (−238 dB every 43.5 Hz on SL+SR, −234 dB every 52.6 Hz on BL+BR) are
gone, replaced by the pair's ordinary aperiodic floor. Per-bin ripple σ on
those two pairs fell from 10.86 / 15.21 dB to 5.47 / 7.40 dB — the residual
is the Rayleigh floor, not structure. The StemRouter pairs no longer *build
up* on fold-down either (+1.46 → +0.01 dB).

### 3c. Derived channels (phase 0 §1c)

| Channel | broadband gain, before → after (dB) |
|---|---|
| SL / SR | −7.40 / −7.40 → **−4.44 / −4.44** |
| BL / BR | −13.86 / −13.86 → **−6.80 / −7.32** |
| TFL / TFR | −6.85 / −8.01 → **−7.90 / −7.87** |
| TBL / TBR | −13.77 / −15.35 → **−11.95 / −12.43** |

The lateral bias phase 0 §1c measured is closed: TFR was **1.16 dB** below
TFL and TBR **1.58 dB** below TBL because only the right side carried a
delay; they are now within **0.03 dB** and **0.48 dB**. The derived surrounds
and backs also stopped losing the blend's 2.97 dB per cascade stage (BL/BR
gain +7 dB, i.e. two stages of it).

The band-ripple column moved the other way — 6.89 → 11.09 dB on SL, 11.02 →
22.50 dB on BL — and that is real, not an artefact: below ~200 Hz a 30 ms
sparse FIR has too few taps per wavelength to average flat, where a delay
blend is smoothly rippled everywhere. It lands where both send pre-filters
already attenuate (HP250 on surround, the elevation EQ's 0.15 sub-150 Hz
gain on height), and it cancels on the pair sum, which is why §3b's fold-down
is flat. Judged over 200 Hz–16 kHz, where the sends actually carry content,
the pair is within phase 2 §2's ±2.5 dB.

### 3d. Zone energy accounting (phase 0 §4)

Phase 0's re-scope verdict predicted this table would move, and it did: a
flat send replaces a −2.97 dB one, so `route`'s renormalization hands the
sends more of each stem at identical weights.

| Preset | front, before → after | surround | height |
|---|---|---|---|
| balanced | 0.907 → 0.882 | 0.046 → 0.049 | 0.047 → 0.069 |
| intimate | 0.926 → 0.915 | 0.060 → 0.062 | 0.015 → 0.023 |
| stage | 0.893 → 0.868 | 0.046 → 0.049 | 0.061 → 0.084 |
| wide | 0.826 → 0.785 | 0.043 → 0.046 | 0.130 → 0.169 |
| immersive | 0.756 → 0.692 | 0.025 → 0.028 | 0.218 → 0.280 |
| live | 0.867 → 0.828 | 0.037 → 0.041 | 0.095 → 0.131 |

Every stem still renormalizes to exactly 1.0000 across the non-LFE channels
(asserted in the kit and, unmarked, by
`test_stem_router.py::test_main_bed_routing_is_constant_power`), so total
routed energy per stem is unchanged — the plan's out-of-scope constraint
holds. What changed is its distribution: the mix is genuinely more immersive
at the same preset numbers, ~2.5 points of front energy on `balanced` and
6.4 on `immersive`.

**This enlarges phase 4's target.** Height content is what the BS.775
downmix drops, so the mean downmix loss rose from 0.23 to 0.34 dB on
`balanced` and from 6.17 to **8.56 dB** for `wide`/Crash. Phase 0 shrank
phase 4 on the strength of the old numbers; these are the ones it should be
re-judged on.

### 3e. Untouched

LFE (§3) is unchanged to 0.1 dB — the sends never fed it. §2a's two-stereo-
image mismatch persists (Crowd −0.51 → −2.27 dB offset, still 5 dB of
per-bin ripple): that is phase 4's, not this phase's.

## 4. Parity

The kernel lands once in `dsp-core` and both bindings call it, so there is no
second implementation to drift. What the phase adds on top:

- `stream/routing.rs` gained two tests that run a signal through the
  streaming sends in **ragged blocks** (333 / 999 / 128) and compare against
  `VelvetFir::process` offline at 1e-12 — the export path's own function.
  `stream_equivalence.rs`'s `routing_output_is_independent_of_block_size` and
  `full_chain_output_is_independent_of_block_size` still pass unchanged,
  which is what pins the block refactor in §5.
- `decorrelate.rs`'s streaming test now also covers block sizes that do not
  divide the internal chunk.
- Phase 2's golden tap-table pin (`golden_kernels.rs` ↔
  `test_velvet_decorrelator.py`) is untouched and still passes, so the wasm
  and PyO3 builds still produce the same taps.
- No whole-bed null harness was added. The only thing this phase changed
  below the bed sum is the send operator, and it is pinned sample-for-sample
  against the offline function on both sides; the gain/renormalization
  arithmetic around it is unchanged code with unchanged tests.

**Ledger D33** records the real parity find (§5).

### Deviation: the velvet constants are not served over the wire

The plan asks for the new constants (seeds, length, taps, mix) to be
published through `engine_constants()` → `engineParams.ts` →
`engineConstants.fixture.ts` in place of the retired ones. They are not, and
the three retired keys are simply gone from the endpoint. The tap set is
structural, not tunable — `packages/dsp/AGENTS.md`'s own carve-out for the
BS.1770 true-peak FIR and the ambisonic normalization — and the seeds *are*
the filters: the fold-down property both pairs are built on holds only while
both sides come from one draw, so a wire round-trip could add a way to break
it and no way to use it. `packages/core` reads the same values back through
the binding (`upmixer.utils.SURROUND_VELVET_SEED` / `HEIGHT_VELVET_SEED`),
so neither side keeps a copy. `preview_export_parity.md` §2 is updated to say
so.

## 5. Realtime budget — and a stale artifact (D33)

`npm run build:wasm` is required after any `packages/dsp` change, and doing
it exposed that **the committed artifact was two commits stale**: last built
at `8da41d5`, while `4548970` (mid-bass decorrelation) changed `dsp-core`
after it. The preview has been running an engine without that stage. That is
fixed here — the rebuilt artifact is in this phase's commit — but it re-opens
the §4 budget, because that stage had never been benched from a current
build.

Attribution, all four numbers from **freshly built** wasm on the same
machine, so the comparison is like-for-like:

| build | mean | p99 | worst | verdict |
|---|---|---|---|---|
| committed (stale) artifact, old sends | 0.815 ms (0.31x) | 2.325 ms (0.87x) | 2.549 ms (0.96x) | ok |
| HEAD source rebuilt, old sends | 1.957 ms (0.73x) | 7.308 ms (2.74x) | 7.628 ms (2.86x) | FAIL |
| this phase, velvet bypassed | 1.908 ms (0.72x) | 7.098 ms (2.66x) | 8.491 ms (3.18x) | FAIL |
| this phase, velvet active | 1.923 ms (0.72x) | 7.023 ms (2.63x) | 7.452 ms (2.79x) | FAIL |
| this phase, velvet active, `decorrelate: 0` | **0.794 ms (0.30x)** | **2.189 ms (0.82x)** | 2.820 ms (1.06x) | ok |

Read the last two rows together: **the velvet sends cost nothing measurable**
(0.794 ms with them active against the stale artifact's 0.815 ms without
them, on nine stems × four sends), and the whole overrun belongs to mid-bass
decorrelation, which costs ~1.1 ms per quantum on its own. Phase 3's own
budget impact is inside the noise of the measurement.

Two implementation choices kept it there:

- `VelvetLine` filters a **whole block per tap** rather than a tap loop per
  sample, so each tap is a sequential slice add over the ring rather than 128
  masked random reads. (Measured on its own this was worth little in wasm —
  1.85 → 1.92 ms, i.e. nothing — but it is the shape the offline kernel
  already had, and it is what makes the send cost scale with taps rather than
  with taps × bounds checks.)
- A send **no speaker draws from is skipped**, exactly as `route`'s
  `needs_surround` / `needs_height` guards do offline; it reads back as the
  dry signal. Its filters then start cold if a later mix edit routes the stem
  there — the same cold start the offline path takes on every render.

The bench's `measuring (fast excerpt, playing)` case already failed at HEAD
with the stale artifact, so it is not new either.

**The budget failure is not this phase's to fix**, but it is real and it
blocks trusting the preview: at p99 2.6x the callback starves and the node
emits silence. It needs the treatment D25/D26 got — see D33.

## 6. Validation

- `cd packages/dsp && cargo test` → 119 lib + 45 integration/golden tests
  pass. Net −2 lib tests: the two `sends.rs` tests for the deleted kernels
  and the `DelayLine` test are gone, one new streaming test
  (`surround_and_height_sends_use_different_tap_sets`) is added, and the two
  send-parity tests were rewritten rather than added.
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` →
  **1092 passed, 31 deselected** (phase 2 left 1090; +2 from the new
  fold-down and zone-decorrelation tests in `test_stem_router.py`).
- `cd apps/web && npm test` → 249 passed; `npm run build` → clean.
- `npm run build:wasm` → rebuilt and committed (§5).
- `npm run bench:engine` → §5.
- Measurement kit re-run → §3.

## 7. Not done: the A/B listening note

The plan requires a listening pass (dense rock/pop and sparse acoustic,
7.1.4 + its stereo downmix, old vs new sends) checking for loss of the
hollow/phasey surround character, unchanged front imaging, and no new
artefacts on separation bleed in the surrounds. **I cannot listen**, so this
is outstanding rather than done. What the measurements can and cannot stand
in for:

- The hollow/phasey character has an objective correlate and it moved:
  §3a's 490–585 periodic −20 dB notches became ~75 aperiodic ones, and
  §3b's fold-down nulls are gone. That is the mechanism the complaint names.
- Front imaging is untouched by construction — FL/FR/C take the dry stem;
  only the send operator changed. But §3d says the *balance* moved: sends now
  carry 2.5–6.4 points more energy at identical preset weights, so the mix
  will read as more immersive. Whether that is an improvement or wants a
  preset re-scale is a listening call, and it is the one thing here most
  likely to need one.
- Bleed behaviour is unchanged in kind: the sends still run post-separation,
  so artefacts stay in their source channel. The velvet pair spreads each
  artefact over 30 ms of sparse taps instead of one delayed copy, which
  should smear rather than double it — untested by ear.

Suggested first listen: `immersive` on a dense track, since that is where
§3d moved most (front 0.756 → 0.692).
