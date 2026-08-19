# Mastering Quality — Phase Plans

Goal: close the gaps between the mastering section and current delivery
practice for spatial audio, traditional multichannel, and stereo — the code
that turns the mixed bed into the delivered master:
`packages/core/src/mastering/` (chain, EQ, compressor, bass, limiter,
match_reference), `packages/core/src/loudness.py`, the shared Rust stages in
`packages/dsp/crates/dsp-core/src/mastering/` + `stream/master.rs` +
`loudness_stream.rs`, the export writers (`packages/core/src/io/`), and the
web mastering surface (`apps/web/src/features/composer/sections/
MasteringSection.tsx`, `apps/web/src/features/projects/` preview metering
and bypass).

What is already right and must not regress: the contracted stage order
(reference match → EQ → compression → bass → BS.1770 loudness → limiter
last, `mastering/chain.py`), the shared-curve/linked-gain invariant that
keeps every stage commuting with the LF sum
(`docs/contracts/preview_export_parity.md` §1), the BS.1770-5 K-weighting /
gating / channel weights and the 4x-oversampled true-peak kernel shared by
meter and limiter, and preview/export algorithm identity through `dsp-core`.

Why (findings from the 2026-08-18 mastering audit, checked against the
Dolby Atmos Music delivery spec, ITU-R BS.1770-5, EBU R128 and current
mastering-tool practice):

1. **The immersive compliance number is measured on the wrong programme.**
   `MasteringChain` measures and normalizes BS.1770 loudness on the full
   bed (7.1.4 → twelve weighted channels). The Dolby Atmos Music spec —
   the source of the −18 LKFS / −1 dBTP defaults in `config.py` — measures
   integrated loudness on the **5.1 re-render** of the mix, and that is
   the number distributor QC reads. On height-heavy content the two
   diverge, so a bed normalized to −18 can fail (or undershoot) the spec.
2. **The limiter links LFE into the shared gain curve.**
   `mastering/limiter.rs::lookahead_limit` takes the envelope maximum
   across *all* channels, LFE included, and applies one gain everywhere —
   so an LFE-only peak (`cinema` bass profile sends 50% of the low bus to
   LFE) ducks the entire bed. Immersive limiter practice (FLUX Elixir,
   Pulsar P21 Atlas) is: shared gain-reduction for the mains to preserve
   imaging, LFE capped independently and excluded from the link.
3. **Measurement stops at integrated + true peak.** No LRA (EBU Tech
   3342), no momentary/short-term maxima, no PLR/PSR crest metrics, no
   gain-reduction telemetry. `MasteringResult` carries two numbers, they
   reach the BWF bext chunk — and nothing else: the jobs API
   (`apps/api/src/features/jobs/schemas.py`) and the web UI never see
   them. There is no way to tell from a finished export whether the
   limiter shaved 0.2 dB or crushed 6 dB.
4. **No delivery targets.** Loudness is two raw sliders (target LKFS,
   ceiling dBTP). Nothing encodes the actual specs: Atmos Music ≤ −18
   LKFS / −1 dBTP, EBU R128 −23 ±0.5 / −1, ATSC A/85 −24 ±2 / −2,
   Netflix −27 ±2 dialog-gated / −2 (bed limiters at −2.3), stereo
   streaming ≈ −14 / −1, Apple −16. A user can dial −10 LKFS on an Atmos
   bed and `loudness_max_gain_db = 30` will drive the limiter into
   continuous deep GR with no warning anywhere.
5. **Bit-depth reduction is undithered.** `io/writer.py` hands float64 to
   libsndfile at `PCM_24`/`PCM_16` with no TPDF dither; a 16-bit export
   truncates. Best practice: dither exactly once, as the absolute last
   operation, optional noise shaping.
6. **The chain has no head and no pre-limiter stage.** No subsonic
   HPF / DC-offset control anywhere in the chain, and no soft-clip or
   saturation stage ahead of the limiter — on transient-heavy material
   the limiter does all the work alone, which is the pumping-prone
   configuration every modern chain avoids (clipper shaves ~0.5–1 dB,
   limiter cleans up). No dynamic EQ or multiband option exists either;
   the bus compressor is single-band full-range.
7. **A/B is loudness-biased.** The master-bypass button compares the
   mastered bed (loudness-normalized to target) against the raw bed at
   whatever level it happens to have. 1–2 dB of level difference is
   enough to decide the comparison on loudness alone; a gain-matched
   bypass is the standard fix.
8. **The preview meters RMS/peak only** (`stream/meters.rs`,
   `useStripMeterLoop.ts`). The engine already runs a full BS.1770
   measurement (`stream/measure.rs`) and owns the TP kernel, but the
   user never sees LUFS, true peak, correction gain, or limiter GR.
9. **Reference matching lacks the controls that make match-EQ usable**:
   no smoothing-amount control, no frequency-range masks, no
   loudness-matched audition of the result. (Strength, max-correction
   soft knee and the ±2 dB sub-bass clamp are already right.)
10. **Downmix compatibility is unchecked.** The export can write a
    BS.775 stereo downmix and the preview can audition folds, but
    nothing measures the folds: stereo/binaural loudness and TP are
    never reported next to the native bed's, so a master that folds
    3 LU quiet or clips post-fold ships silently.

Non-goals, decided now: per-channel or M/S EQ on the multichannel bus
(breaks the shared-curve phase invariant BS.775 fold-down and transaural
XTC depend on — `match_reference/processor.py` docstring); low-end mono
for stereo delivery (already covered — the bass controller's `unify_hz` on
a 2-channel bed *is* a side-channel low cut); dialnorm/AC-3 metadata
authoring (no encoder in scope); upward compression.

