"""Stem-separation-based upmix pipeline.

Uses the in-core inference engine (upmixer.separation.inference) to split
audio into instrument stems, then spatially routes each stem to the
appropriate 3D position in the output layout.

Multichannel input handling:
  Stereo / mono  → single "front" zone, separated directly.
  Multichannel   → channels split into stereo pairs by spatial zone:
                     front        (FL / FR)
                     surround     (SL / SR)
                     back         (BL / BR)      — 7.1+
                     height_front (TFL / TFR)    — Atmos
                     height_back  (TBL / TBR)    — Atmos 5.1.4 / 7.1.4
                   Each zone is separated independently; stems are tagged
                   "StemName@zone" so the router keeps them in their spatial home.
                   Center (C) and LFE are passed through without separation.

This is a non-realtime, file-based pipeline.
For realtime/low-latency upmixing use UpmixPipeline in pipeline.py.

Usage:
    pip install 'upmixer[separation-cpu]'

    from upmixer.separation.stem_pipeline import StemUpmixPipeline
    from upmixer.config import UpmixConfig

    cfg = UpmixConfig(output_format='7.1.4')
    pipeline = StemUpmixPipeline(cfg)
    result = pipeline.process_file('surround_51.wav', 'atmos_714.wav')
    print(result.to_json())
"""
from __future__ import annotations

import logging
import math
import time
from typing import Callable

import numpy as np
from scipy.signal import resample_poly

from upmixer.binaural.renderer import render_binaural_delivery
from upmixer.config import UpmixConfig
from upmixer.crosstalk.renderer import render_crosstalk_delivery
from upmixer.codecs import validate_codec
from upmixer.formats import (
    BINAURAL,
    TRANSAURAL,
    OutputFormat,
    detect_input_format,
    validate_delivery,
)
from upmixer.loudness import measure_integrated_loudness
from upmixer.io.adm_writer import AdmBwfWriter
from upmixer.io.writer import AudioWriter, write_audio
from upmixer.mastering import MasteringChain, MasteringResult
from upmixer.result import UpmixResult
from upmixer.separation.separator import StemSeparator
from upmixer.separation.source_anchor import apply_source_anchor
from upmixer.separation.stem_pipeline_separate import SeparationResult, separate
from upmixer.separation.stem_router import StemRouter
from upmixer.separation.stem_zones import _resample_zones
from upmixer.utils import itu_downmix_stereo

_log = logging.getLogger("upmixer")


class PreMasterAbort(Exception):
    """Raise from a ``process_file`` ``pre_master_hook`` to stop the run right
    after the hook observes the pre-mastering bed, before mastering/writing.

    Safe to raise unconditionally from the hook: nothing has been written to
    ``output_path`` yet at that point, so aborting leaves no partial output.
    """


