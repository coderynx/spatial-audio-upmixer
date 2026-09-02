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

    center_gain: float = 0.85
    # BS.775-4 Annex 7: LFE programme level is 10 dB below full-range beds.
    lfe_gain: float = 0.31622776601683794
    surround_gain: float = 0.6
    back_gain: float = 0.55
    height_gain: float = 0.55

    lfe_cutoff_hz: float = 120.0
    lfe_filter_order: int = 4

    surround_bass_cutoff_hz: float = 250.0

    height_low_rolloff_hz: float = 150.0
    height_low_rolloff_gain: float = 0.15
    height_crossover_hz: float = 3000.0
    height_high_shelf_gain: float = 1.5
    height_directional_band_hz: float = 8000.0
    height_directional_band_gain: float = 1.0

    output_format: str = "5.1"
    output_subtype: str = "PCM_24"
    # Export-tail bit-depth reduction: off (round only), tpdf, or shaped.
    # Integer PCM subtypes only; see docs/standards/loudness_dsp_bs1770.md.
    output_dither: str = "tpdf"
    # Fixed so two renders of the same job are byte-identical.
    output_dither_seed: int = 20260819
    output_type: str = "multichannel"
    # Delivery-profile label is descriptive; its layout/format/mastering
    # settings remain the executable delivery contract.
    delivery_profile: str | None = None
    output_codec: str = "wav_pcm"
    sample_rate: int | None = None
    output_sample_rate: int | None = None

    normalize_output: bool = True
    peak_limit_threshold: float = 0.95

    # See docs/standards/spatial_audio_engine.md.
    binaural_profile: str = "studio"

    # See docs/standards/transaural_speakers.md.
    transaural_profile: str = "stereo"

    surround_downmix_coeff: float = 0.7071
    # BS.775 has no height coefficient; see docs/standards/spatial_layouts_bs775_bs2051.md.
    height_downmix_coeff: float = 0.7071

    downmix_enabled: bool = False
    loudness_normalize: bool = True
    # Unset defers to the named delivery target, and with none named to the
    # Dolby Atmos Music pair these two have always defaulted to; see
    # upmixer.mastering.delivery.
    loudness_target_preset: str | None = None
    loudness_target_lkfs: float | None = None
    loudness_max_tp: float | None = None
    loudness_max_gain_db: float = 30.0

    # Unset renders the binaural QC programme only for height-bearing beds;
    # see docs/standards/spatial_layouts_bs775_bs2051.md §"Fold QC thresholds".
    qc_measure_binaural: bool | None = None

    limiter_lookahead_ms: float = 5.0
    limiter_release_ms: float = 50.0

    downmix_output_path: str | None = None

    preview: bool = False
    preview_duration_s: float = 30.0
    preview_start_s: float | None = None

    mastering_highpass_enabled: bool = False
    mastering_highpass_hz: float = 20.0

    mastering_clip_enabled: bool = False
    mastering_clip_db: float = 0.5
    mastering_clip_knee: float = 1.0

    mastering_eq_profile: str | None = None
    mastering_eq_strength: float = 1.0

    mastering_dyneq_profile: str | None = None
    # One dict per bell band: freq_hz, q, threshold_db, ratio, attack_ms,
    # release_ms.  Overrides the profile; with both unset the stage is out of
    # the chain entirely.
    mastering_dyneq_bands: list[dict] | None = None

    mastering_comp_profile: str | None = None
    mastering_comp_threshold_db: float | None = None
    mastering_comp_ratio: float | None = None
    mastering_comp_attack_ms: float | None = None
    mastering_comp_release_ms: float | None = None
    mastering_comp_knee_db: float | None = None
    mastering_comp_makeup_db: float | None = None
    mastering_comp_sidechain_hpf_hz: float | None = None

    # None preserves the legacy profile/override activation rules; False is
    # an explicit module bypass that keeps tuned values available to the UI.
    mastering_bass_enabled: bool | None = None
    mastering_bass_profile: str | None = None
    mastering_bass_sub_gain_db: float | None = None
    mastering_bass_mid_gain_db: float | None = None
    mastering_bass_unify_hz: float | None = None
    mastering_bass_spread: str | None = None
    mastering_bass_punch: float | None = None
    mastering_bass_harmonics: float | None = None
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
    mastering_match_ref_smooth_oct: float | None = None
    mastering_match_ref_low_hz: float | None = None
    mastering_match_ref_high_hz: float | None = None

    bed_trim_db: float = 0.0
    stem_rebalance: dict | None = None

    stem_eq_profiles: dict | None = None
    stem_dynamic_eq: dict | None = None
    stem_dynamics: dict | None = None

    # Per-stem ambient sends: how much of the stem's ambient half reaches the
    # surround speakers, and the height speakers.  Keyed like stem_rebalance.
    stem_ambient_rear: dict | None = None
    stem_ambient_height: dict | None = None
    stem_ambient_height_crossover_hz: dict | None = None
    spatial_downmix_lock: bool = False
    spatial_render_model: str = "object-bed"
    stem_object_mode: dict | None = None
    stem_object_metadata: dict | None = None

    # Explicit per-stem speaker-bed routing.  Each value maps a canonical stem
    # name (or ``Stem@zone`` key) to output channel weights.
    stem_routing: dict | None = None
    stem_placement: dict | None = None

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

    # Fixed DSP cleanup over the separator's vocal/instrumental estimates.
    # Baked into cached stems; default-off until corpus and listening gates pass.
    stem_bleed_reduction: bool = False

    # Share the remainder each split leaves on its parent back over the
    # children, so they sum to it. Full re-projection was measured worse on
    # SDR, fullness and bleedless at every exponent for both stages
    # (docs/reports/primary_remask.md, docs/reports/drum_remask.md).
    stem_drum_remask: bool = True
    stem_primary_remask: bool = True

    def resolve_fft_params(self, actual_sample_rate: int) -> tuple[int, int]:
        """Returns (fft_size, hop_size) after applying sample rate adaptation."""
        if self.auto_fft_size:
            fft = _auto_fft_size(actual_sample_rate)
            return fft, fft // 4
        return self.fft_size, self.hop_size
