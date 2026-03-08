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
    coherence_smoothing: float = 0.6
    epsilon: float = 1e-10

    # Soft matrix parameters
    center_extraction_gain: float = 0.7
    center_attenuation: float = 0.3

    # Channel routing gains (linear)
    center_gain: float = 0.85
    lfe_gain: float = 0.5
    surround_gain: float = 0.6
    back_gain: float = 0.4
    height_gain: float = 0.45

    # LFE parameters
    lfe_cutoff_hz: float = 120.0
    lfe_filter_order: int = 4

    # Decorrelation parameters
    decorr_filter_length: int = 4096
    decorr_seed: int = 42
    decorr_n_filters: int = 8

    # Back channel parameters
    back_delay_ms: float = 15.0

    # Height channel parameters
    height_crossover_hz: float = 3000.0
    height_transition_width_hz: float = 1000.0
    height_max_gain: float = 0.7
    height_back_delay_ms: float = 10.0

    # Output format
    output_format: str = "5.1"
    output_subtype: str = "PCM_24"
    sample_rate: int | None = None

    # Post-processing
    normalize_output: bool = True
    peak_limit_threshold: float = 0.95

    def resolve_fft_params(self, actual_sample_rate: int) -> tuple[int, int]:
        """Returns (fft_size, hop_size) after applying sample rate adaptation."""
        if self.auto_fft_size:
            fft = _auto_fft_size(actual_sample_rate)
            return fft, fft // 4
        return self.fft_size, self.hop_size
