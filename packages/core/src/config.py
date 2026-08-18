from dataclasses import dataclass


def _auto_fft_size(sample_rate: int, target_resolution_hz: float = 10.8) -> int:
    """Select FFT size to maintain consistent frequency resolution across sample rates."""
    target = int(sample_rate / target_resolution_hz)
    power = 1
    while power < target:
        power <<= 1
    return min(power, 16384)


@dataclass
class UpmixConfig:
    """All tunable parameters for the upmix pipeline."""

    fft_size: int = 4096
    hop_size: int = 1024
    window_type: str = "hann"
    auto_fft_size: bool = True

    block_size: int = 4096

    coherence_smoothing: float = 0.6
    coherence_attack_alpha: float = 0.25
    coherence_release_alpha: float = 0.75
    epsilon: float = 1e-10

    center_extraction_gain: float = 0.85
    center_attenuation: float = 0.5

    center_gain: float = 0.85
    # BS.775-4 Annex 7: LFE programme level is 10 dB below full-range beds.
    lfe_gain: float = 0.31622776601683794
    surround_gain: float = 0.6
    back_gain: float = 0.55
    height_gain: float = 0.55

    lfe_cutoff_hz: float = 120.0
    lfe_filter_order: int = 4

    surround_bass_cutoff_hz: float = 250.0
    transient_gate_min: float = 0.15

    transient_n_bands: int = 10
    transient_ema_alpha: float = 0.85
    transient_sensitivity_k: float = 2.5

    harmonic_median_half_width: int = 8
    harmonic_smoothing_alpha: float = 0.7

    height_low_rolloff_hz: float = 150.0
    height_low_rolloff_gain: float = 0.15
    height_crossover_hz: float = 3000.0
    height_high_shelf_gain: float = 1.5
    height_directional_band_hz: float = 8000.0
    height_directional_band_gain: float = 1.0

    stem_transient_duck: float = 0.0

    output_format: str = "5.1"
    output_subtype: str = "PCM_24"
    output_type: str = "multichannel"
    output_codec: str = "wav_pcm"
    sample_rate: int | None = None
    output_sample_rate: int | None = None

    normalize_output: bool = True
    peak_limit_threshold: float = 0.95

    content_mix_strength: float = 1.0
    content_hf_analysis_hz: float = 4000.0

    # ``auto`` selects a content-led profile per file; live StreamingProcessor
    # callers without a pre-analysis plan stay deliberately conservative.
    spatial_profile: str = "auto"
    spatial_intensity: float = 1.0
    spatial_preanalysis: bool = True

    # See docs/standards/spatial_audio_engine.md.
    binaural_profile: str = "studio"

    # See docs/standards/transaural_speakers.md.
    transaural_profile: str = "stereo"

    surround_downmix_coeff: float = 0.7071
    # BS.775 has no height coefficient; see docs/standards/spatial_layouts_bs775_bs2051.md.
    height_downmix_coeff: float = 0.7071

    downmix_enabled: bool = False
    loudness_normalize: bool = True
    loudness_target_lkfs: float = -18.0
    loudness_max_tp: float = -1.0
    loudness_max_gain_db: float = 30.0

    limiter_lookahead_ms: float = 5.0
    limiter_release_ms: float = 50.0

    downmix_output_path: str | None = None

    preview: bool = False
    preview_duration_s: float = 30.0
    preview_start_s: float | None = None

    mastering_eq_profile: str | None = None
    mastering_eq_strength: float = 1.0

    mastering_comp_profile: str | None = None
    mastering_comp_threshold_db: float | None = None
    mastering_comp_ratio: float | None = None
    mastering_comp_attack_ms: float | None = None
    mastering_comp_release_ms: float | None = None
    mastering_comp_knee_db: float | None = None
    mastering_comp_makeup_db: float | None = None
    mastering_comp_sidechain_hpf_hz: float | None = None

    mastering_bass_profile: str | None = None
    mastering_bass_sub_gain_db: float | None = None
    mastering_bass_mid_gain_db: float | None = None
    mastering_bass_unify_hz: float | None = None
    mastering_bass_spread: str | None = None
    mastering_bass_punch: float | None = None
    # Tri-state like every other bass override: None = take the profile's
    # value, True/False = force it. A plain bool could not express "the
    # profile turns the exciter on and the user turned it back off".
    mastering_bass_excite: bool | None = None
    mastering_bass_lfe_mode: str | None = None
    mastering_bass_lfe_send: float | None = None
    mastering_bass_lfe_gain_db: float | None = None
    mastering_bass_decorrelate: float | None = None

    mastering_match_ref_path: str | None = None
    mastering_match_ref_strength: float = 0.7
    mastering_match_ref_spectrum: bool = True
    mastering_match_ref_rms: bool = True
    mastering_match_ref_max_db: float = 6.0

    stem_rebalance: dict | None = None

    stem_eq_profiles: dict | None = None

    # Explicit per-stem speaker-bed routing.  Each value maps a canonical stem
    # name (or ``Stem@zone`` key) to output channel weights.
    stem_routing: dict | None = None

    # Explicit per-stem on/off state.  Missing stems remain enabled.
    stem_enabled: dict | None = None

    # Optional solo stems.  When set, only these stems are routed.
    stem_solo: list[str] | None = None

    stem_cache_dir: str | None = None

    # Stable identity to key stem-cache entries by instead of the input file's
    # resolved filesystem path. A caller whose stem_cache_dir already isolates
    # one cache entry per logical source (e.g. one directory per project
    # track) can set this so relocating the data/working directory doesn't
    # orphan previously separated stems.
    stem_cache_key: str | None = None

    # Write separated stems to a plain per-caller directory after separation,
    # with no cache-identity gating (unlike stem_cache_dir). For a caller that
    # already owns one directory per logical source (e.g. a project track).
    stem_output_dir: str | None = None

    # Load pre-separated stems from a plain directory written by
    # stem_output_dir, skipping inference and stem_cache_dir entirely.
    stem_input_dir: str | None = None

    # None selects conservative backend-aware inference batching.
    stem_batch_size: int | None = None
    stem_segment_size: int | None = None
    stem_chunk_duration_s: float | None = None
    stem_model_cache_size: int | None = None

    # None selects the community-default overlap (2 windows per chunk).
    stem_overlap: int | None = None
    # Test-time augmentation: average predictions over polarity/channel
    # variants. Off by default (~3x inference cost when enabled).
    stem_tta: bool = False
    # Pitch-register rescue trick: resample by this ratio before separation
    # and back afterward. None disables it.
    stem_pitch_shift: float | None = None

    stems: list[str] | None = None

    stem_silence_skip: bool = True
    stem_silence_threshold_db: float = -90.0
    stem_silence_min_duration_s: float = 2.0
    stem_silence_crossfade_ms: float = 10.0
    stem_silence_pad_ms: float = 200.0

    stem_source_anchor_strength: float = 0.5

    # Baked into the cached stems at separation time, so the default gate keys
    # on a stem's default spatial role, not on any later user 3D placement.
    # Per-stem dicts override per canonical stem name (or "*"). See the
    # knowledge base's techniques/phase_and_bleed.md.
    stem_bleed_reduction: bool = False
    stem_phase_fix: dict | None = None
    stem_phase_fix_low_hz: float = 500.0
    stem_phase_fix_high_hz: float = 5000.0
    stem_phase_fix_scale: float = 0.8
    stem_phase_fix_reference_model: str = "kimmel_unwa_ft2_bleedless.ckpt"
    stem_debleed: dict | None = None
    stem_debleed_model: str = "mel_band_roformer_bleed_suppressor_v1.ckpt"

    # Share the remainder each split leaves on its parent back over the
    # children, so they sum to it. Full re-projection was measured worse on
    # SDR, fullness and bleedless at every exponent for both stages
    # (docs/reports/primary_remask.md, docs/reports/drum_remask.md).
    stem_drum_remask: bool = True
    stem_primary_remask: bool = True

    # Split the vocal stem into a dry stem and a wet "Vocals Reverb" stem the
    # router places surround/height-heavy. The wet stem is the dereverb model's
    # residual against its own input, so the pair nulls against the parent.
    # Costs one model download (GPL weights) and one inference stage per zone.
    stem_wet_dry_split: bool = False
    stem_dereverb_model: str = "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt"
    # Gentle denoise over the wet stem only: the residual carries whatever the
    # dereverb model got wrong, into the speakers where artifacts show most.
    stem_wet_denoise: bool = False

    def resolve_fft_params(self, actual_sample_rate: int) -> tuple[int, int]:
        """Returns (fft_size, hop_size) after applying sample rate adaptation."""
        if self.auto_fft_size:
            fft = _auto_fft_size(actual_sample_rate)
            return fft, fft // 4
        return self.fft_size, self.hop_size
