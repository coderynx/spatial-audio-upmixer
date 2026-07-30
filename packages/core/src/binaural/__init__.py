"""Spatial Audio Engine: virtual-loudspeaker HOA binaural rendering.

Renders a discrete multichannel bed (e.g. 7.1.4) to headphone stereo by
encoding each speaker to order-3 ambisonics, summing to one HOA bus, and
decoding through a profile-selected bank of ambisonic-to-binaural FIR
filters. See ``docs/standards/spatial_audio_engine.md`` for the full
signal-graph contract shared with the web preview engine.
"""
from upmixer.binaural.profiles import BINAURAL_PROFILES, BinauralProfile
from upmixer.binaural.renderer import render_binaural, render_binaural_delivery

__all__ = [
    "BinauralProfile",
    "BINAURAL_PROFILES",
    "render_binaural",
    "render_binaural_delivery",
]
