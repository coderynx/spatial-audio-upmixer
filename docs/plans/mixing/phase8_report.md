# Phase 8 report — D33 closed, measurement kit re-baselined

Nothing audible ships here. The export path is byte-for-byte the code it was;
the preview computes the same band splits it already computed, on a schedule
that fits the audio thread.

## 1. What D33 actually was

`bench:engine` failed every render case with `decorrelate: 1`:

```
FAIL binaural (order-3 decode)  mean 1.950ms (0.73x)  p99 7.158ms (2.68x)  worst 7.917ms (2.97x)
FAIL transaural                 mean 1.825ms (0.68x)  p99 7.101ms (2.66x)  worst 7.392ms (2.77x)
FAIL native 7.1.4 + limiter     mean 1.709ms (0.64x)  p99 6.978ms (2.62x)  worst 7.189ms (2.70x)
FAIL stereo downmix             mean 1.659ms (0.62x)  p99 6.939ms (2.60x)  worst 7.956ms (2.98x)
FAIL measuring (exact, paused)  mean 4.779ms (1.79x)  p99 7.103ms (2.66x)  worst 7.456ms (2.80x)
FAIL measuring (fast excerpt, playing) mean 2.864ms (1.07x)  p99 14.565ms (5.46x)  worst 15.285ms (5.73x)
```

The cost was the zero-phase band split, and it was the same defect D25 and
D26 record: redundant recomputation, not expensive maths. `HorizonFiltFilt`
ran a whole `sosfiltfilt` over `[start − 300 ms, end + 300 ms]` to emit each
512-frame stride — 29,312 samples filtered twice, per channel, for 512
samples of output, on all 11 non-LFE channels. 57× redundant.

Attribution was measured, not assumed: with `decorrelate: 0` the identical
build benched p99 0.88x (binaural), so the stage owned ~4.8 ms of the 7.2 ms
heavy quantum. The LF unifier's own pass owned ~1.1 ms of the rest.

## 2. The fix

`stream::band::RollingBand` replaces `HorizonFiltFilt` for both stages.

`filtfilt(x) = reverse(filt(reverse(filt(x))))`. The inner forward pass is
causal, so it does not need recomputing at all — it now carries its state and
reads every sample exactly once, entering the signal through the same odd
extension and `sosfilt_zi` seeding `sosfiltfilt` uses, and running out through
the trailing pad at the end of the programme. Only the outer backward pass is
anticausal, and only *it* needs a warm-up.

That warm-up is then paid gradually. The backward pass produces one `chunk`
of band at a time, walking down from `chunk + ahead` with the filter seeded at
its step state; the walk is sliced across the render calls that consume the
*previous* chunk, at a rate of `⌈(chunk + ahead) / chunk⌉ + 1` samples per
frame of output. No single quantum carries a whole warm-up. (The `+ 1` is
load-bearing: paced at exactly the chunk's own rate the last slice lands in
the call that needs it, and rounding turns into a synchronous catch-up right
there — worst went 2.2x → 1.1x once it was added.)

The engine buffers `2 × chunk + ahead` of look-ahead for this, since the next
chunk's warm-up must exist while the current one is still being consumed.

Two knock-on changes fell out:

- `fill_post` cloned the entire live `pre` queue every stride, because
  `LfUnifier::process` mutated its input in place. It now reads `pre` and
  writes the output window, and the clone is gone — ~1.4 MB of copying per
  stride at the look-ahead the decorrelator needs.
- `DECORR_HORIZON_MS` drops 300 → 200. The 300 ms was sized when *both*
  passes were truncated; with the forward pass exact, only the backward
  warm-up decays. Measured against the offline pass on the 100–300 Hz
  band-pass:

  | warm-up | max abs error vs `sosfiltfilt` |
  |---|---|
  | 50 ms | 6.3e-4 |
  | 75 ms | 2.8e-5 |
  | 100 ms | 1.3e-6 |
  | 150 ms | 2.5e-9 |
  | **200 ms** | **5.2e-12** |
  | 300 ms | 3.2e-13 |

  200 ms sits three orders inside the tightest parity tolerance in
  `stream_equivalence.rs` (1e-8 block-size independence, 1e-6 versus
  offline) and keeps the engine's total look-ahead at 14,400 frames — the
  same figure it buffered before this phase, so cold start and seek cost
  what they did.

