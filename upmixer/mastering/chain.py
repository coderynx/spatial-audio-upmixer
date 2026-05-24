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
   when set.
2.5 **Bass control** (optional) — multichannel low-end shaper: sub/mid-bass EQ,
   bass mono-maker for L/R pairs, harmonic exciter, LFE gain trim.  Controlled
   by ``config.mastering_bass_profile`` and individual ``mastering_bass_*``
   params.  Disabled when both profile and all individual params are unset.
3. **ITU-R BS.1770-4 loudness normalization** (if
   ``config.loudness_normalize`` is ``True``).  A scalar linear gain is applied
   to all channels simultaneously — no dynamic processing, no clipping.
4. **Dolby Atmos True Peak ceiling** — if the post-LN peak exceeds
   ``config.loudness_max_tp`` dBTP, a second linear gain reduction is applied.
5. **Tanh soft-limiter** (always applied) — catches any residual transient
   peaks without hard-clipping.

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
from upmixer.utils import soft_limit

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
    """BS.1770-4 integrated loudness *before* normalization, in LKFS."""

    measured_tp_dbtp: float | None = None
    """Maximum True Peak across all channels *after* loudness gain, in dBTP."""

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
                    sample_rate=sample_rate,
                )
                channels = comp.process(channels)

        _bass_active = (
            cfg.mastering_bass_profile is not None
            or cfg.mastering_bass_sub_gain_db is not None
            or cfg.mastering_bass_mid_gain_db is not None
            or cfg.mastering_bass_mono_cutoff_hz is not None
            or cfg.mastering_bass_lfe_gain_db is not None
            or cfg.mastering_bass_excite
        )
        if _bass_active:
            from .bass import BassController, BASS_PROFILES
            preset = BASS_PROFILES.get(cfg.mastering_bass_profile or "", {})

            def _bp(attr: str, default: float = 0.0) -> float:
                val = getattr(cfg, attr)
                stripped = attr.removeprefix("mastering_bass_")
                return val if val is not None else preset.get(stripped, default)

            bass = BassController(
                sub_gain_db=_bp("mastering_bass_sub_gain_db"),
                mid_gain_db=_bp("mastering_bass_mid_gain_db"),
                mono_cutoff_hz=(
                    cfg.mastering_bass_mono_cutoff_hz
                    if cfg.mastering_bass_mono_cutoff_hz is not None
                    else preset.get("mono_cutoff_hz")
                ),
                excite=cfg.mastering_bass_excite or preset.get("excite", False),
                lfe_gain_db=_bp("mastering_bass_lfe_gain_db"),
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
            )
            result = MasteringResult(
                measured_lkfs=ln_info["measured_lkfs"],
                measured_tp_dbtp=ln_info["measured_tp_dbtp"],
                applied_gain_db=ln_info["applied_gain_db"],
                tp_limited=ln_info["tp_limited"],
            )
            _log.info(
                "  Loudness: %.1f LKFS → %.1f LKFS  gain %+.1f dB  TP %.1f dBTP%s",
                result.measured_lkfs,
                cfg.loudness_target_lkfs,
                result.applied_gain_db,
                result.measured_tp_dbtp,
                "  [TP limited]" if result.tp_limited else "",
            )

        channels = {
            name: soft_limit(ch, cfg.peak_limit_threshold)
            for name, ch in channels.items()
        }

        return channels, result