## Phases

Run in order. Each phase is a self-contained agent task with its own
validation; a phase must be green before the next starts.

| Phase | File | Deliverable |
|-------|------|-------------|
| 0 | `phase0_measurement_kit_baseline.md` | Mastering measurement kit (LRA, M/S maxima, PLR/PSR, per-channel TP, limiter/comp GR stats) + compliance baseline report + audit of the 5.1-fold delta, LFE-link duck depth, 96 kHz TP factor and quantization floor. May re-scope later phases — run first. |
| 1 | `phase1_delivery_targets.md` → `phase1_report.md` | **Done.** Named delivery targets (atmos-music, ebu-r128, atsc-a85, netflix-atmos, streaming-stereo, apple-music, custom); immersive compliance measured on the 5.1 re-render; results surfaced through jobs API and web UI. |
| 2 | `phase2_limiter_linking.md` | LFE out of the limiter's shared gain engine (independent TP cap), GR telemetry, optional partial link — in `dsp-core` once, offline + streaming. |
| 3 | `phase3_preview_metering_ab.md` | Momentary/short-term loudness + TP + GR meters in the preview, PLR/PSR readout, and a loudness-matched master bypass. |
| 4 | `phase4_chain_head_tail.md` | Chain head (subsonic HPF + DC block) and pre-limiter soft clip, both linked/shared so the commutation invariant holds; default off. |
| 5 | `phase5_dynamic_eq.md` | Linked-detection dynamic EQ stage (threshold-triggered bands), default off — the surgical tool the static profile EQ can't be. |
| 6 | `phase6_dither_export.md` | TPDF dither (+ optional noise shaping) at bit-depth reduction, dither-last ordering guarantee, SRC quality audit. |
| 7 | `phase7_reference_match_usability.md` | Match smoothing control, frequency-range masks, loudness-matched audition. |
| 8 | `phase8_downmix_qc.md` | Fold/render QC: loudness + TP of the BS.775 stereo fold and binaural render measured and reported against the native bed, with UI warnings. |

Phase 5 is the highest-risk phase (multiband detectors diverging through
decaying broadband material is exactly what killed mixing phase 13 — read
`docs/plans/mixing/phase13_report.md` §9 before starting) and the plan
survives without it. Phases 6–8 are independent of each other and of 5;
if priorities force a cut, cut from 5 first, never from 0–2.

A codec-preview stage (post-AAC/Opus true-peak check, NUGEN
MasterCheck-style) was considered and deferred: it needs an encoder
dependency the workspace doesn't carry, and the −1 dBTP ceiling already
budgets for codec overshoot. Revisit only if delivery reports show
post-codec clipping in the wild.

## Ground rules for every phase

- Read the repo root `AGENTS.md` and `packages/core/AGENTS.md` first.
  Comment policy, file-size policy (~400 soft / ~600 hard), and package
  boundaries (web/CLI consume only core's public API; no DSP in the web
  layer) all apply.
- `uv run pytest packages/core/tests apps/api/tests apps/cli/tests -q`
  must pass before and after every phase (baseline: 1175 passed /
  43 deselected after phase 1). Phases touching `apps/web` also run
  `npm test` and `npm run build` there.
- **Preview/export parity is a hard constraint.** Any new mastering DSP
  lands once in `dsp-core` with both an offline entry and a streaming
  entry, is reached via PyO3 on the export side and wasm in the preview,
  and updates `docs/contracts/preview_export_parity.md` §1–§3 in the same
  phase. New tunables are served by the engine-constants endpoint
  (`apps/api/src/features/system/service.py`) and consumed through
  `resolveEngineConstants` — never hardcoded in the web. Rebuild the
  committed wasm (`npm run build:wasm`) after any `packages/dsp` change.
- The preview worklet must stay inside its 2.67 ms/quantum budget. Any
  phase touching the streaming path runs `npm run bench:engine` in
  `apps/web` and reports numbers; stages that ship default-off are
  benched on.
- **The contracted stage order is load-bearing.** New stages slot into
  the order documented in `mastering/chain.py`'s docstring and parity
  contract §1; anything that runs on the bed before the limiter must be
  a shared curve or linked gain across non-LFE channels (the commutation
  invariant), or explicitly justify why not in the phase report and the
  contract.
- Mastering quality is only partly SDR-measurable. Every audible change
  validates with (a) the phase 0 measurement kit re-run and (b) a short
  A/B listening note in the phase report. The separation eval harness is
  NOT required — no phase here may change separation behavior; if one
  accidentally does, stop and re-scope.
- Standards-governed changes (loudness measurement, TP detection, LFE
  behavior, downmix folds) must update the matching doc under
  `docs/standards/` in the same phase — `loudness_dsp_bs1770.md` and
  `spatial_layouts_bs775_bs2051.md` are the two this plan touches — and
  cite it from code with at most a one-line pointer.
- Consult `~/Projects/upmixer-knowledge/techniques/mastering_restoration.md`
  (external sibling repo, read by absolute path) before each phase, per
  `packages/core/AGENTS.md`; if the directory is missing in the
  environment, note that in the phase report rather than guessing.
- No new Python or JS dependencies. New DSP (dither, clipper, dynamic-EQ
  bands, LRA histogram) is hand-rolled in `dsp-core` with golden tests,
  matching existing kernel style.
- Delivery-spec numbers cited in code or docs carry their source (spec
  name + section) in the standards doc, not in comments.
