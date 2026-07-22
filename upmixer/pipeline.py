import logging
import math
import time
from typing import Callable

import numpy as np
from scipy.signal import resample_poly

from upmixer.analysis.coherence import CoherenceEstimator
from upmixer.analysis.spatial import SpatialPlan, analyze_spatial_plan
from upmixer.analysis.stft import StreamingSTFT
from upmixer.config import UpmixConfig
from upmixer.decomposition.direct_ambient import SoftMatrixDecomposer
from upmixer.formats import (
    FORMAT_MAP,
    INPUT_FORMAT_MAP,
    can_upmix,
    detect_input_format,
)
from upmixer.io.adm_writer import AdmBwfWriter
from upmixer.io.reader import AudioReader
from upmixer.io.writer import AudioWriter
from upmixer.mastering import MasteringChain
from upmixer.result import UpmixResult
from upmixer.routing.channel_router import ChannelRouter
from upmixer.utils import preview_slice, itu_downmix_stereo

_log = logging.getLogger("upmixer")


class _LinkedEnergyController:
    """Bound aggregate routed energy without file-wide lookahead."""

    def __init__(self, sample_rate: int, hop_size: int):
        self._attack_alpha = math.exp(-hop_size / (sample_rate * 0.020))
        self._release_alpha = math.exp(-hop_size / (sample_rate * 0.500))
        self._gain = 1.0

    def apply(
        self,
        spectra: dict[str, np.ndarray],
        source_left: np.ndarray,
        source_right: np.ndarray,
    ) -> dict[str, np.ndarray]:
        source_energy = float(
            np.vdot(source_left, source_left).real
            + np.vdot(source_right, source_right).real
        )
        routed_energy = float(sum(np.vdot(frame, frame).real for frame in spectra.values()))
        if source_energy <= 1e-20 or routed_energy <= 1e-20:
            return spectra

        target = math.sqrt(source_energy / routed_energy)
        target = float(np.clip(target, 10.0 ** (-6.0 / 20.0), 10.0 ** (6.0 / 20.0)))
        alpha = self._attack_alpha if target < self._gain else self._release_alpha
        self._gain = alpha * self._gain + (1.0 - alpha) * target
        return {name: frame * self._gain for name, frame in spectra.items()}

    def reset(self) -> None:
        self._gain = 1.0


class _AllPassDecorrelator:
    """First-order all-pass decorrelator for rear auxiliary sends."""

    def __init__(self, coefficient: float = 0.7):
        self._coefficient = coefficient
        self._previous_input = 0.0
        self._previous_output = 0.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        output = np.empty_like(samples)
        a = self._coefficient
        x_prev = self._previous_input
        y_prev = self._previous_output
        for i, sample in enumerate(samples):
            value = -a * sample + x_prev + a * y_prev
            output[i] = value
            x_prev = sample
            y_prev = value
        self._previous_input = x_prev
        self._previous_output = y_prev
        return output

    def reset(self) -> None:
        self._previous_input = 0.0
        self._previous_output = 0.0


