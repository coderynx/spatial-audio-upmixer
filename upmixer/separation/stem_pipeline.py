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
    pipeline.process_file('surround_51.wav', 'atmos_714.wav')
"""
from __future__ import annotations

import math
import os
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, FORMAT_MAP, INPUT_FORMAT_MAP, detect_input_format
from upmixer.io.adm_writer import AdmBwfWriter
from upmixer.io.reader import AudioReader
from upmixer.io.writer import AudioWriter
from upmixer.separation.separator import StemSeparator, DEFAULT_MODEL
from upmixer.separation.stem_router import StemRouter
from upmixer.utils import soft_limit

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

    def process_file(
        self,
        input_path: str,
        output_path: str,
        input_format_override: str | None = None,
    ) -> None:
        """Separate stems and write spatially routed multichannel output file."""
        cfg = self.config

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

        print(f"Input:  {input_path}")
        print(f"  Format:       {input_fmt.name} ({input_fmt.n_channels}ch)")
        print(f"  Sample rate:  {sr} Hz")
        print(f"  Output format:{output_fmt.name} ({output_fmt.n_channels}ch)")
        print(f"  Model:        {self._model}")

        # audio-separator outputs at sep_sr regardless of input sr
        sep_sr = 44100
        separator = StemSeparator(
            model=self._model,
            model_dir=self._model_dir,
            sample_rate=sep_sr,
        )

        # Build zone pairs and passthrough channels
        if input_fmt.n_channels <= 2:
            # Stereo / mono: single zone, untagged stems → DEFAULT_ROUTING
            # (full 3D spread including SL/SR/BL/BR/height/LFE).
            sep_zones: dict[str, str | np.ndarray] = {"front": input_path}
            passthrough: dict[str, np.ndarray] = {}
            stereo_mode = True
            print("  Mode: stereo — single zone, full-3D routing")
        else:
            sep_zones, passthrough = _extract_zones(audio_full, input_fmt)
            stereo_mode = False
            print(f"  Mode: multichannel — zones: {sorted(sep_zones.keys())}")
            if passthrough:
                print(f"  Passthrough: {sorted(passthrough.keys())}")

        # Separate each zone
        print("  Separating stems...")
        all_stems: dict[str, np.ndarray] = {}
        tmp_files: list[str] = []

        try:
            for zone_name, pair_src in sep_zones.items():
                print(f"    {zone_name}...")
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

        if not all_stems:
            raise RuntimeError(
                "Stem separation produced no output. Check model and input file."
            )

        n_samples = max(len(s) for s in all_stems.values())
        stem_summary = sorted({k.split("@")[0] for k in all_stems})  # noqa: keep zone-stripped names
        print(
            f"  Stems: {stem_summary}  ({n_samples / sep_sr:.2f}s at {sep_sr} Hz)"
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

        router = StemRouter(cfg, output_fmt, sep_sr, self._custom_routing)
        out_sr = cfg.output_sample_rate if cfg.output_sample_rate else sep_sr

        # Content-aware routing: analyse each stem → modulate routing table gains
        per_stem_routing = None
        if cfg.content_aware_mixing:
            from upmixer.separation.content_mixer import ContentMixer
            mixer = ContentMixer(cfg, output_fmt, sep_sr)
            per_stem_routing = mixer.build(all_stems, router)
            print("  Content-aware mixing:")
            for stem_key, audio in all_stems.items():
                base = router.get_routing(stem_key)
                if base:
                    features = mixer._analyzer.analyze(audio)
                    print(f"    {mixer.describe(stem_key, features)}")

        # Route all stems to a mixed multichannel bed (both adm-bwf and wav)
        channels = router.route(
            all_stems,
            n_samples,
            passthrough_channels=set(passthrough_resampled.keys()),
            per_stem_routing=per_stem_routing,
        )

        # Inject passthrough channels
        for ch_name, ch_audio in passthrough_resampled.items():
            if ch_name in channels:
                n = min(len(ch_audio), n_samples)
                channels[ch_name][:n] += ch_audio[:n]

        # Normalize energy
        if cfg.normalize_output:
            total_input_energy = sum(
                float(np.sum(s ** 2)) for s in all_stems.values()
            ) + sum(
                float(np.sum(v ** 2)) for v in passthrough.values()
            )
            total_output_energy = sum(
                float(np.sum(ch ** 2)) for ch in channels.values()
            )
            if total_output_energy > 1e-20:
                scale = np.sqrt(total_input_energy / total_output_energy)
                channels = {k: v * scale for k, v in channels.items()}

        # Loudness normalization — BS.1770-4, Dolby DEE compliance
        if cfg.loudness_normalize:
            from upmixer.loudness import normalize_loudness
            channels, ln_info = normalize_loudness(
                channels,
                sep_sr,
                output_fmt,
                target_lkfs=cfg.loudness_target_lkfs,
                max_tp_dbtp=cfg.loudness_max_tp,
                max_gain_db=cfg.loudness_max_gain_db,
            )
            print(
                f"  Loudness: {ln_info['measured_lkfs']:.1f} LKFS → "
                f"{cfg.loudness_target_lkfs:.1f} LKFS  "
                f"gain {ln_info['applied_gain_db']:+.1f} dB  "
                f"TP {ln_info['measured_tp_dbtp']:.1f} dBTP"
                + ("  [TP limited]" if ln_info["tp_limited"] else "")
            )

        for name in channels:
            channels[name] = soft_limit(channels[name], cfg.peak_limit_threshold)

        # Resample to output sample rate
        if cfg.output_sample_rate and cfg.output_sample_rate != sep_sr:
            g = math.gcd(cfg.output_sample_rate, sep_sr)
            up, down = cfg.output_sample_rate // g, sep_sr // g
            channels = {
                name: resample_poly(ch, up, down).astype(np.float64)
                for name, ch in channels.items()
            }
            print(f"  Resampled: {sep_sr} Hz → {cfg.output_sample_rate} Hz")

        if cfg.output_type == "adm-bwf":
            writer = AdmBwfWriter(output_path, out_sr, cfg)
        else:
            writer = AudioWriter(output_path, out_sr, cfg)
        writer.write(channels)

        print(f"Output: {output_path}")


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
