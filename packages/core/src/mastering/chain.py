"""Post-mixing mastering chain: EQ shaping → bus compression → loudness → limiter.

Encapsulates all mastering-stage processing so both the realtime and stem
pipelines share identical mastering behaviour.  The mixing pipelines handle
only spatial routing and energy normalisation; this module handles everything
that shapes the final tone, dynamics, loudness, and peak ceiling.

Processing order
----------------
0. **Reference matching** (optional) — spectral envelope ratio EQ + global RMS
   scalar derived from a reference audio file.  Runs first to imprint the
   reference's "feel" before any other mastering stage.  Controlled by
   ``config.mastering_match_ref_path`` (``None`` = disabled).
1. **Spectral shaping** (optional) — minimum-phase FIR tonal curve applied to
   all channels except LFE.  Controlled by ``config.mastering_eq_profile`` and
   ``config.mastering_eq_strength``.  Disabled when profile is ``None``.
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
   send, and an LFE gain trim.  Controlled by ``config.mastering_bass_profile`` and individual
   ``mastering_bass_*`` params.  Disabled when both profile and all individual
   params are unset.

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
   True-Peak compliance for this chain.
4. **Look-ahead true-peak limiter** — a linked, look-ahead brickwall
   limiter (:class:`~upmixer.mastering.limiter.LookAheadLimiter`) reduces
   gain ahead of any inter-sample peak so the delivered signal never
   exceeds ``config.loudness_max_tp`` dBTP.  Runs *last*, after loudness
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
from dataclasses import dataclass

import numpy as np

from upmixer.config import UpmixConfig
from upmixer.formats import OutputFormat

from .limiter import LookAheadLimiter

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "loudness": {
        "normalize": ("config", "loudness_normalize"),
        "target":    ("config", "loudness_target"),
        "max_tp":    ("config", "loudness_max_tp"),
    },
})
del _rbk


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


class MasteringChain:
    """Stateless mastering chain for post-mixing multichannel audio.

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
    ) -> tuple[dict[str, np.ndarray], MasteringResult]:
        """Apply the full mastering chain to a mixed multichannel bed.

        Args:
            channels:    Dict channel_name → 1D float64 array.
            sample_rate: Audio sample rate in Hz.
            output_fmt:  Output format — used to select BS.1770-4 channel weights.

        Returns:
            ``(processed_channels, MasteringResult)`` where
            ``processed_channels`` is the mastered multichannel dict and
            ``MasteringResult`` carries the loudness metadata.
        """
        cfg = self._cfg
        result = MasteringResult()

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
                sample_rate=sample_rate,
            )
            channels = proc.process(channels)

        if cfg.mastering_eq_profile is not None:
            from .eq import SpectralShaper
            shaper = SpectralShaper(
                profile=cfg.mastering_eq_profile,
                strength=cfg.mastering_eq_strength,
                sample_rate=sample_rate,
            )
            channels = shaper.process(channels)

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
                channels = comp.process(channels)

        _bass_active = (
            cfg.mastering_bass_profile is not None
            or cfg.mastering_bass_sub_gain_db is not None
            or cfg.mastering_bass_mid_gain_db is not None
            or cfg.mastering_bass_unify_hz is not None
            or cfg.mastering_bass_spread is not None
            or cfg.mastering_bass_punch is not None
            or cfg.mastering_bass_lfe_mode is not None
            or cfg.mastering_bass_lfe_send is not None
            or cfg.mastering_bass_lfe_gain_db is not None
            or cfg.mastering_bass_excite is not None
            or cfg.mastering_bass_decorrelate is not None
        )
        if _bass_active:
            from .bass import BassController, BASS_PROFILES
            preset = BASS_PROFILES.get(cfg.mastering_bass_profile or "", {})

            def _bp(attr: str, default=0.0):
                val = getattr(cfg, attr)
                stripped = attr.removeprefix("mastering_bass_")
                return val if val is not None else preset.get(stripped, default)

            bass = BassController(
                sub_gain_db=_bp("mastering_bass_sub_gain_db"),
                mid_gain_db=_bp("mastering_bass_mid_gain_db"),
                unify_hz=_bp("mastering_bass_unify_hz", None),
                spread=_bp("mastering_bass_spread", "bed"),
                punch=_bp("mastering_bass_punch"),
                excite=_bp("mastering_bass_excite", False),
                lfe_mode=_bp("mastering_bass_lfe_mode", "off"),
                lfe_send=_bp("mastering_bass_lfe_send"),
                lfe_gain_db=_bp("mastering_bass_lfe_gain_db"),
                decorrelate=_bp("mastering_bass_decorrelate"),
                lfe_authoring_gain=cfg.lfe_gain,
                sample_rate=sample_rate,
            )
            channels = bass.process(channels)

        if cfg.loudness_normalize:
            _log.info("  Normalizing loudness (BS.1770-4)...")
            from upmixer.loudness import normalize_loudness

            channels, ln_info = normalize_loudness(
                channels,
                sample_rate,
                output_fmt,
                target_lkfs=cfg.loudness_target_lkfs,
                max_tp_dbtp=cfg.loudness_max_tp,
                max_gain_db=cfg.loudness_max_gain_db,
                apply_tp_gain=False,
            )
            _log.info(
                "  Loudness: %.1f LKFS → %.1f LKFS  gain %+.1f dB  TP %.1f dBTP%s",
                ln_info["pre_lkfs"],
                cfg.loudness_target_lkfs,
                ln_info["applied_gain_db"],
                ln_info["measured_tp_dbtp"],
                "  [TP limited]" if ln_info["tp_limited"] else "",
            )

        # The look-ahead limiter runs last, after loudness/true-peak
        # correction, so it only ever engages as a safety net on the
        # already-corrected signal — limiting first would bake its gain
        # reduction in ahead of whatever peaks the pre-gain bed happens to
        # have, which no later scalar gain can undo (the same bug class
        # fixed in render_binaural_delivery; see that module's docstring).
        limiter = LookAheadLimiter(
            ceiling_dbtp=cfg.loudness_max_tp,
            lookahead_ms=cfg.limiter_lookahead_ms,
            release_ms=cfg.limiter_release_ms,
            sample_rate=sample_rate,
        )
        channels = limiter.process(channels)

        if cfg.loudness_normalize:
            from upmixer.loudness import measure_integrated_loudness, measure_true_peak

            result = MasteringResult(
                measured_lkfs=measure_integrated_loudness(channels, sample_rate, output_fmt),
                measured_tp_dbtp=measure_true_peak(channels),
                applied_gain_db=ln_info["applied_gain_db"],
                tp_limited=ln_info["tp_limited"],
            )
        return channels, result