class StreamingProcessor:
    """Stateful streaming upmix processor.

    Holds all inter-block state (STFT overlap buffers, coherence accumulators).
    Call process_block() repeatedly with chunks of stereo audio.

    This is the class that a GStreamer element would wrap.
    """

    def __init__(self, config: UpmixConfig, sample_rate: int, spatial_plan: SpatialPlan | None = None):
        self._config = config
        self._sample_rate = sample_rate

        fft_size, hop_size = config.resolve_fft_params(sample_rate)
        self._hop_size = hop_size

        self._stft_L = StreamingSTFT(config, sample_rate)
        self._stft_R = StreamingSTFT(config, sample_rate)

        self._format = FORMAT_MAP[config.output_format]
        self._stft_out: dict[str, StreamingSTFT] = {
            label.value: StreamingSTFT(config, sample_rate)
            for label in self._format.channels
        }

        n_freq = fft_size // 2 + 1
        self._coherence_est = CoherenceEstimator(config)
        self._coherence_state = self._coherence_est.create_state(n_freq)

        self._decomposer = SoftMatrixDecomposer(config, sample_rate=sample_rate, n_freq=n_freq)
        self._router = ChannelRouter(config, sample_rate, n_freq)
        self._energy_controller = (
            _LinkedEnergyController(sample_rate, hop_size) if config.normalize_output else None
        )
        self._rear_decorrelators = {
            name: _AllPassDecorrelator()
            for name in ("BL", "BR") if name in self._stft_out
        }
        self._spatial_plan = spatial_plan
        self._frame_index = 0

        self._input_buffer_L = np.zeros(0)
        self._input_buffer_R = np.zeros(0)
        self._flushed = False

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
        if self._flushed:
            raise RuntimeError("Cannot process more blocks after flush(); call reset() first")
        left = np.asarray(left, dtype=np.float64)
        right = np.asarray(right, dtype=np.float64)
        if left.ndim != 1 or right.ndim != 1:
            raise ValueError("left and right must be 1D arrays")
        if len(left) != len(right):
            raise ValueError("left and right must have equal lengths")

        input_L = np.concatenate([self._input_buffer_L, left])
        input_R = np.concatenate([self._input_buffer_R, right])

        hop = self._hop_size
        output_chunks: dict[str, list[np.ndarray]] = {
            ch: [] for ch in self._stft_out
        }

        complete = len(input_L) // hop * hop
        for start in range(0, complete, hop):
            hop_L = input_L[start:start + hop]
            hop_R = input_R[start:start + hop]

            X_L = self._stft_L.analyze_frame(hop_L)
            X_R = self._stft_R.analyze_frame(hop_R)

            coherence = self._coherence_est.estimate_frame(
                X_L, X_R, self._coherence_state
            )
            directness = self._coherence_est.directness_frame(self._coherence_state)

            decomp = self._decomposer.decompose_frame(X_L, X_R, coherence, directness)

            mid_frame = (X_L + X_R) * 0.5

            controls = self._spatial_plan.controls_at(self._frame_index * hop) if self._spatial_plan else None
            channel_spectra = self._router.route_frame(decomp, mid_frame, controls)
            if self._energy_controller is not None:
                channel_spectra = self._energy_controller.apply(channel_spectra, X_L, X_R)
            self._frame_index += 1

            for ch_name, spectrum in channel_spectra.items():
                samples = self._stft_out[ch_name].synthesize_frame(spectrum)
                if ch_name in self._rear_decorrelators:
                    samples = self._rear_decorrelators[ch_name].process(samples)
                output_chunks[ch_name].append(samples)

        self._input_buffer_L = input_L[complete:].copy()
        self._input_buffer_R = input_R[complete:].copy()

        result = {}
        for ch_name, chunks in output_chunks.items():
            if chunks:
                result[ch_name] = np.concatenate(chunks)
            else:
                result[ch_name] = np.zeros(0)

        return result

    def flush(self) -> dict[str, np.ndarray]:
        """Finish partial input and emit the delayed WOLA tail once."""
        if self._flushed:
            return {ch: np.zeros(0) for ch in self._stft_out}

        remaining = (-len(self._input_buffer_L)) % self._hop_size
        pad = remaining + self.latency_samples
        output = self.process_block(np.zeros(pad), np.zeros(pad))
        self._flushed = True
        return output

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
        self._decomposer.reset()
        if self._energy_controller is not None:
            self._energy_controller.reset()
        for decorrelator in self._rear_decorrelators.values():
            decorrelator.reset()
        self._frame_index = 0
        self._input_buffer_L = np.zeros(0)
        self._input_buffer_R = np.zeros(0)
        self._flushed = False


