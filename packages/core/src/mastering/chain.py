"""Linked bed/object mastering: EQ → compression → loudness → limiter.

Encapsulates all mastering-stage processing so both the realtime and stem
pipelines share identical mastering behaviour.  The mixing pipelines handle
spatial routing and energy normalisation; this module masters authored bed and
object sources against their rendered speaker programme.

Processing order
----------------
-1. **Chain head** (optional) — subsonic high-pass on every non-LFE channel
   plus a DC blocker on LFE.  Runs before everything else so no later stage
   matches, shapes or measures DC and sub-20 Hz rumble.  Controlled by
   ``config.mastering_highpass_enabled`` / ``mastering_highpass_hz``.
0. **Reference matching** (optional) — spectral envelope ratio EQ + global RMS
   scalar derived from a reference audio file.  Runs first to imprint the
   reference's "feel" before any other mastering stage.  Controlled by
   ``config.mastering_match_ref_path`` (``None`` = disabled).
1. **Spectral shaping** (optional) — minimum-phase FIR tonal curve applied to
   all channels except LFE.  Controlled by ``config.mastering_eq_profile`` and
   ``config.mastering_eq_strength``.  Disabled when profile is ``None``.
1.5 **Dynamic EQ** (optional) — up to four parametric bells that act only when
   their own band crosses its threshold, each driven by one detector linked
   across every non-LFE channel.  Surgical correction ahead of glue, and still
   a shared time-varying filter across the bed, so it commutes with the LF sum
   like the stages either side of it.  Controlled by
   ``config.mastering_dyneq_profile`` (a name in ``DYNEQ_PROFILES``), with
   ``config.mastering_dyneq_bands`` overriding it outright for explicit
   control.  Disabled when both are unset.
2. **Bus compression** (optional) — linked-sidechain RMS glue compressor.
   Cosmetic only; does not substitute for loudness normalization.  Controlled
   by ``config.mastering_comp_profile`` (``None`` = disabled).  Individual
   param fields (``mastering_comp_threshold_db``, etc.) override the profile
   when set.  ``mastering_comp_sidechain_hpf_hz`` high-passes the detector
   only, which is what keeps the bed's low end from driving gain reduction
   across every channel — pair it with bass control's ``punch``, which the
   full-band sidechain otherwise squashes before the shaper ever sees it.
2.5 **Bass control** (optional) — multichannel bass management: sub/mid-bass
   EQ, LF unification across the bed with redistribution, transient shaping
   and harmonic excitation on the unified bus, mid-bass decorrelation, an LFE
   send, and an LFE gain trim.  Controlled by ``config.mastering_bass_enabled``
   plus ``config.mastering_bass_profile`` and individual ``mastering_bass_*``
   params.  ``False`` is an explicit bypass; ``None`` preserves the legacy
   profile/override activation rules.

   This stage runs *after* reference matching for a reason that is not
   cosmetic: ``match_reference/spectrum.py`` compares a BS.1770-weighted
   *power* sum against the reference, so correlated bass spread across N
   channels reads as a ~10·log10(N) dB low-frequency deficit.  Unifying first
   would make the matcher lean on its ±2 dB sub-bass clamp on every render.
   The EQ, reference and compression stages ahead of it all apply one shared
   curve or one linked gain to every bed channel, which commutes with the LF
   sum — so bass control never has to compensate for what they did.
3. **ITU-R BS.1770-5 loudness normalization** (if
   ``config.loudness_normalize`` is ``True``).  A scalar linear gain is applied
   to all channels simultaneously — no dynamic processing, no clipping.  The
   scalar True-Peak gain step ``normalize_loudness`` otherwise applies is
   skipped here (``apply_tp_gain=False``) since step 4 below now owns
   True-Peak compliance for this chain.  Target and ceiling come from the
   named delivery target (``config.loudness_target_preset``, resolved by
   :func:`~upmixer.mastering.delivery.resolve_delivery_target`), and for beds
   wider than 5.1 both the normalization and the reported number are measured
   on the 5.1 re-render the delivery specs read
   (``docs/standards/loudness_dsp_bs1770.md`` §"Measurement programme").
3.5 **Soft clip** (optional) — one shared memoryless transfer curve on every
   non-LFE channel, shaving transients so the limiter is not left doing all
   the peak control alone.  Runs after loudness normalization, since
   normalizing an already-clipped signal would make the clip depth depend on
   the loudness target.  Controlled by ``config.mastering_clip_enabled`` /
   ``mastering_clip_db`` / ``mastering_clip_knee``.  This is the one
   pre-limiter stage that does *not* commute with the LF sum, which is why it
   sits after bass management — see
   ``docs/contracts/preview_export_parity.md`` §1.
4. **Look-ahead true-peak limiter** — a look-ahead brickwall limiter
   (:class:`~upmixer.mastering.limiter.LookAheadLimiter`), linked across the
   mains and capping LFE on its own curve, reduces gain ahead of any
   inter-sample peak so the delivered signal never exceeds
   ``config.loudness_max_tp`` dBTP.  Runs *last*, after loudness
   normalization, so it only ever engages as a true-peak safety net on the
   already loudness-corrected signal — running it earlier would bake its
   gain reduction in ahead of whatever peaks the pre-gain bed happens to
   have, and a later scalar gain couldn't undo that.  (The same
   process-order bug class was previously found and fixed in
   ``upmixer.binaural.renderer.render_binaural_delivery``; this chain
   follows the same discipline.)  Loudness/True-Peak are re-measured after
   this step so ``MasteringResult`` always reflects the truly-final
   delivered waveform.

Standards compliance (``atmos-music`` profile)
-----------------------------------------------
- Dolby Atmos Music Master Delivery Specification v2022.07:
  Integrated loudness ≤ −18.0 LKFS (target), True Peak ≤ −1.0 dBTP.
- BS.1770-4 K-weighting filters are exact per Annex 1.
- bext chunk loudness metadata is populated via the writer (caller passes
  ``MasteringResult`` fields to ``AdmBwfWriter.write()``).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig
from upmixer.formats import OutputFormat

from .delivery import resolve_delivery_target
from .foldqc import FoldQC, measure_folds
from .limiter import LookAheadLimiter

_log = logging.getLogger("upmixer")

MANIFEST_FIELDS = {
    "loudness": {
        "normalize":     ("config", "loudness_normalize"),
        "target_preset": ("config", "loudness_target_preset"),
        "target":        ("config", "loudness_target"),
        "max_tp":        ("config", "loudness_max_tp"),
    },
    "qc": {
        "measure_binaural": ("config", "qc_measure_binaural"),
    },
}


@dataclass
class MasteringResult:
    """Metadata produced by a completed mastering pass.

    Loudness fields are ``None`` when ``loudness_normalize`` is disabled in
    config.  Suitable for embedding in :class:`~upmixer.result.UpmixResult`
    and for writing to the BWF bext chunk loudness fields.
    """

    measured_lkfs: float | None = None
    """BS.1770-5 integrated loudness of delivered PCM, in LKFS."""

    measured_tp_dbtp: float | None = None
    """Maximum True Peak across delivered PCM, in dBTP."""

    applied_gain_db: float | None = None
    """Total gain applied (loudness gain ± TP correction), in dB."""

    tp_limited: bool = False
    """True if gain was reduced to meet the True Peak ceiling."""

    lra_lu: float | None = None
    """EBU Tech 3342 loudness range of delivered PCM, in LU."""

    max_momentary_lkfs: float | None = None
    """Loudest 400 ms window (EBU Tech 3341 momentary maximum), in LKFS."""

    max_short_term_lkfs: float | None = None
    """Loudest 3 s window (EBU Tech 3341 short-term maximum), in LKFS."""

    plr_db: float | None = None
    """Peak-to-loudness ratio: true peak minus integrated loudness, in dB."""

    psr_db: float | None = None
    """Peak-to-short-term ratio: true peak minus the loudest short-term
    window, in dB.  The over-limiting canary — mastering practice treats a
    PSR under ~8 dB in the loudest sections as crushed."""

    limiter_gr_peak_db: float | None = None
    """Deepest gain reduction the true-peak limiter applied to the mains, in dB."""

    limiter_gr_duty: float | None = None
    """Fraction of samples the limiter held the mains under reduction."""

    limiter_gr_lfe_peak_db: float | None = None
    """Deepest gain reduction on the LFE's own curve, in dB."""

    comp_gr_peak_db: float | None = None
    """Deepest gain reduction the bus compressor applied, in dB."""

    comp_gr_avg_db: float | None = None
    """Mean gain reduction across the bus compressor's whole programme."""

    per_channel_tp_dbtp: dict[str, float] | None = None
    """Delivered True Peak per channel, in dBTP, keyed by channel name."""

    target_preset: str | None = None
    """Name of the delivery target the pass was held to, or *None* for free
    target/ceiling values."""

    target_lkfs: float | None = None
    """Integrated loudness the pass normalized to, in LKFS."""

    target_tolerance_lu: float | None = None
    """The target's published tolerance in LU, or *None* where the
    specification gives a target without one."""

    target_max_tp_dbtp: float | None = None
    """True Peak ceiling the pass was held to, in dBTP."""

    loudness_compliant: bool | None = None
    """Whether ``measured_lkfs`` lands inside ``target_tolerance_lu`` of the
    target.  *None* when the target publishes no tolerance."""

    tp_compliant: bool | None = None
    """Whether ``measured_tp_dbtp`` stays under the ceiling."""

    fold_referenced: bool = False
    """True when ``measured_lkfs`` and the loudness statistics come from the
    5.1 re-render rather than the delivered bed."""

    full_bed_lkfs: float | None = None
    """Integrated loudness of the delivered bed itself — the secondary
    diagnostic that sits next to a fold-referenced ``measured_lkfs``."""

    folds: FoldQC | None = None
    """Loudness/True-Peak QC of the master's folds (BS.775 stereo downmix, 5.1
    re-render, binaural render), or *None* for a bed with no fold to measure."""

    def delivery_fields(self) -> dict:
        """The loudness and compliance block, keyed as
        :class:`~upmixer.result.UpmixResult` fields."""
        return {
            "measured_lkfs": self.measured_lkfs,
            "measured_tp_dbtp": self.measured_tp_dbtp,
            "applied_gain_db": self.applied_gain_db,
            "target_preset": self.target_preset,
            "target_lkfs": self.target_lkfs,
            "target_max_tp_dbtp": self.target_max_tp_dbtp,
            "target_tolerance_lu": self.target_tolerance_lu,
            "loudness_compliant": self.loudness_compliant,
            "tp_compliant": self.tp_compliant,
            "fold_referenced": self.fold_referenced,
            "full_bed_lkfs": self.full_bed_lkfs,
            "folds": self.folds,
        }


