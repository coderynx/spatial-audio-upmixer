import numpy as np

from upmixer.analysis.coherence import CoherenceEstimator, CoherenceState
from upmixer.analysis.stft import StreamingSTFT
from upmixer.config import UpmixConfig
from upmixer.decomposition.direct_ambient import SoftMatrixDecomposer
from upmixer.formats import FORMAT_MAP
from upmixer.io.reader import AudioReader
from upmixer.io.writer import AudioWriter
from upmixer.routing.channel_router import ChannelRouter
from upmixer.utils import normalize_energy, soft_limit


class StreamingProcessor:
    """Stateful streaming upmix processor.

    Holds all inter-block state (STFT overlap buffers, coherence accumulators).
    Call process_block() repeatedly with chunks of stereo audio.

    This is the class that a GStreamer element would wrap.
    """

    def __init__(self, config: UpmixConfig, sample_rate: int):
        self._config = config
        self._sample_rate = sample_rate

        fft_size, hop_size = config.resolve_fft_params(sample_rate)
        self._hop_size = hop_size

        # Streaming STFT instances (one per input channel)
        self._stft_L = StreamingSTFT(config, sample_rate)
        self._stft_R = StreamingSTFT(config, sample_rate)

        # Output STFT instances (one per output channel)
        self._format = FORMAT_MAP[config.output_format]
        self._stft_out: dict[str, StreamingSTFT] = {
            label.value: StreamingSTFT(config, sample_rate)
            for label in self._format.channels
        }

        # Analysis components
        n_freq = fft_size // 2 + 1
        self._coherence_est = CoherenceEstimator(config)
        self._coherence_state = self._coherence_est.create_state(n_freq)

        # Decomposition and routing
        self._decomposer = SoftMatrixDecomposer(config)
        self._router = ChannelRouter(config, sample_rate, n_freq)

        # Input buffering (accumulate until we have hop_size samples)
        self._input_buffer_L = np.zeros(0)
        self._input_buffer_R = np.zeros(0)

    def process_block(
        self, left: np.ndarray, right: np.ndarray
    ) -> dict[str, np.ndarray]:
        """Process a block of stereo audio, return multichannel output.

        Args:
            left: 1D array of left channel samples (any length).
            right: 1D array of right channel samples (same length).

        Returns:
            Dict mapping channel name -> 1D array of output samples.
        """
        self._input_buffer_L = np.concatenate([self._input_buffer_L, left])
        self._input_buffer_R = np.concatenate([self._input_buffer_R, right])

        hop = self._hop_size
        output_chunks: dict[str, list[np.ndarray]] = {
            ch: [] for ch in self._stft_out
        }

        while len(self._input_buffer_L) >= hop:
            hop_L = self._input_buffer_L[:hop]
            hop_R = self._input_buffer_R[:hop]
            self._input_buffer_L = self._input_buffer_L[hop:]
            self._input_buffer_R = self._input_buffer_R[hop:]

            X_L = self._stft_L.analyze_frame(hop_L)
            X_R = self._stft_R.analyze_frame(hop_R)

            if X_L is None or X_R is None:
                # Still filling initial STFT buffer
                for ch in output_chunks:
                    output_chunks[ch].append(np.zeros(hop))
                continue

            # Coherence estimation (updates state in place)
            coherence = self._coherence_est.estimate_frame(
                X_L, X_R, self._coherence_state
            )

            # Soft matrix decomposition
            decomp = self._decomposer.decompose_frame(X_L, X_R, coherence)

            # Mid signal for LFE
            mid_frame = (X_L + X_R) * 0.5

            # Route to output channels
            channel_spectra = self._router.route_frame(decomp, mid_frame)

            # Synthesize each channel
            for ch_name, spectrum in channel_spectra.items():
                samples = self._stft_out[ch_name].synthesize_frame(spectrum)
                output_chunks[ch_name].append(samples)

        result = {}
        for ch_name, chunks in output_chunks.items():
            if chunks:
                result[ch_name] = np.concatenate(chunks)
            else:
                result[ch_name] = np.zeros(0)

        return result

    def flush(self) -> dict[str, np.ndarray]:
        """Flush remaining samples by padding with zeros."""
        remaining = self._hop_size - len(self._input_buffer_L)
        if remaining > 0 and len(self._input_buffer_L) > 0:
            return self.process_block(np.zeros(remaining), np.zeros(remaining))
        return {ch: np.zeros(0) for ch in self._stft_out}

    @property
    def latency_samples(self) -> int:
        return self._stft_L.latency_samples

    def reset(self) -> None:
        self._stft_L.reset()
        self._stft_R.reset()
        for stft in self._stft_out.values():
            stft.reset()
        n_freq = self._stft_L.n_freq_bins
        self._coherence_state = self._coherence_est.create_state(n_freq)
        self._input_buffer_L = np.zeros(0)
        self._input_buffer_R = np.zeros(0)