class UpmixPipeline:
    """Top-level orchestrator for file-based processing."""

    def __init__(self, config: UpmixConfig | None = None):
        self.config = config or UpmixConfig()
        self._spatial_plan: SpatialPlan | None = None

    def process_file(
        self,
        input_path: str,
        output_path: str,
        input_format_override: str | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> UpmixResult:
        """Upmix any supported input format to a higher output format.

        Args:
            input_path: Source audio file (WAV/FLAC).
            output_path: Destination file path.
            input_format_override: Force a specific input layout name instead of
                auto-detecting from channel count.
            progress_callback: Optional callable ``(message, fraction)`` invoked
                at key stages.  *fraction* is in [0, 1].

        Returns:
            :class:`~upmixer.result.UpmixResult` with processing metadata.
        """
        t0 = time.monotonic()
        cfg = self.config

        def _progress(msg: str, frac: float) -> None:
            _log.info(msg)
            if progress_callback is not None:
                progress_callback(msg, frac)

        reader = AudioReader(input_path)
        audio, sr = reader.read()
        n_samples = audio.shape[0]

        _progress(f"Input:  {input_path}", 0.0)

        if input_format_override is not None:
            if input_format_override not in INPUT_FORMAT_MAP:
                raise ValueError(
                    f"Unknown input format '{input_format_override}'. "
                    f"Valid: {sorted(INPUT_FORMAT_MAP.keys())}"
                )
            input_fmt = INPUT_FORMAT_MAP[input_format_override]
            if input_fmt.n_channels != reader.n_channels:
                raise ValueError(
                    f"Input format '{input_format_override}' expects "
                    f"{input_fmt.n_channels} channels but file has {reader.n_channels}"
                )
        else:
            input_fmt = detect_input_format(reader.n_channels)

        output_fmt = FORMAT_MAP[cfg.output_format]

        if not can_upmix(input_fmt, output_fmt):
            raise ValueError(
                f"Cannot upmix {input_fmt.name} → {output_fmt.name}: "
                f"output format is missing input channels or has fewer total channels. "
                f"Output must be a strict superset of the input channel layout."
            )

        _log.info("  Format:        %s (%dch)", input_fmt.name, input_fmt.n_channels)
        _log.info("  Sample rate:   %d Hz", sr)
        _log.info("  Duration:      %.2fs (%d samples)", n_samples / sr, n_samples)
        _log.info("  Output format: %s (%dch)", output_fmt.name, output_fmt.n_channels)

        if cfg.preview:
            audio, t0_preview, t1_preview = preview_slice(
                audio, sr, cfg.preview_duration_s, cfg.preview_start_s
            )
            n_samples = audio.shape[0]
            _log.info(
                "  Preview:       %.2fs–%.2fs (%.2fs window)",
                t0_preview, t1_preview, n_samples / sr,
            )

        _progress(f"  Format: {input_fmt.name} → {output_fmt.name}", 0.1)

        if input_fmt.n_channels <= 2:
            if input_fmt.n_channels == 1:
                left = right = audio[:, 0]
            else:
                left, right = audio[:, 0], audio[:, 1]

            fft_size, hop_size = cfg.resolve_fft_params(sr)
            _log.info("  FFT size: %d, hop: %d", fft_size, hop_size)
            self._spatial_plan = (
                analyze_spatial_plan(left, right, sr, cfg) if cfg.spatial_preanalysis else None
            )
            if self._spatial_plan is not None:
                _log.info("  Spatial: %s (confidence %.2f)", self._spatial_plan.profile, self._spatial_plan.confidence)
            channels = self._run_stereo_pipeline(
                left, right, sr, n_samples, fft_size, hop_size, progress_callback
            )
            channels = self._post_process(channels)
        else:
            from upmixer.upmix.multichannel import MultichannelUpmixer

            input_channels = {
                label: audio[:, i]
                for i, label in enumerate(input_fmt.channels)
            }
            _progress("  Processing (multichannel pass-through + channel derivation)...", 0.2)
            self._spatial_plan = (
                analyze_spatial_plan(audio[:, 0], audio[:, 1], sr, cfg) if cfg.spatial_preanalysis else None
            )
            if self._spatial_plan is not None:
                _log.info("  Spatial: %s (confidence %.2f)", self._spatial_plan.profile, self._spatial_plan.confidence)
            upmixer = MultichannelUpmixer(cfg, input_fmt, output_fmt, sr)
            channels = upmixer.process(input_channels, self._spatial_plan)
            channels = self._post_process_multichannel(channels, sr, audio)

        _progress("  Processing complete.", 0.9)

        out_sr = cfg.output_sample_rate if cfg.output_sample_rate else sr
        if cfg.output_type == "adm-bwf":
            if cfg.output_sample_rate is None:
                out_sr = 48_000
            if out_sr not in (48_000, 96_000):
                raise ValueError("Dolby ADM-BWF requires a 48 kHz or 96 kHz output sample rate")
            if cfg.output_subtype != "PCM_24":
                raise ValueError("Dolby ADM-BWF requires output_subtype='PCM_24'")
        if out_sr != sr:
            channels = self._resample_channels(channels, sr, out_sr)
            _log.info("  Resampled: %d Hz → %d Hz", sr, out_sr)

        _progress("  Mastering...", 0.93)
        mastering = MasteringChain(cfg)
        channels, mastering_result = mastering.process(channels, out_sr, output_fmt)

        if cfg.output_type == "adm-bwf":
            writer = AdmBwfWriter(output_path, out_sr, cfg)
            writer.write(
                channels,
                measured_lkfs=mastering_result.measured_lkfs,
                measured_tp_dbtp=mastering_result.measured_tp_dbtp,
            )
        else:
            writer = AudioWriter(output_path, out_sr, cfg)
            writer.write(channels)

        _progress(f"Output: {output_path}", 1.0)

        if cfg.downmix_output_path:
            self._write_downmix(channels, out_sr, cfg)

        return UpmixResult(
            input_path=input_path,
            output_path=output_path,
            input_format=input_fmt.name,
            output_format=output_fmt.name,
            input_sample_rate=sr,
            output_sample_rate=out_sr,
            duration_seconds=n_samples / sr,
            n_channels_in=input_fmt.n_channels,
            n_channels_out=output_fmt.n_channels,
            mode="realtime",
            measured_lkfs=mastering_result.measured_lkfs,
            measured_tp_dbtp=mastering_result.measured_tp_dbtp,
            applied_gain_db=mastering_result.applied_gain_db,
            spatial_profile=self._spatial_plan.profile if self._spatial_plan else None,
            spatial_profile_confidence=self._spatial_plan.confidence if self._spatial_plan else None,
            processing_time_seconds=time.monotonic() - t0,
        )

    def _run_stereo_pipeline(
        self,
        left: np.ndarray,
        right: np.ndarray,
        sr: int,
        n_samples: int,
        fft_size: int,
        hop_size: int,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> dict[str, np.ndarray]:
        """Run the coherence-based STFT pipeline on a stereo (or mono→stereo) pair.

        Uses pre-allocated output buffers (one array per channel) to avoid
        accumulating tens of thousands of tiny numpy chunks — which causes heavy
        GC pressure and apparent stalls on long high-sample-rate files.
        """
        cfg = self.config
        processor = StreamingProcessor(cfg, sr, self._spatial_plan)
        fmt = FORMAT_MAP[cfg.output_format]
        channel_names = [label.value for label in fmt.channels]

        latency = fft_size - hop_size

        buf_size = n_samples + latency + hop_size
        out_buf: dict[str, np.ndarray] = {
            ch: np.zeros(buf_size) for ch in channel_names
        }
        write_ptr = 0

        def _write_block(block_out: dict[str, np.ndarray]) -> int:
            chunk_len = len(next(iter(block_out.values())))
            end = write_ptr + chunk_len
            for ch, samples in block_out.items():
                out_buf[ch][write_ptr:end] = samples
            return chunk_len

        _log.info("  Processing...")
        n_blocks = math.ceil(n_samples / cfg.block_size)
        log_interval = max(1, n_blocks // 20)

        for block_idx, start in enumerate(range(0, n_samples, cfg.block_size)):
            end = min(start + cfg.block_size, n_samples)
            block_out = processor.process_block(left[start:end], right[start:end])
            write_ptr += _write_block(block_out)

            if n_blocks > 0:
                if (block_idx + 1) % log_interval == 0 or block_idx == n_blocks - 1:
                    pct = (block_idx + 1) * 100 // n_blocks
                    _log.info("  Processing... %3d%%", pct)
                if progress_callback is not None:
                    frac = 0.2 + 0.6 * (block_idx + 1) / n_blocks
                    progress_callback(f"  Block {block_idx + 1}/{n_blocks}", frac)

        flush_out = processor.flush()
        if flush_out and len(next(iter(flush_out.values()))) > 0:
            write_ptr += _write_block(flush_out)

        channels = {}
        for ch_name, buf in out_buf.items():
            full = buf[:write_ptr][latency:]
            if len(full) > n_samples:
                channels[ch_name] = full[:n_samples]
            elif len(full) < n_samples:
                channels[ch_name] = np.pad(full, (0, n_samples - len(full)))
            else:
                channels[ch_name] = full

        return channels

    def _post_process(self, channels: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Return stereo-sourced mixing output for the mastering chain."""
        return channels

    def _post_process_multichannel(
        self,
        channels: dict[str, np.ndarray],
        sr: int,
        original_audio: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """Mixing-phase post-processing for multichannel-sourced output: energy normalization.

        Delays are already applied inside MultichannelUpmixer.process().
        Soft-limiting and loudness normalization are handled by the mastering chain.
        """
        cfg = self.config

        if cfg.normalize_output:
            input_energy = float(np.sum(original_audio ** 2))
            output_energy = float(sum(np.sum(ch ** 2) for ch in channels.values()))
            if output_energy > 1e-20:
                scale = np.sqrt(input_energy / output_energy)
                channels = {name: ch * scale for name, ch in channels.items()}

        return channels

    @staticmethod
    def _resample_channels(
        channels: dict[str, np.ndarray], src_sr: int, dst_sr: int
    ) -> dict[str, np.ndarray]:
        """Resample all channels from src_sr to dst_sr using polyphase filter."""
        g = math.gcd(dst_sr, src_sr)
        up, down = dst_sr // g, src_sr // g
        return {
            name: resample_poly(ch, up, down).astype(np.float64)
            for name, ch in channels.items()
        }

    @staticmethod
    def _write_downmix(
        channels: dict[str, np.ndarray], sample_rate: int, cfg: UpmixConfig
    ) -> None:
        """Write ITU-R BS.775-4 Table 2 stereo downmix to cfg.downmix_output_path."""
        import soundfile as sf
        from upmixer.loudness import measure_true_peak

        L, R = itu_downmix_stereo(channels, surround_coeff=cfg.surround_downmix_coeff)
        stereo = np.column_stack([L, R])
        tp = measure_true_peak({"FL": L, "FR": R}, sample_rate)
        if tp > cfg.loudness_max_tp:
            stereo *= 10.0 ** ((cfg.loudness_max_tp - tp) / 20.0)
            _log.warning("  Downmix gain reduced %.2f dB to protect true peak", cfg.loudness_max_tp - tp)
        sf.write(cfg.downmix_output_path, stereo, sample_rate, subtype=cfg.output_subtype)
        _log.info("  Downmix: %s", cfg.downmix_output_path)
