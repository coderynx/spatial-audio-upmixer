"""CLI flag application and resource-limit setup for the ``upmixer`` CLI."""

import argparse
import math

from upmixer.config import UpmixConfig
from upmixer.separation import apply_stem_pan


def _apply_cli_flags(config: UpmixConfig, args: argparse.Namespace, sample_rate_set: bool) -> None:
    """Apply explicitly-set CLI flags to config.

    Only non-None values are applied so manifest defaults are preserved for
    flags the user did not supply.  ``sample_rate_set`` indicates whether
    ``--output-sample-rate`` was given on the command line (needed to avoid
    clobbering a manifest-set sample rate).
    """
    if args.format is not None:
        config.output_format = args.format
    if args.center_gain is not None:
        config.center_gain = args.center_gain
    if args.surround_gain is not None:
        config.surround_gain = args.surround_gain
    if args.back_gain is not None:
        config.back_gain = args.back_gain
    if args.height_gain is not None:
        config.height_gain = args.height_gain
    if args.lfe_gain is not None:
        config.lfe_gain = args.lfe_gain
    if args.lfe_cutoff is not None:
        config.lfe_cutoff_hz = args.lfe_cutoff
    if args.height_low_rolloff_gain is not None:
        config.height_low_rolloff_gain = args.height_low_rolloff_gain
    if args.height_high_shelf_gain is not None:
        config.height_high_shelf_gain = args.height_high_shelf_gain
    if args.height_directional_band_gain is not None:
        config.height_directional_band_gain = args.height_directional_band_gain
    if args.fft_size is not None:
        config.fft_size = args.fft_size
        config.hop_size = args.fft_size // 4
    if args.no_auto_fft:
        config.auto_fft_size = False
    if args.no_normalize:
        config.normalize_output = False
    if args.binaural_profile is not None:
        config.binaural_profile = args.binaural_profile
    if args.transaural_profile is not None:
        config.transaural_profile = args.transaural_profile
    if args.no_loudness_normalize:
        config.loudness_normalize = False
    if args.loudness_preset is not None:
        config.loudness_target_preset = args.loudness_preset
    if args.loudness_target is not None:
        config.loudness_target_lkfs = args.loudness_target
    if args.limiter_lookahead is not None:
        config.limiter_lookahead_ms = args.limiter_lookahead
    if args.limiter_release is not None:
        config.limiter_release_ms = args.limiter_release
    if args.output_type is not None:
        config.output_type = args.output_type
    elif not args.manifest:
        config.output_type = "multichannel"
    if args.output_codec is not None:
        config.output_codec = args.output_codec
    if args.output_subtype is not None:
        config.output_subtype = args.output_subtype
    if sample_rate_set:
        config.output_sample_rate = args.output_sample_rate
    if args.downmix_surround_coeff is not None:
        config.surround_downmix_coeff = args.downmix_surround_coeff
    if args.downmix_height_coeff is not None:
        config.height_downmix_coeff = args.downmix_height_coeff
    if args.downmix_output is not None:
        config.downmix_output_path = args.downmix_output
    if args.preview:
        config.preview = True
    if args.preview_duration is not None:
        config.preview_duration_s = args.preview_duration
    if args.preview_start is not None:
        config.preview_start_s = args.preview_start
    if args.mastering_eq is not None:
        config.mastering_eq_profile = args.mastering_eq
    if args.mastering_eq_strength is not None:
        config.mastering_eq_strength = max(0.0, min(1.0, args.mastering_eq_strength))
    if args.mastering_comp is not None:
        config.mastering_comp_profile = args.mastering_comp
    if args.mastering_comp_threshold is not None:
        config.mastering_comp_threshold_db = args.mastering_comp_threshold
    if args.mastering_comp_ratio is not None:
        config.mastering_comp_ratio = args.mastering_comp_ratio
    if args.mastering_comp_attack is not None:
        config.mastering_comp_attack_ms = args.mastering_comp_attack
    if args.mastering_comp_release is not None:
        config.mastering_comp_release_ms = args.mastering_comp_release
    if args.mastering_comp_makeup is not None:
        config.mastering_comp_makeup_db = args.mastering_comp_makeup
    if args.mastering_comp_sidechain_hpf is not None:
        config.mastering_comp_sidechain_hpf_hz = args.mastering_comp_sidechain_hpf
    if args.mastering_bass is not None:
        config.mastering_bass_profile = args.mastering_bass
    if args.mastering_bass_sub is not None:
        config.mastering_bass_sub_gain_db = args.mastering_bass_sub
    if args.mastering_bass_mid is not None:
        config.mastering_bass_mid_gain_db = args.mastering_bass_mid
    if args.mastering_bass_unify is not None:
        config.mastering_bass_unify_hz = args.mastering_bass_unify
    if args.mastering_bass_spread is not None:
        config.mastering_bass_spread = args.mastering_bass_spread
    if args.mastering_bass_punch is not None:
        config.mastering_bass_punch = max(-1.0, min(1.0, args.mastering_bass_punch))
    if args.mastering_bass_excite:
        config.mastering_bass_excite = True
    if args.mastering_bass_lfe_mode is not None:
        config.mastering_bass_lfe_mode = args.mastering_bass_lfe_mode
    if args.mastering_bass_lfe_send is not None:
        config.mastering_bass_lfe_send = max(0.0, min(1.0, args.mastering_bass_lfe_send))
    if args.mastering_bass_lfe is not None:
        config.mastering_bass_lfe_gain_db = args.mastering_bass_lfe
    if args.mastering_bass_decorrelate is not None:
        config.mastering_bass_decorrelate = max(0.0, min(1.0, args.mastering_bass_decorrelate))
    if args.match_reference is not None:
        config.mastering_match_ref_path = args.match_reference
    if args.match_reference_strength is not None:
        config.mastering_match_ref_strength = max(0.0, min(1.0, args.match_reference_strength))
    if args.no_match_reference_spectrum:
        config.mastering_match_ref_spectrum = False
    if args.no_match_reference_rms:
        config.mastering_match_ref_rms = False
    if args.match_reference_max_db is not None:
        config.mastering_match_ref_max_db = args.match_reference_max_db
    if args.stem_rebalance is not None:
        config.stem_rebalance = _parse_key_value_pairs(args.stem_rebalance, float)
    if args.stem_rebalance_profile is not None:
        from upmixer.separation.stem_rebalance import REBALANCE_PROFILES
        if args.stem_rebalance_profile not in REBALANCE_PROFILES:
            raise SystemExit(
                f"Unknown stem rebalance profile '{args.stem_rebalance_profile}'. "
                f"Valid choices: {sorted(REBALANCE_PROFILES.keys())}"
            )
        if config.stem_rebalance is None:
            config.stem_rebalance = REBALANCE_PROFILES[args.stem_rebalance_profile]
    if args.stem_eq is not None:
        config.stem_eq_profiles = _parse_key_value_pairs(args.stem_eq, str)
    if args.stem_lfe is not None:
        lfe_sends = _parse_key_value_pairs(args.stem_lfe, float)
        for stem, amount in lfe_sends.items():
            if not math.isfinite(amount) or amount < 0.0:
                raise SystemExit(f"--stem-lfe amount for '{stem}' must be finite and non-negative, got {amount}.")
        if config.stem_routing is None:
            config.stem_routing = {}
        for stem, amount in lfe_sends.items():
            config.stem_routing.setdefault(stem, {})["LFE"] = amount
    if args.stem_pan is not None:
        pans = _parse_key_value_pairs(args.stem_pan, float)
        for stem, pan in pans.items():
            if not math.isfinite(pan) or not 0.0 <= pan <= 1.0:
                raise SystemExit(f"--stem-pan value for '{stem}' must be between 0.0 and 1.0, got {pan}.")
        if config.stem_routing is None:
            config.stem_routing = {}
        for stem, pan in pans.items():
            config.stem_routing[stem] = apply_stem_pan(config.stem_routing.get(stem, {}), pan)
    if args.stem_cache_dir is not None:
        config.stem_cache_dir = args.stem_cache_dir
    if args.stem_batch_size is not None:
        config.stem_batch_size = args.stem_batch_size
    if args.stem_segment_size is not None:
        config.stem_segment_size = args.stem_segment_size
    if args.stem_chunk_duration_s is not None:
        config.stem_chunk_duration_s = args.stem_chunk_duration_s
    if args.stem_model_cache_size is not None:
        config.stem_model_cache_size = args.stem_model_cache_size
    if args.stem_silence_skip is not None:
        config.stem_silence_skip = args.stem_silence_skip
    if args.stem_silence_threshold_db is not None:
        config.stem_silence_threshold_db = args.stem_silence_threshold_db
    if args.stem_silence_min_duration_s is not None:
        config.stem_silence_min_duration_s = args.stem_silence_min_duration_s
    if args.stem_silence_crossfade_ms is not None:
        config.stem_silence_crossfade_ms = args.stem_silence_crossfade_ms
    if args.stem_silence_pad_ms is not None:
        config.stem_silence_pad_ms = args.stem_silence_pad_ms
    if args.stem_source_anchor_strength is not None:
        config.stem_source_anchor_strength = args.stem_source_anchor_strength
    if args.stem_bleed_reduction is not None:
        config.stem_bleed_reduction = args.stem_bleed_reduction
    if args.stem_phase_fix_low_hz is not None:
        config.stem_phase_fix_low_hz = args.stem_phase_fix_low_hz
    if args.stem_phase_fix_high_hz is not None:
        config.stem_phase_fix_high_hz = args.stem_phase_fix_high_hz
    if args.stem_phase_fix_scale is not None:
        config.stem_phase_fix_scale = args.stem_phase_fix_scale
    if args.stem_phase_fix_reference_model is not None:
        config.stem_phase_fix_reference_model = args.stem_phase_fix_reference_model
    if args.stem_debleed_model is not None:
        config.stem_debleed_model = args.stem_debleed_model
    if args.stem_drum_remask is not None:
        config.stem_drum_remask = args.stem_drum_remask
    if args.stem_primary_remask is not None:
        config.stem_primary_remask = args.stem_primary_remask
    if args.stem_wet_dry_split is not None:
        config.stem_wet_dry_split = args.stem_wet_dry_split
    if args.stem_dereverb_model is not None:
        config.stem_dereverb_model = args.stem_dereverb_model
    if args.stem_wet_denoise is not None:
        config.stem_wet_denoise = args.stem_wet_denoise
    if args.stems is not None:
        from upmixer.separation.stem_plan import normalize_stems as _normalize
        raw = [s.strip() for s in args.stems.split(",") if s.strip()]
        config.stems = _normalize(raw)


