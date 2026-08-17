# Phase 5 — LFE level policy and crossover alignment

Read `docs/plans/mixing/README.md` first for context and ground rules.
Requires phase 0 (its LFE energy audit is the evidence base and the
yardstick). Run after phase 3 (send changes) so listening checks are not
confounded.

## Goal

Two defects in how the LFE bus is built
(`StemRouter.route` LFE handling, `routing/lfe.py`, preset `lfe` values
in `stem_placement.py`, `ZONE_ROUTING` LFE entries in `stem_router.py`):

1. **Level:** the LFE channel plays back +10 dB in-band. Preset sends
   (Kick 0.85–0.90, Bass 0.75) are full-band-referenced gains with no
   compensation, while the same stems keep full-band bass in FL/FR —
   phase 0's audit table quantifies the resulting in-band doubling.
   Atmos music practice keeps LFE an effect send, conservative.
2. **Phase:** the LFE bus lowpass (plain Butterworth of configurable
   order) has no alignment relationship with the unfiltered mains bass.
   The correlated in-band content sums with filter phase rotation —
   position-dependent build-up/cancellation around the crossover.

## Design decisions (make, document, implement)

- **In-band compensation:** apply a fixed −10 dB on the LFE bus at build
  time (one multiply, documented as the BS.775/Atmos monitoring-gain
  complement) rather than editing every preset value — preset `lfe`
  knobs keep their current relative meaning; absolute calibration becomes
  correct. Record in `docs/standards/loudness_dsp_bs1770.md` or a new
  short LFE section in the spatial-layouts standards doc (pick the doc
  that already discusses LFE; add a pointer from the other).
- **Alignment:** make the LFE lowpass a Linkwitz-Riley (even-order,
  squared Butterworth) at `lfe_cutoff_hz`. Do **not** add a complementary
  highpass to the mains (bass management is the playback system's job;
  mains keep full range per Atmos music practice) — the LR choice is
  about predictable phase at the crossover, and the −10 dB compensation
  already reduces the correlated-sum contribution. Verify with the
  phase 0 coincident-sum measurement that the residual ripple through
  crossover is within ±1.5 dB; if not, evaluate an allpass on the LFE bus
  to track the mains' phase and record the outcome either way.
- **Preset review:** with compensation in place, re-check Kick 0.85 /
  Bass 0.75 / `default_lfe_send` values against the audit table; adjust
  only if the measured LFE-vs-mains in-band ratio still exceeds the
  documented target (target: LFE contributes weight, mains carry the
  core — ratio ≤ 0 dB with +10 dB weighting applied; justify any
  exception per stem).

## Deliverables

1. LFE bus changes in `StemRouter.route` (compensation + LR lowpass) and
   the mirrored streaming path in `dsp-core` `stream/routing.rs`;
   `MultichannelUpmixer`'s `_lfe_filter` and `routing/lfe.py::LFEExtractor`
   get the same treatment so all three LFE producers agree.
2. Config surface: `lfe_filter_order` semantics revisited (LR orders are
   even; validate and document), defaults unchanged where possible.
3. Parity: engine constants / worklet path if LFE parameters are served
   to the web preview (grep the engine-constants endpoint); re-hash
   `docs/contracts/preview_export_parity.md` if touched.
4. Standards doc section as above; code comments reduced to one-line
   pointers.

## Tests

- LR lowpass magnitude −6 dB at cutoff (the LR signature), phase 0 kit's
  coincident-sum ripple within target.
- LFE in-band energy with +10 dB weighting vs mains: per-preset table
  re-generated; Kick/Bass rows meet the documented target.
- A stem with `lfe: 0` is bit-identical to before (compensation must not
  leak into non-LFE paths).
- Golden mastering-chain hashes: LFE content changes, so downstream
  loudness normalization gains will shift — regenerate goldens knowingly,
  and state in the PR that BS.1770 loudness (which excludes LFE from
  measurement) changes only via inter-channel masking effects, not
  directly.

## Out of scope

- Bass management / redirect of mains bass into LFE.
- Height/surround gain tables.
- The `lfe_gain` user knob semantics (stays a multiplier on the bus).

## Done when

- Full suites green; phase 0 LFE tables re-generated before/after in the
  report.
- A/B listening note: bass-heavy track on a calibrated 5.1/7.1.4 monitor
  chain (or a bass-managed headphone render) — low end tighter, no lost
  weight; stereo downmix (LFE excluded per BS.775) unchanged.
