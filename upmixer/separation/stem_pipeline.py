"""Stem-separation-based upmix pipeline.

Uses python-audio-separator to split audio into instrument stems, then
spatially routes each stem to the appropriate 3D position in the output layout.

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
    pip install 'audio-separator[cpu]'

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
import os
import tempfile
import time
from typing import Callable

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, FORMAT_MAP, INPUT_FORMAT_MAP, detect_input_format
from upmixer.io.adm_writer import AdmBwfWriter
from upmixer.io.reader import AudioReader
from upmixer.io.writer import AudioWriter
from upmixer.result import UpmixResult
from upmixer.separation.separator import StemSeparator, DEFAULT_MODEL
from upmixer.separation.stem_analyzer import analyze_stems
from upmixer.separation.stem_router import StemRouter
from upmixer.mastering import MasteringChain
from upmixer.utils import preview_slice, itu_downmix_stereo

_log = logging.getLogger("upmixer")

# Ordered list of (zone_name, left_channel, right_channel) pairs.
# Only zones whose both channels exist in the input are extracted.
_ZONE_PAIRS: list[tuple[str, ChannelLabel, ChannelLabel]] = [
    ("front",        ChannelLabel.FL,  ChannelLabel.FR),
    ("surround",     ChannelLabel.SL,  ChannelLabel.SR),
    ("back",         ChannelLabel.BL,  ChannelLabel.BR),
    ("height_front", ChannelLabel.TFL, ChannelLabel.TFR),
    ("height_back",  ChannelLabel.TBL, ChannelLabel.TBR),
]

# Channels passed through directly without stem separation.
_PASSTHROUGH_LABELS: list[ChannelLabel] = [ChannelLabel.C, ChannelLabel.LFE]


class StemUpmixPipeline:
    """File-based upmix pipeline using instrument stem separation.

    For stereo/mono input: separates the file directly as a single front zone.

    For multichannel input: extracts stereo pairs per spatial zone (front,
    surround, back, height_front, height_back), separates each independently,
    then routes zone-tagged stems to their spatial home in the output. Center
    and LFE channels bypass separation and are injected directly.

    Args:
        config: UpmixConfig controlling gains, LFE cutoff, output format, etc.
        model: audio-separator model filename. Defaults to htdemucs_ft (4-stem).
        model_dir: Model cache directory. Defaults to ~/.cache/upmixer-models.
        custom_routing: Override the fallback stem→channel routing table used
            when a stem/zone combination is not in the built-in zone tables.
            Format: {stem_name: {channel_name: gain}}.
    """

    def __init__(
        self,
        config: UpmixConfig | None = None,
        model: str = DEFAULT_MODEL,
        model_dir: str | None = None,
        custom_routing: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.config = config or UpmixConfig()
        self._model = model
        self._model_dir = model_dir
        self._custom_routing = custom_routing
        # Shared separator — model loaded once, reused across process_file() calls.
        # Re-created only when sample_rate changes between files.
        self._separator: StemSeparator | None = None
        self._separator_sr: int | None = None

    def _get_or_create_separator(self, sep_sr: int) -> StemSeparator:
        """Return a ready StemSeparator, creating or re-creating only when needed.

        The neural network model is loaded once and reused across all files that
        share the same sample rate — the dominant cost saving in batch mode.
        """
        sep_log_level = logging.DEBUG if _log.isEnabledFor(logging.DEBUG) else logging.WARNING
        if self._separator is None or self._separator_sr != sep_sr:
            if self._separator is not None:
                _log.info(
                    "  Separator: sample rate changed %d→%d, re-creating.",
                    self._separator_sr, sep_sr,
                )
                self._separator.close()
            self._separator = StemSeparator(
                model=self._model,
                model_dir=self._model_dir,
                sample_rate=sep_sr,
                log_level=sep_log_level,
            )
            self._separator_sr = sep_sr
        return self._separator

    def close(self) -> None:
        """Release the separator and unload the neural network model."""
        if self._separator is not None:
            self._separator.close()
            self._separator = None
            self._separator_sr = None

    def __enter__(self) -> "StemUpmixPipeline":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def process_file(
        self,
        input_path: str,
        output_path: str,
        input_format_override: str | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> UpmixResult:
        """Separate stems and write spatially routed multichannel output file.

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
        audio_full, sr = reader.read()

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

        _log.info("Input:  %s", input_path)
        _log.info("  Format:        %s (%dch)", input_fmt.name, input_fmt.n_channels)
        _log.info("  Sample rate:   %d Hz", sr)
        _log.info("  Duration:      %.2fs", audio_full.shape[0] / sr)
        _log.info("  Output format: %s (%dch)", output_fmt.name, output_fmt.n_channels)
        _log.info("  Model:         %s", self._model)

        # Preview: slice audio_full to the requested window before any processing.
        # Stereo mode normally passes the original file path to the separator;
        # after slicing we must pass a numpy array so the temp-file path below
        # applies — forcing a write of the sliced audio for the separator.
        _preview_stereo_forced_array: bool = False
        if cfg.preview:
            audio_full, t0_preview, t1_preview = preview_slice(
                audio_full, sr, cfg.preview_duration_s, cfg.preview_start_s
            )
            _log.info(
                "  Preview:       %.2fs–%.2fs (%.2fs window)",
                t0_preview, t1_preview, audio_full.shape[0] / sr,
            )
            _preview_stereo_forced_array = True  # force numpy path even for stereo

        # Resolve the final output sample rate BEFORE creating the separator so
        # audio-separator resamples internally during stem extraction.  All
        # downstream processing (routing, LN, resampling) then runs at out_sr
        # instead of the input sample rate.
        #
        # Why this matters: a 192 kHz / 408 s input with ADM-BWF output produces
        # ~7.5 GB of float64 channel data at 192 kHz before the final 48 kHz
        # resample.  By resolving the target rate early and passing it to the
        # separator, every post-separation step works at 48 kHz (~1.9 GB).
        out_sr: int = cfg.output_sample_rate or sr
        if cfg.output_type == "adm-bwf" and cfg.output_sample_rate is None and out_sr != 48_000:
            out_sr = 48_000
            _log.info("  ADM-BWF: output forced to 48 kHz (Dolby spec)")
        sep_sr = out_sr  # separate at final output rate; separator handles resample

        # ── Stem cache: try to load pre-separated stems ───────────────────────
        # Check before building the separator (avoids loading the model).
        _stem_cache = None
        _cache_hit_stems: dict[str, np.ndarray] | None = None
        if cfg.stem_cache_dir:
            from upmixer.separation.stem_cache import StemCache
            _stem_cache = StemCache(cfg.stem_cache_dir)
            _cache_result = _stem_cache.load(
                input_path, self._model, sep_sr,
                is_preview=cfg.preview,
                preview_duration=cfg.preview_duration_s,
                preview_start=cfg.preview_start_s,
            )
            if _cache_result is not None:
                _cache_hit_stems, _ = _cache_result

        # Build zone pairs and passthrough channels
        if input_fmt.n_channels <= 2:
            # Stereo / mono: single zone, untagged stems → DEFAULT_ROUTING
            # (full 3D spread including SL/SR/BL/BR/height/LFE).
            # In preview mode use numpy array (forces temp-file write below)
            # so the separator sees only the sliced window.
            if _preview_stereo_forced_array:
                n_ch = audio_full.shape[1] if audio_full.ndim > 1 else 1
                front_arr = (
                    np.column_stack([audio_full[:, 0], audio_full[:, 0]])
                    if n_ch == 1
                    else audio_full[:, :2]
                )
                sep_zones: dict[str, str | np.ndarray] = {"front": front_arr}
            else:
                sep_zones = {"front": input_path}
            passthrough: dict[str, np.ndarray] = {}
            stereo_mode = True
            _log.info("  Mode: stereo — single zone, full-3D routing")
        else:
            sep_zones, passthrough = _extract_zones(audio_full, input_fmt)
            stereo_mode = False
            _log.info("  Mode: multichannel — zones: %s", sorted(sep_zones.keys()))
            if passthrough:
                _log.info("  Passthrough: %s", sorted(passthrough.keys()))

        _progress("  Separating stems...", 0.1)

        # Separate each zone (skip if cache hit)
        all_stems: dict[str, np.ndarray] = {}

        if _cache_hit_stems is not None:
            # Cache hit — skip separation entirely
            all_stems = _cache_hit_stems
            _log.info("  Stem cache: using cached stems (separation skipped)")
        else:
            separator = self._get_or_create_separator(sep_sr)
            tmp_files: list[str] = []
            zone_names = list(sep_zones.keys())
            n_zones = len(zone_names)

            try:
                for zone_idx, zone_name in enumerate(zone_names):
                    pair_src = sep_zones[zone_name]
                    zone_frac = 0.15 + 0.60 * (zone_idx / n_zones)
                    _progress(f"    Separating zone: {zone_name}...", zone_frac)

                    if isinstance(pair_src, str):
                        sep_path = pair_src
                    else:
                        tmp = tempfile.mktemp(
                            suffix=".wav", prefix=f"upmixer_{zone_name}_"
                        )
                        sf.write(tmp, pair_src, sr, subtype="PCM_24")
                        sep_path = tmp
                        tmp_files.append(tmp)

                    zone_stems = separator.separate(sep_path)
                    for stem_name, stem_audio in zone_stems.items():
                        # Stereo: unzoned keys → DEFAULT_ROUTING (full 3D + LFE).
                        # Multichannel: zone-tagged keys → ZONE_ROUTING.
                        key = stem_name if stereo_mode else f"{stem_name}@{zone_name}"
                        all_stems[key] = stem_audio

            finally:
                for tmp in tmp_files:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                # separator is shared across calls — do NOT close here

            # Save to cache for next run
            if _stem_cache is not None and all_stems:
                _stem_cache.save(
                    input_path, self._model, sep_sr, all_stems, sep_sr,
                    is_preview=cfg.preview,
                    preview_duration=cfg.preview_duration_s,
                    preview_start=cfg.preview_start_s,
                )

        if not all_stems:
            raise RuntimeError(
                "Stem separation produced no output. Check model and input file."
            )

        n_samples = max(len(s) for s in all_stems.values())
        stem_summary = sorted({k.split("@")[0] for k in all_stems})
        _log.info(
            "  Stems: %s  (%.2fs at %d Hz)",
            stem_summary, n_samples / sep_sr, sep_sr,
        )

        # Resample passthrough channels to sep_sr for consistent mixing
        passthrough_resampled: dict[str, np.ndarray] = {}
        if passthrough:
            if sr != sep_sr:
                g = math.gcd(sr, sep_sr)
                up, down = sep_sr // g, sr // g
                for ch_name, ch_audio in passthrough.items():
                    passthrough_resampled[ch_name] = resample_poly(
                        ch_audio, up, down
                    ).astype(np.float64)
            else:
                passthrough_resampled = {k: v.astype(np.float64) for k, v in passthrough.items()}

        # ── Optional: stem rebalance (pre-routing) ───────────────────────────────
        # Applied after all_stems is fully built and resampled, before routing.
        # When disabled the existing content-aware routing runs unchanged.
        if cfg.stem_rebalance:
            from upmixer.separation.stem_rebalance import StemRebalancer
            _log.info("  Applying stem rebalance: %s", cfg.stem_rebalance)
            rebalancer = StemRebalancer(cfg.stem_rebalance, sep_sr)
            all_stems = rebalancer.process(all_stems)
            # Recompute n_samples in case any array changed shape (shouldn't happen,
            # but guards against accidental truncation in custom rebalancers).
            n_samples = max(len(s) for s in all_stems.values())

        # ── Optional: per-stem EQ (pre-routing) ──────────────────────────────
        if cfg.stem_eq_profiles:
            from upmixer.separation.stem_eq import StemEQ
            _log.info("  Applying per-stem EQ: %s", cfg.stem_eq_profiles)
            stem_eq = StemEQ(cfg.stem_eq_profiles, sep_sr)
            all_stems = stem_eq.process(all_stems)

        router = StemRouter(cfg, output_fmt, sep_sr, self._custom_routing)
        # out_sr already resolved above (before separator creation)

        # Content-aware analysis: per-stem features drive spatial gain scaling
        _progress("  Analyzing stem content...", 0.75)
        stem_features = analyze_stems(all_stems, sep_sr)
        for stem_key, feat in sorted(stem_features.items()):
            name = stem_key.split("@")[0]
            zone = f"@{stem_key.split('@')[1]}" if "@" in stem_key else ""
            _log.info(
                "    %s%s: width=%.2f  highs=%.2f  lows=%.2f  transients=%.2f",
                name, zone,
                feat.stereo_width, feat.high_freq_ratio,
                feat.low_freq_ratio, feat.transient_ratio,
            )

        # Route all stems to a mixed multichannel bed
        _progress("  Routing stems to channels...", 0.80)
        channels = router.route(
            all_stems,
            n_samples,
            passthrough_channels=set(passthrough_resampled.keys()),
            stem_features=stem_features,
        )

        # Normalize stem-derived channel energy BEFORE injecting passthrough.
        #
        # Why order matters: passthrough channels (C, LFE) carry the original
        # center vocals and bass at their original amplitude. If we inject them
        # first and then normalize, the large C energy causes total_output >> total_input
        # which drives scale < 1, silently attenuating the vocals.
        #
        # By normalizing stems-only first, we balance the routing spread without
        # touching the passthrough signal. C and LFE are then added at their original
        # level; the subsequent BS.1770-4 pass handles the final loudness target.
        if cfg.normalize_output:
            stem_input_energy = sum(
                float(np.sum(s ** 2)) for s in all_stems.values()
            )
            stem_output_energy = sum(
                float(np.sum(ch ** 2)) for ch in channels.values()
            )
            if stem_output_energy > 1e-20:
                # Cap at 1.0: never amplify derived channels above source energy.
                # Upward scaling (scale > 1) inflates FL/FR relative to the
                # passthrough C (vocals), causing perceived low vocal level on
                # mixes where routing gains lose energy vs. input.
                scale = min(1.0, np.sqrt(stem_input_energy / stem_output_energy))
                channels = {k: v * scale for k, v in channels.items()}

        # Inject passthrough channels at their original level (not scaled above)
        for ch_name, ch_audio in passthrough_resampled.items():
            if ch_name in channels:
                n = min(len(ch_audio), n_samples)
                channels[ch_name][:n] += ch_audio[:n]

        # Mastering phase — BS.1770-4 loudness normalization + True Peak + soft-limiting.
        # Separated from the routing/mixing phase above so both pipelines share
        # identical mastering behaviour via MasteringChain.
        _progress("  Mastering...", 0.90)
        mastering = MasteringChain(cfg)
        channels, mastering_result = mastering.process(channels, sep_sr, output_fmt)

        if out_sr != sep_sr:
            g = math.gcd(out_sr, sep_sr)
            up, down = out_sr // g, sep_sr // g
            channels = {
                name: resample_poly(ch, up, down).astype(np.float64)
                for name, ch in channels.items()
            }
            _log.info("  Resampled: %d Hz → %d Hz", sep_sr, out_sr)

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

        if cfg.downmix_output_path:
            L, R = itu_downmix_stereo(channels, surround_coeff=cfg.surround_downmix_coeff)
            sf.write(cfg.downmix_output_path, np.column_stack([L, R]), out_sr, subtype=cfg.output_subtype)
            _log.info("  Downmix: %s", cfg.downmix_output_path)

        _progress(f"Output: {output_path}", 1.0)

        return UpmixResult(
            input_path=input_path,
            output_path=output_path,
            input_format=input_fmt.name,
            output_format=output_fmt.name,
            input_sample_rate=sr,
            output_sample_rate=out_sr,
            duration_seconds=n_samples / sep_sr,
            n_channels_in=input_fmt.n_channels,
            n_channels_out=output_fmt.n_channels,
            mode="stem",
            measured_lkfs=mastering_result.measured_lkfs,
            measured_tp_dbtp=mastering_result.measured_tp_dbtp,
            applied_gain_db=mastering_result.applied_gain_db,
            stems=stem_summary,
            processing_time_seconds=time.monotonic() - t0,
        )


def _extract_zones(
    audio: np.ndarray,
    input_fmt: object,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Split multichannel audio into stereo pairs by spatial zone and passthrough channels.

    Returns:
        zones: zone_name → (n_samples, 2) float64 array for stem separation.
        passthrough: channel_name → (n_samples,) float64 array for direct injection.
    """
    ch_map = {
        label: audio[:, i].astype(np.float64)
        for i, label in enumerate(input_fmt.channels)
    }

    zones: dict[str, np.ndarray] = {}
    for zone_name, left_label, right_label in _ZONE_PAIRS:
        if left_label in ch_map and right_label in ch_map:
            zones[zone_name] = np.column_stack(
                [ch_map[left_label], ch_map[right_label]]
            )

    passthrough: dict[str, np.ndarray] = {}
    for label in _PASSTHROUGH_LABELS:
        if label in ch_map:
            passthrough[label.value] = ch_map[label]

    return zones, passthrough
