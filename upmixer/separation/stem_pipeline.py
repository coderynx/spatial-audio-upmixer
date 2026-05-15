"""Stem-separation-based upmix pipeline.

Uses python-audio-separator to split a stereo file into instrument stems,
then spatially routes each stem to the appropriate 3D position in the
output channel layout.

This is a separate, non-realtime pipeline — not suitable for streaming.
For realtime/low-latency upmixing use UpmixPipeline in pipeline.py.

Usage:
    pip install 'audio-separator[cpu]'

    from upmixer.separation.stem_pipeline import StemUpmixPipeline
    from upmixer.config import UpmixConfig

    cfg = UpmixConfig(output_format='7.1.2')
    pipeline = StemUpmixPipeline(cfg)
    pipeline.process_file('stereo.wav', 'output.wav')
"""
from __future__ import annotations

import math
import tempfile
import os

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, INPUT_FORMAT_MAP, can_upmix, detect_input_format
from upmixer.io.adm_writer import AdmBwfWriter
from upmixer.io.reader import AudioReader
from upmixer.io.writer import AudioWriter
from upmixer.separation.separator import StemSeparator, DEFAULT_MODEL
from upmixer.separation.stem_router import StemRouter
from upmixer.utils import normalize_energy, soft_limit


class StemUpmixPipeline:
    """File-based upmix pipeline using instrument stem separation.

    Stems are separated via python-audio-separator (requires separate install),
    then spatially routed to a multichannel output using perceptually-motivated
    default positions:

      Vocals  → Center (+ small FL/FR for harmony width, light height)
      Bass    → LFE + FL/FR
      Drums   → FL/FR primary, SL/SR room reverb, TFL/TFR overheads, LFE kick
      Other   → SL/SR primary (surround bed), FL/FR secondary, height/back for pads

    Args:
        config: UpmixConfig controlling gains, LFE cutoff, output format, etc.
        model: audio-separator model filename. Defaults to htdemucs_ft (4-stem).
        model_dir: Model cache directory. Defaults to ~/.cache/upmixer-models.
        custom_routing: Override the default stem→channel routing table.
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
        """Separate stems from input_path and write spatially routed multichannel file."""
        cfg = self.config

        reader = AudioReader(input_path)
        _, sr = reader.read()

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
        print(f"  Format:  {input_fmt.name} ({input_fmt.n_channels}ch)")
        print(f"  Sample rate: {sr} Hz")
        print(f"  Output format: {output_fmt.name} ({output_fmt.n_channels}ch)")
        print(f"  Model: {self._model}")

        # audio-separator expects mono/stereo; downmix multichannel to stereo if needed
        sep_input_path = input_path
        downmix_tmp: str | None = None
        if input_fmt.n_channels > 2:
            audio_full, _ = reader.read()
            stereo = _downmix_to_stereo(audio_full, input_fmt)
            downmix_tmp = tempfile.mktemp(suffix=".wav", prefix="upmixer_downmix_")
            sf.write(downmix_tmp, stereo, sr, subtype="PCM_24")
            sep_input_path = downmix_tmp
            print(f"  Downmixed {input_fmt.name} → stereo for separation")

        # Determine separation sample rate: audio-separator works best at 44100
        sep_sr = 44100
        print("  Separating stems...")
        separator = StemSeparator(
            model=self._model,
            model_dir=self._model_dir,
            sample_rate=sep_sr,
        )
        try:
            stems = separator.separate(sep_input_path)
        finally:
            if downmix_tmp and os.path.exists(downmix_tmp):
                os.unlink(downmix_tmp)

        if not stems:
            raise RuntimeError("Stem separation produced no output. Check model and input file.")

        stem_names = sorted(stems.keys())
        n_samples = max(len(s) for s in stems.values())
        print(f"  Stems: {stem_names}  ({n_samples / sep_sr:.2f}s at {sep_sr} Hz)")

        # Route stems to channels
        router = StemRouter(cfg, output_fmt, sep_sr, self._custom_routing)
        channels = router.route(stems, n_samples)

        # Normalize and limit
        orig_L = stems.get("Vocals", next(iter(stems.values())))[:, 0]
        orig_R = stems.get("Vocals", next(iter(stems.values())))[:, 1] if stems.get("Vocals", next(iter(stems.values()))).shape[1] > 1 else orig_L

        if cfg.normalize_output:
            # Normalization reference: sum energy of all stems
            total_input_energy = sum(float(np.sum(s ** 2)) for s in stems.values())
            total_output_energy = sum(float(np.sum(ch ** 2)) for ch in channels.values())
            if total_output_energy > 1e-20:
                scale = np.sqrt(total_input_energy / total_output_energy)
                channels = {k: v * scale for k, v in channels.items()}

        for name in channels:
            channels[name] = soft_limit(channels[name], cfg.peak_limit_threshold)

        # Resample to target output sample rate
        out_sr = cfg.output_sample_rate if cfg.output_sample_rate else sep_sr
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


def _downmix_to_stereo(audio: np.ndarray, input_fmt: object) -> np.ndarray:
    """Downmix a multichannel array to stereo using ITU-R BS.775 coefficients.

    Channel layout follows input_fmt.channels order.
    Unknown channels are summed with unity gain into both L and R.
    """
    from upmixer.formats import ChannelLabel

    channels_list = list(input_fmt.channels)
    ch_map = {label: audio[:, i] for i, label in enumerate(channels_list)}

    L = np.zeros(len(audio), dtype=np.float64)
    R = np.zeros(len(audio), dtype=np.float64)

    # ITU-R BS.775 coefficients
    if ChannelLabel.FL in ch_map:
        L += ch_map[ChannelLabel.FL]
    if ChannelLabel.FR in ch_map:
        R += ch_map[ChannelLabel.FR]
    if ChannelLabel.C in ch_map:
        center = ch_map[ChannelLabel.C] * 0.7071
        L += center
        R += center
    if ChannelLabel.SL in ch_map:
        L += ch_map[ChannelLabel.SL] * 0.7071
    if ChannelLabel.SR in ch_map:
        R += ch_map[ChannelLabel.SR] * 0.7071
    if ChannelLabel.BL in ch_map:
        L += ch_map[ChannelLabel.BL] * 0.7071
    if ChannelLabel.BR in ch_map:
        R += ch_map[ChannelLabel.BR] * 0.7071
    if ChannelLabel.TFL in ch_map:
        L += ch_map[ChannelLabel.TFL] * 0.5
    if ChannelLabel.TFR in ch_map:
        R += ch_map[ChannelLabel.TFR] * 0.5
    if ChannelLabel.TBL in ch_map:
        L += ch_map[ChannelLabel.TBL] * 0.5
    if ChannelLabel.TBR in ch_map:
        R += ch_map[ChannelLabel.TBR] * 0.5
    # LFE omitted — not part of ITU-R BS.775 stereo downmix

    # Prevent clipping from summation
    peak = max(np.max(np.abs(L)), np.max(np.abs(R)), 1e-10)
    if peak > 0.99:
        L = L * (0.99 / peak)
        R = R * (0.99 / peak)

    return np.column_stack([L, R])
