"""Spatial Audio Engine: crosstalk-cancellation (transaural) speaker rendering.

Renders a discrete multichannel bed to speaker-ready stereo by reusing the
anechoic binaural ear signals (``upmixer.binaural.render_binaural``, "flat"
profile) and applying a profile-selected 2x2 crosstalk-cancellation FIR
matrix, so the intended binaural cues survive playback through real stereo
loudspeakers instead of collapsing into a fold-down mono image. See
``docs/standards/transaural_speakers.md`` for the full signal-graph contract
shared with the web preview engine.
"""
from upmixer.crosstalk.profiles import CROSSTALK_PROFILES, CrosstalkProfile
from upmixer.crosstalk.renderer import render_crosstalk, render_crosstalk_delivery

__all__ = [
    "CrosstalkProfile",
    "CROSSTALK_PROFILES",
    "render_crosstalk",
    "render_crosstalk_delivery",
]