def _parse_key_value_pairs(s: str, value_type: type) -> dict:
    """Parse ``"Key1=val1,Key2=val2"`` into a typed dict.

    Used for ``--stem-rebalance`` and ``--stem-eq`` CLI arguments.

    Examples::

        _parse_key_value_pairs("Vocals=+2.0,Drums=-1.0", float)
        # → {"Vocals": 2.0, "Drums": -1.0}

        _parse_key_value_pairs("Vocals=vocal-presence", str)
        # → {"Vocals": "vocal-presence"}
    """
    result: dict = {}
    for pair in s.split(","):
        pair = pair.strip()
        if "=" not in pair:
            raise SystemExit(
                f"Invalid key=value pair in '{s}'. "
                "Expected format: 'Key1=val1,Key2=val2'."
            )
        k, v = pair.split("=", 1)
        result[k.strip()] = value_type(v.strip())
    return result


def _apply_resource_limits(cpu_priority: str) -> None:
    """Apply scheduling and numeric-library thread limits."""
    import os
    effective = "normal" if cpu_priority == "auto" else cpu_priority
    if effective == "low":
        try:
            os.nice(10)
        except (OSError, AttributeError):
            pass
    n_cpu = max(1, os.cpu_count() or 4)
    n = n_cpu if effective == "normal" else max(1, n_cpu // 2)
    try:
        import torch
        torch.set_num_threads(n)
        try:
            set_interop_threads = getattr(torch, "set_num_interop_threads")
            set_interop_threads(1)
        except (AttributeError, RuntimeError):
            pass
    except ImportError:
        pass
    try:
        from threadpoolctl import threadpool_limits
        threadpool_limits(limits=n)
    except ImportError:
        pass