class StemUpmixPipeline:
    """File-based upmix pipeline using instrument stem separation.

    For stereo/mono input: separates the file directly as a single front zone.

    For multichannel input: extracts stereo pairs per spatial zone (front,
    surround, back, height_front, height_back), separates each independently,
    then routes zone-tagged stems to their spatial home in the output. Center
    and LFE channels bypass separation and are injected directly.

    Stem selection is driven by ``config.stems`` (or the manifest ``stems`` key).
    The pipeline internally resolves which models to run and in which order via
    :func:`~upmixer.separation.stem_plan.resolve_separation_plan`.  Model
    selection is not exposed to callers.

    Args:
        config: UpmixConfig controlling gains, LFE cutoff, output format, etc.
        model_dir: Model cache directory. Defaults to ~/.cache/upmixer-models.
        custom_routing: Override the fallback stem→channel routing table used
            when a stem/zone combination is not in the built-in zone tables.
            Format: {stem_name: {channel_name: gain}}.
    """

    def __init__(
        self,
        config: UpmixConfig | None = None,
        model_dir: str | None = None,
        custom_routing: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.config = config or UpmixConfig()
        self._model_dir = model_dir
        self._custom_routing = custom_routing
        self._separators: dict[str, StemSeparator] = {}
        self._separator_sr: int | None = None
        self._separator_settings: tuple[object, ...] | None = None

    def _validated_separator_settings(self) -> tuple[object, ...]:
        cfg = self.config
        if cfg.stem_batch_size is not None and cfg.stem_batch_size < 1:
            raise ValueError("stem_batch_size must be at least 1")
        if cfg.stem_segment_size is not None and cfg.stem_segment_size < 1:
            raise ValueError("stem_segment_size must be at least 1")
        if cfg.stem_chunk_duration_s is not None and cfg.stem_chunk_duration_s <= 0:
            raise ValueError("stem_chunk_duration_s must be greater than 0")
        if cfg.stem_model_cache_size is not None and cfg.stem_model_cache_size < 1:
            raise ValueError("stem_model_cache_size must be at least 1")
        if cfg.stem_overlap is not None and cfg.stem_overlap < 1:
            raise ValueError("stem_overlap must be at least 1")
        if cfg.stem_pitch_shift is not None and cfg.stem_pitch_shift <= 0:
            raise ValueError("stem_pitch_shift must be greater than 0")
        return (
            cfg.stem_batch_size,
            cfg.stem_segment_size,
            cfg.stem_chunk_duration_s,
            cfg.stem_model_cache_size,
            cfg.stem_overlap,
            cfg.stem_tta,
            cfg.stem_pitch_shift,
        )

    def _get_or_create_separator(self, model: str, sep_sr: int) -> StemSeparator:
        """Return a ready StemSeparator for the given model and sample rate.

        Creates a new instance if the model has not been loaded yet.  If the
        sample rate changes between calls all cached separators are recreated
        (in practice all stages of a single plan run at the same sep_sr).
        """
        cfg = self.config
        requested_settings = self._validated_separator_settings()
        if (
            self._separator_sr != sep_sr
            or self._separator_settings != requested_settings
        ):
            if self._separators:
                _log.info(
                    "  Separator settings changed; re-creating loaded models."
                )
            for s in self._separators.values():
                s.close()
            self._separators = {}
            self._separator_sr = sep_sr
            self._separator_settings = requested_settings
        if model not in self._separators:
            separator = StemSeparator(
                model=model,
                model_dir=self._model_dir,
                sample_rate=sep_sr,
                batch_size=cfg.stem_batch_size,
                segment_size=cfg.stem_segment_size,
                chunk_duration_s=cfg.stem_chunk_duration_s,
                overlap=cfg.stem_overlap,
                tta=cfg.stem_tta,
                pitch_shift=cfg.stem_pitch_shift,
            )
            cache_size = cfg.stem_model_cache_size
            if cache_size is None and separator.backend == "cpu":
                cache_size = 1
            while cache_size is not None and len(self._separators) >= cache_size:
                old_model = next(iter(self._separators))
                old_separator = self._separators.pop(old_model)
                _log.info("  Releasing separator model=%s to bound RAM", old_model)
                old_separator.close()
            self._separators[model] = separator
        else:
            separator = self._separators.pop(model)
            self._separators[model] = separator
        return separator

    def close(self) -> None:
        """Release all separators and unload neural network models."""
        for s in self._separators.values():
            s.close()
        self._separators = {}
        self._separator_sr = None
        self._separator_settings = None

    def __enter__(self) -> "StemUpmixPipeline":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _separate(
        self,
        input_path: str,
        input_format_override: str | None,
        _progress: Callable[[str, float], None],
    ) -> SeparationResult:
        """Read, zone-split, separate, and cache stems — no routing or mastering."""
        return separate(
            self._get_or_create_separator,
            self.config,
            input_path,
            input_format_override,
            _progress,
        )

    def _post_process_stems(
        self, sep: SeparationResult, _progress: Callable[[str, float], None]
    ) -> tuple[dict[str, np.ndarray], int]:
        """Apply rebalance and per-stem EQ, returning stems and new length."""
        cfg = self.config
        all_stems = sep.all_stems
        n_samples = sep.n_samples

        if cfg.stem_rebalance:
            from upmixer.separation.stem_rebalance import StemRebalancer
            _progress("  Rebalancing stems...", 0.76)
            _log.info("  Applying stem rebalance: %s", cfg.stem_rebalance)
            all_stems = StemRebalancer(cfg.stem_rebalance, sep.sep_sr).process(all_stems)
            n_samples = max(len(s) for s in all_stems.values())

        if cfg.stem_eq_profiles:
            from upmixer.separation.stem_eq import StemEQ
            _progress("  Applying per-stem EQ...", 0.77)
            _log.info("  Applying per-stem EQ: %s", cfg.stem_eq_profiles)
            all_stems = StemEQ(cfg.stem_eq_profiles, sep.sep_sr).process(all_stems)

        return all_stems, n_samples

    def _write_delivery(
        self,
        channels: dict[str, np.ndarray],
        output_path: str,
        output_fmt: OutputFormat,
        out_sr: int,
        mastering_result: MasteringResult,
    ) -> tuple[dict[str, np.ndarray], OutputFormat, MasteringResult]:
        """Render the requested delivery type and write the output file."""
        cfg = self.config
        if cfg.output_type == "binaural":
            channels, mastering_result = render_binaural_delivery(
                channels, output_fmt, out_sr, cfg
            )
            output_fmt = BINAURAL
            AudioWriter(output_path, out_sr, cfg, output_format=BINAURAL).write(channels)
        elif cfg.output_type == "transaural":
            channels, mastering_result = render_crosstalk_delivery(
                channels, output_fmt, out_sr, cfg
            )
            output_fmt = TRANSAURAL
            AudioWriter(output_path, out_sr, cfg, output_format=TRANSAURAL).write(channels)
        elif cfg.output_type == "adm-bwf":
            AdmBwfWriter(output_path, out_sr, cfg).write(
                channels,
                measured_lkfs=mastering_result.measured_lkfs,
                measured_tp_dbtp=mastering_result.measured_tp_dbtp,
            )
        else:
            AudioWriter(output_path, out_sr, cfg).write(channels)

        if cfg.downmix_output_path and output_fmt.n_channels > 2:
            self._write_downmix(channels, out_sr)

        return channels, output_fmt, mastering_result

    def _write_downmix(self, channels: dict[str, np.ndarray], out_sr: int) -> None:
        from upmixer.loudness import measure_true_peak
        from upmixer.mastering.delivery import resolve_delivery_target

        cfg = self.config
        ceiling = resolve_delivery_target(cfg).max_tp_dbtp
        left, right = itu_downmix_stereo(
            channels,
            surround_coeff=cfg.surround_downmix_coeff,
            height_coeff=cfg.height_downmix_coeff,
        )
        stereo = np.column_stack([left, right])
        tp = measure_true_peak({"FL": left, "FR": right})
        if tp > ceiling:
            stereo *= 10.0 ** ((ceiling - tp) / 20.0)
        write_audio(
            cfg.downmix_output_path, stereo, out_sr, cfg.output_codec, cfg.output_subtype
        )
        _log.info("  Downmix: %s", cfg.downmix_output_path)

    def process_file(
        self,
        input_path: str,
        output_path: str,
        input_format_override: str | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
        pre_master_hook: Callable[[dict[str, np.ndarray], int, OutputFormat], None] | None = None,
    ) -> UpmixResult:
        """Separate stems and write spatially routed multichannel output file.

        Args:
            input_path: Source audio file (WAV/FLAC).
            output_path: Destination file path.
            input_format_override: Force a specific input layout name instead of
                auto-detecting from channel count.
            progress_callback: Optional callable ``(message, fraction)`` invoked
                at key stages.  *fraction* is in [0, 1].
            pre_master_hook: Optional callable ``(channels, sample_rate,
                output_format)`` invoked with the fully routed, pre-mastering
                channel bed, right before :class:`~upmixer.mastering.chain.MasteringChain`
                runs. Lets a caller inspect or analyze the exact bed the export
                would master (e.g. to precompute a reference-match FIR) without
                duplicating separation/routing. Raise :class:`PreMasterAbort`
                from the hook to stop the run here, before mastering/writing —
                safe because no output has been written yet.

        Returns:
            :class:`~upmixer.result.UpmixResult` with processing metadata.
        """
        t0 = time.monotonic()
        cfg = self.config
        validate_delivery(cfg.output_format, cfg.output_type)
        validate_codec(cfg.output_format, cfg.output_type, cfg.output_codec, cfg.output_subtype)
        if not 0.0 <= cfg.stem_source_anchor_strength <= 1.0:
            raise ValueError("stem_source_anchor_strength must be between 0.0 and 1.0")

        def _progress(msg: str, frac: float) -> None:
            _log.info(msg)
            if progress_callback is not None:
                progress_callback(msg, frac)

        sep = self._separate(input_path, input_format_override, _progress)
        output_fmt = sep.output_fmt
        sr = sep.input_sr
        sep_sr = sep.sep_sr
        out_sr = sep.out_sr
        audio_full = sep.audio_full

        passthrough_resampled: dict[str, np.ndarray] = {}
        if sep.passthrough:
            if sr != sep_sr:
                g = math.gcd(sr, sep_sr)
                up, down = sep_sr // g, sr // g
                for ch_name, ch_audio in sep.passthrough.items():
                    passthrough_resampled[ch_name] = resample_poly(
                        ch_audio, up, down
                    ).astype(np.float32, copy=False)
            else:
                passthrough_resampled = sep.passthrough

        source_zones = _resample_zones(sep.source_zones, sr, sep_sr)
        all_stems, n_samples = self._post_process_stems(sep, _progress)

        if cfg.spatial_profile not in {"auto", "balanced"}:
            _log.warning(
                "Stem mode does not apply dynamic spatial profiles; ignoring '%s'. "
                "Use mixing.stem_routing (or a routing preset) instead.",
                cfg.spatial_profile,
            )
        router = StemRouter(cfg, output_fmt, sep_sr, self._custom_routing)

        _progress("  Routing stems to channels...", 0.80)
        channels = router.route(
            all_stems,
            n_samples,
            passthrough_channels=set(passthrough_resampled.keys()),
        )

        for ch_name, ch_audio in passthrough_resampled.items():
            if ch_name in channels:
                n = min(len(ch_audio), n_samples)
                channels[ch_name][:n] += ch_audio[:n]
        ch_audio = None

        if cfg.stem_source_anchor_strength > 0.0:
            _progress("  Applying source anchor...", 0.83)
        channels = apply_source_anchor(
            channels, source_zones, output_fmt, cfg.stem_source_anchor_strength,
        )

        if cfg.normalize_output:
            _progress("  Normalizing output...", 0.86)
            channels = _normalize_to_source(
                channels, audio_full, sr, sep_sr, output_fmt
            )

        del all_stems, audio_full, source_zones, passthrough_resampled

        if pre_master_hook is not None:
            pre_master_hook(channels, sep_sr, output_fmt)

        _progress("  Mastering...", 0.90)
        channels, mastering_result = MasteringChain(cfg).process(
            channels, sep_sr, output_fmt
        )

        if out_sr != sep_sr:
            g = math.gcd(out_sr, sep_sr)
            up, down = out_sr // g, sep_sr // g
            channels = {
                name: resample_poly(ch, up, down).astype(np.float64)
                for name, ch in channels.items()
            }
            _log.info("  Resampled: %d Hz → %d Hz", sep_sr, out_sr)

        channels, output_fmt, mastering_result = self._write_delivery(
            channels, output_path, output_fmt, out_sr, mastering_result
        )

        _progress(f"Output: {output_path}", 1.0)

        return UpmixResult(
            input_path=input_path,
            output_path=output_path,
            input_format=sep.input_fmt.name,
            output_format=output_fmt.name,
            input_sample_rate=sr,
            output_sample_rate=out_sr,
            duration_seconds=n_samples / sep_sr,
            n_channels_in=sep.input_fmt.n_channels,
            n_channels_out=output_fmt.n_channels,
            mode="stem",
            **mastering_result.delivery_fields(),
            stems=sep.stem_summary,
            processing_time_seconds=time.monotonic() - t0,
        )

    def prepare_stems(
        self,
        input_path: str,
        input_format_override: str | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> UpmixResult:
        """Separate and cache instrument stems without routing or mastering.

        Runs only the separation half of :meth:`process_file` — read, zone
        split, stem inference, and (when ``config.stem_cache_dir`` is set) cache
        write — then stops. No spatial routing, mastering, or output file is
        produced. Used by callers that only need the cached stem set prepared
        for a later mix/export (e.g. project preparation), where the
        routed/mastered output would be discarded.

        Returns a :class:`~upmixer.result.UpmixResult` describing the separated
        stems; output-only fields (``output_path``, ``n_channels_out``,
        mastering measurements) are left empty.
        """
        t0 = time.monotonic()

        def _progress(msg: str, frac: float) -> None:
            _log.info(msg)
            if progress_callback is not None:
                progress_callback(msg, frac)

        sep = self._separate(input_path, input_format_override, _progress)
        _progress("  Stems prepared", 1.0)

        return UpmixResult(
            input_path=input_path,
            output_path="",
            input_format=sep.input_fmt.name,
            output_format=sep.output_fmt.name,
            input_sample_rate=sep.input_sr,
            output_sample_rate=sep.out_sr,
            duration_seconds=sep.n_samples / sep.sep_sr,
            n_channels_in=sep.input_fmt.n_channels,
            n_channels_out=0,
            mode="stem",
            stems=sep.stem_summary,
            processing_time_seconds=time.monotonic() - t0,
        )


def _normalize_to_source(
    channels: dict[str, np.ndarray],
    audio_full: np.ndarray,
    sr: int,
    sep_sr: int,
    output_fmt: OutputFormat,
) -> dict[str, np.ndarray]:
    """Rescale output channels to match the source's BS.1770 loudness.

    Total energy summed every channel equally, so a bed with heavily shaped
    height/surround sends landed off the source's loudness even at matched
    energy (phase 9 report). Falls back to energy when either side is too
    short or quiet to gate, or the source layout is unknown.
    """
    if sr != sep_sr:
        g = math.gcd(sr, sep_sr)
        source_audio = resample_poly(audio_full, sep_sr // g, sr // g, axis=0)
    else:
        source_audio = audio_full
    if source_audio.ndim == 1:
        source_audio = source_audio[:, None]

    try:
        source_fmt = detect_input_format(source_audio.shape[1])
    except ValueError:
        source_fmt = None
    if source_fmt is not None:
        source_lkfs = measure_integrated_loudness(
            {label.value: source_audio[:, i] for i, label in enumerate(source_fmt.channels)},
            sep_sr,
            source_fmt,
        )
        output_lkfs = measure_integrated_loudness(channels, sep_sr, output_fmt)
        if min(source_lkfs, output_lkfs) > -70.0:
            scale = 10.0 ** ((source_lkfs - output_lkfs) / 20.0)
            return {name: ch * scale for name, ch in channels.items()}

    source_energy = float(np.vdot(source_audio, source_audio).real)
    output_energy = sum(float(np.vdot(ch, ch).real) for ch in channels.values())
    if source_energy > 1e-20 and output_energy > 1e-20:
        scale = np.sqrt(source_energy / output_energy)
        return {name: ch * scale for name, ch in channels.items()}
    return channels