class UpmixPipeline:
    """Top-level orchestrator for file-based processing using streaming internals."""

    def __init__(self, config: UpmixConfig | None = None):
        self.config = config or UpmixConfig()

    def process_file(self, input_path: str, output_path: str) -> None:
        """Full file-based processing pipeline using streaming blocks."""
        cfg = self.config

        # 1. Read input
        reader = AudioReader(input_path)
        audio, sr = reader.read()
        left = audio[:, 0]
        right = audio[:, 1]
        n_samples = len(left)

        fft_size, hop_size = cfg.resolve_fft_params(sr)

        print(f"Input: {input_path}")
        print(f"  Sample rate: {sr} Hz")
        print(f"  Duration: {n_samples / sr:.2f}s ({n_samples} samples)")
        print(f"  Output format: {cfg.output_format}")
        print(f"  FFT size: {fft_size}, hop: {hop_size}")

        # 2. Create streaming processor
        processor = StreamingProcessor(cfg, sr)

        # 3. Process in blocks
        block_size = cfg.block_size
        fmt = FORMAT_MAP[cfg.output_format]
        channel_names = [label.value for label in fmt.channels]
        all_outputs: dict[str, list[np.ndarray]] = {ch: [] for ch in channel_names}

        # Streaming latency: the input STFT needs fft_size - hop_size
        # samples before producing the first spectrum frame.
        latency = fft_size - hop_size

        print("  Processing...")
        for start in range(0, n_samples, block_size):
            end = min(start + block_size, n_samples)
            block_out = processor.process_block(left[start:end], right[start:end])
            for ch_name, samples in block_out.items():
                all_outputs[ch_name].append(samples)

        # Feed extra zeros to flush remaining audio through the pipeline
        tail_zeros = np.zeros(latency + fft_size)
        tail_out = processor.process_block(tail_zeros, tail_zeros)
        for ch_name, samples in tail_out.items():
            all_outputs[ch_name].append(samples)

        # Flush any partial hop
        flush_out = processor.flush()
        for ch_name, samples in flush_out.items():
            if len(samples) > 0:
                all_outputs[ch_name].append(samples)

        # 4. Concatenate, compensate latency, and trim to original length
        channels = {}
        for ch_name, chunks in all_outputs.items():
            full = np.concatenate(chunks)
            # Remove latency (initial zeros from STFT fill-up)
            full = full[latency:]
            if len(full) > n_samples:
                channels[ch_name] = full[:n_samples]
            elif len(full) < n_samples:
                channels[ch_name] = np.pad(full, (0, n_samples - len(full)))
            else:
                channels[ch_name] = full

        # 5. Time-domain post-processing
        channels = self._post_process(channels, sr, left, right)

        # 6. Write output
        writer = AudioWriter(output_path, sr, cfg)
        writer.write(channels)
        print(f"Output: {output_path}")

    def _post_process(
        self,
        channels: dict[str, np.ndarray],
        sr: int,
        original_left: np.ndarray,
        original_right: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """Time-domain post-processing: delay, normalization, limiting."""
        cfg = self.config
        fmt = FORMAT_MAP[cfg.output_format]
        n_samples = len(original_left)

        # Apply delay to back channels
        if fmt.has_back:
            delay_samples = int(cfg.back_delay_ms * sr / 1000.0)
            for ch_name in ("BL", "BR"):
                if ch_name in channels:
                    channels[ch_name] = np.pad(
                        channels[ch_name], (delay_samples, 0)
                    )[:n_samples]

        # Apply delay to top back channels
        if fmt.n_height_channels == 4:
            delay_samples = int(cfg.height_back_delay_ms * sr / 1000.0)
            for ch_name in ("TBL", "TBR"):
                if ch_name in channels:
                    channels[ch_name] = np.pad(
                        channels[ch_name], (delay_samples, 0)
                    )[:n_samples]

        # Energy normalization
        if cfg.normalize_output:
            channels = normalize_energy(channels, original_left, original_right)

        # Peak limiting
        for name in channels:
            channels[name] = soft_limit(channels[name], cfg.peak_limit_threshold)

        return channels