## 3. Result

```
deadline 2.67 ms per 128-frame quantum at 48000 Hz

ok   binaural (order-3 decode)  mean 0.750ms (0.28x)  p99 1.843ms (0.69x)  worst 3.208ms (1.20x)  cold 72.8ms
ok   transaural                 mean 0.749ms (0.28x)  p99 1.853ms (0.69x)  worst 3.236ms (1.21x)  cold 72.2ms
ok   native 7.1.4 + limiter     mean 0.622ms (0.23x)  p99 1.706ms (0.64x)  worst 3.168ms (1.19x)  cold 70.3ms
ok   stereo downmix             mean 0.507ms (0.19x)  p99 1.622ms (0.61x)  worst 2.853ms (1.07x)  cold 70.2ms
ok   measuring (exact, paused)  mean 1.593ms (0.60x)  p99 2.144ms (0.80x)  worst 2.860ms (1.07x)  cold 78.2ms
ok   measuring (fast excerpt, paused) mean 6.410ms (2.40x)  p99 7.162ms (2.69x)  worst 8.076ms (3.03x)  cold 93.5ms
ok   measuring (fast excerpt, playing) mean 1.266ms (0.47x)  p99 3.718ms (1.39x)  worst 4.703ms (1.76x)  cold 124.3ms
ok   mix edit (mute + compressor, playing) mean 0.046ms (0.02x)  p99 0.064ms (0.02x)  worst 0.103ms (0.04x)  cold 0.6ms

budget: mean <= 0.4x, p99 <= 1x, worst <= 1.5x of the deadline
```

Every case passes — including `measuring (exact, paused)` and `measuring
(fast excerpt, playing)`, which were over p99 on this machine even with the
decorrelation stage switched *off*. Both stages' band splits got the fix, so
the whole streaming chain gained, not just the stage D33 named. p99 on the
heaviest render case: 2.68x → 0.69x.

Ledger D33 is closed in `docs/contracts/preview_export_parity.md`, with D26
and D31 updated to point at the same mechanism. The wasm artifact is rebuilt
and committed in the same change, per §1's build-provenance rule.

## 4. Output impact

None on export: nothing outside `stream::` changed, and `stream::` is the
preview only. The preview's own band is *closer* to the offline one than it
was — the forward pass is now exact rather than truncated at 300 ms — so this
narrows preview/export divergence rather than trading it for speed. No A/B
listening note applies: there is no audible change to A/B (deliverable 3's
condition did not trigger; the optimization is the transparent kind it asks
for first).

## 5. Re-baseline

`docs/plans/mixing/phase8_baseline.md` — all four phase 0 measurements plus
everything the kit gained in phases 4-7 (1b/1c/1d decorrelator and centre
tables, 2b/2c downmix loss, 4b/4c zone and residual tables), run verbatim at
this head. Phases 9-11 cite that file.

## 6. Validation

- `npm run bench:engine` — green, exit 0, all eight cases; numbers above are
  the last of four runs, which agreed to within noise.
- `cargo test -p upmixer-dsp-core` — 173 tests green, including
  `stream_equivalence.rs` (streaming vs offline at 1e-6, block-size
  independence at 1e-8) and the golden kernel suites.
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q` —
  **1110 passed, 31 deselected**. The plan's stated baseline of 1107 was
  already stale before this phase; phase 8 adds no Python tests and changes
  no Python code, so the delta is pre-existing drift, not a regression.
- `npm test` (249 tests, 31 files) and `npm run build` in `apps/web` — green.
- `uv run pytest packages/core/tests/test_mix_measurement.py -m perf -s` —
  4 passed, output captured in `phase8_baseline.md`.

New Rust coverage in `stream/band.rs`: the rolling band reproduces
`sosfiltfilt` to 1e-9 driven a quantum at a time, is block-size independent to
1e-12, and no single call exceeds its slice budget.

## 7. What phase 8 did not do

- No gain-table, panner, or renorm change (phases 9-11 own those).
- No new measurements — the kit was re-run, not extended.
- `UNIFY_HORIZON_MS` is untouched at 100 ms; only the decorrelator's horizon
  was re-derived, since only it was sized by a truncation this phase removed.
