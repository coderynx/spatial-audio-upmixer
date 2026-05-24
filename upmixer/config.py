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
    lfe_gain: float = 0.5
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
    height_transition_width_hz: float = 2000.0
    height_high_shelf_gain: float = 1.5

    output_format: str = "5.1"
    output_subtype: str = "PCM_24"
    output_type: str = "wav"
    sample_rate: int | None = None
    output_sample_rate: int | None = None

    normalize_output: bool = True
    peak_limit_threshold: float = 0.95

    content_mix_strength: float = 1.0
    content_hf_analysis_hz: float = 4000.0

    surround_downmix_coeff: float = 0.7071

    loudness_normalize: bool = True
    loudness_target_lkfs: float = -18.0
    loudness_max_tp: float = -1.0
    loudness_max_gain_db: float = 30.0

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

    mastering_bass_profile: str | None = None
    mastering_bass_sub_gain_db: float | None = None
    mastering_bass_mid_gain_db: float | None = None
    mastering_bass_mono_cutoff_hz: float | None = None
    mastering_bass_excite: bool = False
    mastering_bass_lfe_gain_db: float | None = None

    mastering_match_ref_path: str | None = None
    mastering_match_ref_strength: float = 0.7
    mastering_match_ref_spectrum: bool = True
    mastering_match_ref_rms: bool = True
    mastering_match_ref_max_db: float = 12.0

    stem_rebalance: dict | None = None

    stem_eq_profiles: dict | None = None

    stem_cache_dir: str | None = None

    stems: list[str] | None = None

    def resolve_fft_params(self, actual_sample_rate: int) -> tuple[int, int]:
        """Returns (fft_size, hop_size) after applying sample rate adaptation."""
        if self.auto_fft_size:
            fft = _auto_fft_size(actual_sample_rate)
            return fft, fft // 4
        return self.fft_size, self.hop_size
