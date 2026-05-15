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

    # STFT parameters
    fft_size: int = 4096
    hop_size: int = 1024
    window_type: str = "hann"
    auto_fft_size: bool = True

    # Streaming parameters
    block_size: int = 4096

    # Analysis parameters
    coherence_smoothing: float = 0.6       # batch path
    coherence_attack_alpha: float = 0.25   # streaming: fast track when coherence rises
    coherence_release_alpha: float = 0.75  # streaming: slow release when coherence falls
    epsilon: float = 1e-10

    # Soft matrix parameters
    center_extraction_gain: float = 0.85
    center_attenuation: float = 0.5

    # Channel routing gains (linear)
    center_gain: float = 0.85
    lfe_gain: float = 0.5
    surround_gain: float = 0.6
    back_gain: float = 0.55
    height_gain: float = 0.55

    # LFE parameters
    lfe_cutoff_hz: float = 120.0
    lfe_filter_order: int = 4

    # Spatial analysis parameters
    surround_bass_cutoff_hz: float = 250.0  # bass below this stays in front, not surround
    transient_gate_min: float = 0.15        # minimum surround gain during transients (batch path)

    # Per-band transient detection (streaming path)
    transient_n_bands: int = 10            # octave bands from 20 Hz to Nyquist
    transient_ema_alpha: float = 0.85      # per-band flux history EMA
    transient_sensitivity_k: float = 2.5  # flux must exceed k × EMA to score 1.0

    # Harmonicity — HPSS-inspired spectral floor
    harmonic_median_half_width: int = 8    # ±k bins sliding median window
    harmonic_smoothing_alpha: float = 0.7  # temporal EMA to suppress frame-to-frame flicker

    # Height channel parameters — elevation EQ
    # Sub-bass rolloff: below low_rolloff_hz → attenuated
    # Flat mids: full body preserved
    # High shelf: above crossover_hz → boosted for elevation HRTF cues
    height_low_rolloff_hz: float = 150.0
    height_low_rolloff_gain: float = 0.15
    height_crossover_hz: float = 3000.0
    height_transition_width_hz: float = 2000.0
    height_high_shelf_gain: float = 1.5

    # Output format
    output_format: str = "5.1"
    output_subtype: str = "PCM_24"
    output_type: str = "wav"   # "wav" or "adm-bwf"
    sample_rate: int | None = None
    output_sample_rate: int | None = None

    # Post-processing
    normalize_output: bool = True
    peak_limit_threshold: float = 0.95

    # Content-aware mixing (stem analysis → spatial routing modulation)
    content_aware_mixing: bool = True
    content_mix_strength: float = 1.0    # [0=neutral, 1=full content-aware]
    content_hf_analysis_hz: float = 4000.0  # lower edge of HF band for air detection

    # Loudness normalization (ITU-R BS.1770-4 / Dolby DEE compliance)
    loudness_normalize: bool = True
    loudness_target_lkfs: float = -24.0   # Dolby Atmos home / cinema
    loudness_max_tp: float = -2.0          # Dolby True Peak ceiling
    loudness_max_gain_db: float = 30.0     # cap upward gain (prevent noise amplification)

    def resolve_fft_params(self, actual_sample_rate: int) -> tuple[int, int]:
        """Returns (fft_size, hop_size) after applying sample rate adaptation."""
        if self.auto_fft_size:
            fft = _auto_fft_size(actual_sample_rate)
            return fft, fft // 4
        return self.fft_size, self.hop_size
