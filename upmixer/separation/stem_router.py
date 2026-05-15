"""Spatial routing: maps separated stems to multichannel output positions.

Each stem is stereo (L, R). Channels receive either stem_L, stem_R, or
the mono sum (L+R)/2, scaled by the routing gain.

Left channels  (FL, SL, BL, TFL, TBL): receive stem_L
Right channels (FR, SR, BR, TFR, TBR): receive stem_R
Center/sub     (C, LFE):               receive (stem_L + stem_R) × 0.5

Design rationale:
  Vocals  → Center: vocals always locked center in studio mixes.
            Small FL/FR for harmony spread. Light height air.
  Bass    → LFE + FL/FR: sub-bass is omni, keep front. No surrounds/height.
  Drums   → FL/FR primary (kick/snare upfront).
            SL/SR for room reflections. LFE for kick sub.
            TFL/TFR for overhead cymbals/hi-hats.
  Other   → SL/SR primary (guitars, keys, pads create the surround bed).
            FL/FR secondary. Back/height for lush pads and ambience.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt

from upmixer.formats import ChannelLabel, OutputFormat
from upmixer.config import UpmixConfig

_LEFT_CHANNELS = {ChannelLabel.FL, ChannelLabel.SL, ChannelLabel.BL, ChannelLabel.TFL, ChannelLabel.TBL}
_RIGHT_CHANNELS = {ChannelLabel.FR, ChannelLabel.SR, ChannelLabel.BR, ChannelLabel.TFR, ChannelLabel.TBR}

# Routing table: stem_name → {channel_label_value: gain}
# Only channels present in the output format are used; extras are ignored.
DEFAULT_ROUTING: dict[str, dict[str, float]] = {
    # ── 4-stem Demucs ─────────────────────────────────────────────────────────
    "Vocals": {
        "C":   0.85,
        "FL":  0.25,
        "FR":  0.25,
        "TFL": 0.15,
        "TFR": 0.15,
    },
    "Bass": {
        "FL":  0.50,
        "FR":  0.50,
        "LFE": 0.90,
    },
    "Drums": {
        "FL":  0.60,
        "FR":  0.60,
        "SL":  0.25,
        "SR":  0.25,
        "BL":  0.10,
        "BR":  0.10,
        "LFE": 0.40,
        "TFL": 0.20,   # overheads / cymbals
        "TFR": 0.20,
    },
    "Other": {
        "FL":  0.35,
        "FR":  0.35,
        "SL":  0.55,
        "SR":  0.55,
        "BL":  0.30,
        "BR":  0.30,
        "TFL": 0.20,
        "TFR": 0.20,
        "TBL": 0.15,
        "TBR": 0.15,
    },

    # ── 6-stem Demucs (htdemucs_6s) ───────────────────────────────────────────
    "Guitar": {
        # Guitars are typically wide-panned → side surrounds primary
        "FL":  0.30,
        "FR":  0.30,
        "SL":  0.60,
        "SR":  0.60,
        "BL":  0.25,
        "BR":  0.25,
        "TFL": 0.15,
        "TFR": 0.15,
    },
    "Piano": {
        # Piano spans the front image, slight surround bloom
        "C":   0.20,
        "FL":  0.50,
        "FR":  0.50,
        "SL":  0.25,
        "SR":  0.25,
        "TFL": 0.15,
        "TFR": 0.15,
    },

    # ── RoFormer 2-stem (Vocals + Instrumental) ────────────────────────────────
    "Instrumental": {
        # Full band minus vocals: FL/FR primary, surrounds for ambience
        "FL":  0.55,
        "FR":  0.55,
        "SL":  0.45,
        "SR":  0.45,
        "BL":  0.25,
        "BR":  0.25,
        "LFE": 0.50,
        "TFL": 0.15,
        "TFR": 0.15,
        "TBL": 0.10,
        "TBR": 0.10,
    },

    # ── Karaoke / vocal splitter models ───────────────────────────────────────
    "Lead Vocals": {
        "C":   0.90,
        "FL":  0.20,
        "FR":  0.20,
        "TFL": 0.15,
        "TFR": 0.15,
    },
    "Backing Vocals": {
        # Harmony vocals spread wide behind the lead
        "FL":  0.40,
        "FR":  0.40,
        "SL":  0.35,
        "SR":  0.35,
        "TFL": 0.20,
        "TFR": 0.20,
    },
}


class StemRouter:
    """Mix separated stems into output channels using the spatial routing table."""

    def __init__(
        self,
        config: UpmixConfig,
        output_fmt: OutputFormat,
        sample_rate: int,
        routing: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._config = config
        self._fmt = output_fmt
        self._routing = routing or DEFAULT_ROUTING
        self._sr = sample_rate
        self._lfe_sos = butter(
            config.lfe_filter_order,
            config.lfe_cutoff_hz / (sample_rate / 2.0),
            btype="low",
            output="sos",
        )
        self._lfe_gain = config.lfe_gain

    def route(
        self,
        stems: dict[str, np.ndarray],
        n_samples: int,
    ) -> dict[str, np.ndarray]:
        """Mix stems into output channels.

        Args:
            stems: Dict stem_name → ndarray (n_samples, 2) stereo float.
            n_samples: Expected output length.

        Returns:
            Dict channel_name → 1D float64 array of length n_samples.
        """
        channels: dict[str, np.ndarray] = {
            label.value: np.zeros(n_samples, dtype=np.float64)
            for label in self._fmt.channels
        }

        for stem_name, audio in stems.items():
            if stem_name not in self._routing:
                continue
            stem_routing = self._routing[stem_name]

            n = min(len(audio), n_samples)
            stem_L = audio[:n, 0].astype(np.float64)
            stem_R = audio[:n, 1].astype(np.float64) if audio.shape[1] > 1 else stem_L.copy()
            stem_mono = (stem_L + stem_R) * 0.5

            # LFE pre-filtered mono (avoid redundant filtering per-channel)
            lfe_signal: np.ndarray | None = None

            for label in self._fmt.channels:
                ch = label.value
                if ch not in stem_routing:
                    continue
                gain = stem_routing[ch]

                if label == ChannelLabel.LFE:
                    if lfe_signal is None:
                        lfe_signal = self._lfe_gain * sosfilt(self._lfe_sos, stem_mono)
                    channels[ch][:n] += gain * lfe_signal
                elif label in _LEFT_CHANNELS:
                    channels[ch][:n] += gain * stem_L
                elif label in _RIGHT_CHANNELS:
                    channels[ch][:n] += gain * stem_R
                elif label == ChannelLabel.C:
                    channels[ch][:n] += gain * stem_mono

        return channels