class MasteringChain:
    """Stateless mastering chain for authored multichannel sources.

    Instantiate with a :class:`~upmixer.config.UpmixConfig` once per pipeline
    run.  Call :meth:`process` with the fully mixed channel dict.

    Args:
        config: UpmixConfig controlling all mastering parameters.
    """

    def __init__(self, config: UpmixConfig) -> None:
        self._cfg = config

    def process(
        self,
        channels: dict[str, np.ndarray],
        sample_rate: int,
        output_fmt: OutputFormat,
        linked_channels: dict[str, np.ndarray] | None = None,
        programme_renderer: Callable[
            [dict[str, np.ndarray]], dict[str, np.ndarray]
        ] | None = None,
    ) -> tuple[dict[str, np.ndarray], MasteringResult]:
        """Apply the full mastering chain to a bed and optional objects.

        Args:
            channels:    Dict channel_name → 1D float64 array.
            sample_rate: Audio sample rate in Hz.
            output_fmt:  Output format — used to select BS.1770-4 channel weights.
            linked_channels: Object tracks processed with the bed while kept
                as independently authored signals.
            programme_renderer: Render the current bed and linked tracks to the
                speaker programme used by linked detectors and measurement.

        Returns:
            ``(processed_channels, MasteringResult)`` where
            ``processed_channels`` is the mastered multichannel dict and
            ``MasteringResult`` carries the loudness metadata.
        """
        cfg = self._cfg
        delivery = resolve_delivery_target(cfg)
        # Layout arity, not output type: a stereo/binaural/transaural delivery
        # already measures its own two-channel programme.
        fold = len(output_fmt.channels) > 6
        result = MasteringResult()
        comp_gr: tuple[float, float] | None = None
        renderer_aware = linked_channels is not None and programme_renderer is not None
        bed_names = {name: name for name in channels}
        linked_names = (
            {name: f"object:{name}" for name in linked_channels}
            if linked_channels is not None
            else {}
        )
        lfe_key = bed_names.get("LFE", "LFE")

        def sources() -> dict[str, np.ndarray]:
            return {
                **{key: channels[name] for name, key in bed_names.items()},
                **{
                    key: linked_channels[name]
                    for name, key in linked_names.items()
                },
            }

        def accept(processed: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
            if linked_channels is not None:
                linked_channels.update({
                    name: processed[key] for name, key in linked_names.items()
                })
            return {name: processed[key] for name, key in bed_names.items()}

        if cfg.mastering_highpass_enabled:
            from .head import apply_chain_head
            channels = accept(apply_chain_head(
                sources(), sample_rate, cfg.mastering_highpass_hz, lfe_key
            )) if renderer_aware else apply_chain_head(
                channels, sample_rate, cfg.mastering_highpass_hz,
            )

        if cfg.mastering_match_ref_path is not None:
            from .match_reference import ReferenceMatchProcessor
            _log.info(
                "  Match reference: analysing '%s'...", cfg.mastering_match_ref_path
            )
            proc = ReferenceMatchProcessor(
                reference_path=cfg.mastering_match_ref_path,
                output_fmt=output_fmt,
                strength=cfg.mastering_match_ref_strength,
                match_spectrum=cfg.mastering_match_ref_spectrum,
                match_rms=cfg.mastering_match_ref_rms,
                max_correction_db=cfg.mastering_match_ref_max_db,
                smooth_octaves=cfg.mastering_match_ref_smooth_oct,
                low_hz=cfg.mastering_match_ref_low_hz,
                high_hz=cfg.mastering_match_ref_high_hz,
                sample_rate=sample_rate,
            )
            channels = accept(proc.process(
                sources(), lfe_key, programme_renderer(channels)
            )) if renderer_aware else proc.process(channels)

        if cfg.mastering_eq_profile is not None:
            from .eq import SpectralShaper
            shaper = SpectralShaper(
                profile=cfg.mastering_eq_profile,
                strength=cfg.mastering_eq_strength,
                sample_rate=sample_rate,
            )
            channels = accept(shaper.process(
                sources(), lfe_key
            )) if renderer_aware else shaper.process(channels)

        if cfg.mastering_dyneq_profile or cfg.mastering_dyneq_bands:
            from .dyneq import apply_dynamic_eq, resolve_dyneq_bands
            processed = apply_dynamic_eq(
                sources() if renderer_aware else channels,
                sample_rate,
                resolve_dyneq_bands(
                    cfg.mastering_dyneq_profile, cfg.mastering_dyneq_bands
                ),
                lfe_key=lfe_key if renderer_aware else "LFE",
                detector_channels=(
                    programme_renderer(channels) if renderer_aware else None
                ),
            )
            channels = accept(processed) if renderer_aware else processed

        if cfg.mastering_comp_profile is not None:
            from .compressor import BusCompressor, COMP_PROFILES

            preset = COMP_PROFILES.get(cfg.mastering_comp_profile, {})
            if not preset:
                _log.warning(
                    "Unknown compressor profile '%s' — skipping. "
                    "Valid: %s",
                    cfg.mastering_comp_profile,
                    sorted(COMP_PROFILES.keys()),
                )
            else:
                comp = BusCompressor(
                    threshold_db=cfg.mastering_comp_threshold_db
                    if cfg.mastering_comp_threshold_db is not None
                    else preset["threshold_db"],
                    ratio=cfg.mastering_comp_ratio
                    if cfg.mastering_comp_ratio is not None
                    else preset["ratio"],
                    attack_ms=cfg.mastering_comp_attack_ms
                    if cfg.mastering_comp_attack_ms is not None
                    else preset["attack_ms"],
                    release_ms=cfg.mastering_comp_release_ms
                    if cfg.mastering_comp_release_ms is not None
                    else preset["release_ms"],
                    knee_db=cfg.mastering_comp_knee_db
                    if cfg.mastering_comp_knee_db is not None
                    else preset["knee_db"],
                    makeup_db=cfg.mastering_comp_makeup_db
                    if cfg.mastering_comp_makeup_db is not None
                    else preset["makeup_db"],
                    sidechain_hpf_hz=(
                        cfg.mastering_comp_sidechain_hpf_hz
                        if cfg.mastering_comp_sidechain_hpf_hz is not None
                        else preset["sidechain_hpf_hz"]
                    ),
                    sample_rate=sample_rate,
                )
                processed = comp.process(
                    sources() if renderer_aware else channels,
                    lfe_key=lfe_key if renderer_aware else "LFE",
                    detector_channels=(
                        programme_renderer(channels) if renderer_aware else None
                    ),
                )
                channels = accept(processed) if renderer_aware else processed
                comp_gr = (comp.gr_peak_db, comp.gr_avg_db)

        _bass_overrides = (
            cfg.mastering_bass_profile is not None
            or cfg.mastering_bass_sub_gain_db is not None
            or cfg.mastering_bass_mid_gain_db is not None
            or cfg.mastering_bass_unify_hz is not None
            or cfg.mastering_bass_spread is not None
            or cfg.mastering_bass_punch is not None
            or cfg.mastering_bass_harmonics is not None
            or cfg.mastering_bass_lfe_mode is not None
            or cfg.mastering_bass_lfe_send is not None
            or cfg.mastering_bass_lfe_gain_db is not None
            or cfg.mastering_bass_excite is not None
            or cfg.mastering_bass_decorrelate is not None
        )
        _bass_active = cfg.mastering_bass_enabled is True or (
            cfg.mastering_bass_enabled is not False and _bass_overrides
        )
        if _bass_active:
            from .bass import BASS_PROFILES, DEFAULT_UNIFY_HZ, BassController
            preset = BASS_PROFILES.get(cfg.mastering_bass_profile or "", {})

            def _bp(attr: str, default=0.0):
                val = getattr(cfg, attr)
                stripped = attr.removeprefix("mastering_bass_")
                return val if val is not None else preset.get(stripped, default)

            punch = _bp("mastering_bass_punch")
            harmonics = cfg.mastering_bass_harmonics
            if harmonics is None:
                excite = cfg.mastering_bass_excite
                harmonics = float(excite if excite is not None else preset.get("excite", False))
            unify_hz = _bp("mastering_bass_unify_hz", None)
            if unify_hz is None and (punch != 0.0 or harmonics > 0.0):
                unify_hz = DEFAULT_UNIFY_HZ

            bass = BassController(
                sub_gain_db=_bp("mastering_bass_sub_gain_db"),
                mid_gain_db=_bp("mastering_bass_mid_gain_db"),
                unify_hz=unify_hz,
                spread=_bp("mastering_bass_spread", "bed"),
                punch=punch,
                excite=harmonics > 0.0,
                lfe_mode=_bp("mastering_bass_lfe_mode", "off"),
                lfe_send=_bp("mastering_bass_lfe_send"),
                lfe_gain_db=_bp("mastering_bass_lfe_gain_db"),
                decorrelate=_bp("mastering_bass_decorrelate"),
                lfe_authoring_gain=cfg.lfe_gain,
                sample_rate=sample_rate,
                harmonics=harmonics,
            )
            processed = bass.process(
                sources() if renderer_aware else channels,
                lfe_key=lfe_key if renderer_aware else "LFE",
                spatial_channels=len(channels) if renderer_aware else None,
            )
            channels = accept(processed) if renderer_aware else processed

        if cfg.loudness_normalize:
            _log.info("  Normalizing loudness (BS.1770-4)...")
            from upmixer.loudness import normalize_loudness

            normalization_input = (
                programme_renderer(channels) if renderer_aware else channels
            )
            _, ln_info = normalize_loudness(
                normalization_input,
                sample_rate,
                output_fmt,
                target_lkfs=delivery.target_lkfs,
                max_tp_dbtp=delivery.max_tp_dbtp,
                max_gain_db=cfg.loudness_max_gain_db,
                apply_tp_gain=False,
                fold_measurement=fold,
            )
            gain = 10.0 ** (ln_info["applied_gain_db"] / 20.0)
            channels = {name: audio * gain for name, audio in channels.items()}
            if renderer_aware:
                linked_channels.update({
                    name: audio * gain for name, audio in linked_channels.items()
                })
            _log.info(
                "  Loudness: %.1f LKFS → %.1f LKFS  gain %+.1f dB  TP %.1f dBTP%s",
                ln_info["pre_lkfs"],
                delivery.target_lkfs,
                ln_info["applied_gain_db"],
                ln_info["measured_tp_dbtp"],
                "  [TP limited]" if ln_info["tp_limited"] else "",
            )

        if cfg.mastering_clip_enabled:
            from .clip import apply_soft_clip
            processed = apply_soft_clip(
                sources() if renderer_aware else channels,
                delivery.max_tp_dbtp,
                cfg.mastering_clip_db,
                cfg.mastering_clip_knee,
                lfe_key=lfe_key if renderer_aware else "LFE",
            )
            channels = accept(processed) if renderer_aware else processed

        # The look-ahead limiter runs last, after loudness/true-peak
        # correction, so it only ever engages as a safety net on the
        # already-corrected signal — limiting first would bake its gain
        # reduction in ahead of whatever peaks the pre-gain bed happens to
        # have, which no later scalar gain can undo (the same bug class
        # fixed in render_binaural_delivery; see that module's docstring).
        limiter = LookAheadLimiter(
            ceiling_dbtp=delivery.max_tp_dbtp,
            lookahead_ms=cfg.limiter_lookahead_ms,
            release_ms=cfg.limiter_release_ms,
            sample_rate=sample_rate,
        )
        if renderer_aware:
            limiter_input = {
                **{bed_names[name]: audio for name, audio in channels.items()},
                **{linked_names[name]: audio for name, audio in linked_channels.items()},
                **{
                    f"render:{name}": audio
                    for name, audio in programme_renderer(channels).items()
                    if name != "LFE"
                },
            }
            limited = limiter.process(
                limiter_input,
                lfe_key=bed_names.get("LFE", "LFE"),
            )
            channels = {name: limited[key] for name, key in bed_names.items()}
            linked_channels.update({
                name: limited[key] for name, key in linked_names.items()
            })
        else:
            channels = limiter.process(channels)

        if cfg.loudness_normalize:
            from upmixer.loudness import (
                ABS_GATE,
                measure_integrated_loudness,
                measure_loudness_stats,
                measure_true_peak,
                measure_true_peak_per_channel,
                measurement_programme,
            )

            delivered_programme = (
                programme_renderer(channels) if renderer_aware else channels
            )
            programme, programme_fmt = (
                measurement_programme(delivered_programme, output_fmt)
                if fold
                else (delivered_programme, output_fmt)
            )
            stats = measure_loudness_stats(programme, sample_rate, programme_fmt)
            measured_tp = measure_true_peak(delivered_programme)
            short_term = stats["max_short_term_lkfs"]
            deviation = abs(stats["integrated_lkfs"] - delivery.target_lkfs)
            native_lkfs = (
                measure_integrated_loudness(
                    delivered_programme, sample_rate, output_fmt
                )
                if fold
                else stats["integrated_lkfs"]
            )
            result = MasteringResult(
                measured_lkfs=stats["integrated_lkfs"],
                measured_tp_dbtp=measured_tp,
                applied_gain_db=ln_info["applied_gain_db"],
                tp_limited=ln_info["tp_limited"],
                lra_lu=stats["lra_lu"],
                max_momentary_lkfs=stats["max_momentary_lkfs"],
                max_short_term_lkfs=stats["max_short_term_lkfs"],
                plr_db=measured_tp - stats["integrated_lkfs"],
                psr_db=(
                    measured_tp - short_term
                    if short_term > ABS_GATE
                    else None
                ),
                limiter_gr_peak_db=limiter.gr_peak_db,
                limiter_gr_duty=limiter.gr_duty,
                limiter_gr_lfe_peak_db=limiter.gr_lfe_peak_db,
                comp_gr_peak_db=comp_gr[0] if comp_gr else None,
                comp_gr_avg_db=comp_gr[1] if comp_gr else None,
                per_channel_tp_dbtp=measure_true_peak_per_channel(
                    delivered_programme
                ),
                target_preset=delivery.preset,
                target_lkfs=delivery.target_lkfs,
                target_tolerance_lu=delivery.tolerance_lu,
                target_max_tp_dbtp=delivery.max_tp_dbtp,
                loudness_compliant=(
                    deviation <= delivery.tolerance_lu
                    if delivery.tolerance_lu is not None
                    else None
                ),
                tp_compliant=measured_tp <= delivery.max_tp_dbtp,
                fold_referenced=fold,
                full_bed_lkfs=native_lkfs if fold else None,
                folds=measure_folds(
                    delivered_programme,
                    sample_rate,
                    output_fmt,
                    cfg,
                    native_lkfs,
                    delivery.max_tp_dbtp,
                ),
            )
        return channels, result
