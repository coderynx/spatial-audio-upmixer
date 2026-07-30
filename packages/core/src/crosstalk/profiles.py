"""Stereo / Smart-speaker / Car crosstalk-cancellation (transaural) profiles.

Each profile selects an XTC filter set (a regularized speaker-to-ear inverse,
baked offline by ``scripts/build_crosstalk_filters.py``) and a voicing chain
applied after cancellation. The voicing chain reuses
:class:`upmixer.binaural.profiles.VoicingParams` and
:func:`upmixer.binaural.voicing.apply_voicing` unchanged — post-cancellation
tone/width shaping is the same problem for speakers as for headphones. See
``docs/standards/transaural_speakers.md`` §5 for the authoritative parameter
table this module implements; the web preview's ``masteringProfiles.ts``
mirrors these values.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from upmixer.binaural.profiles import VoicingParams


class CrosstalkProfile(str, Enum):
    STEREO = "stereo"
    SMART_SPEAKER = "smart_speaker"
    CAR = "car"


@dataclass(frozen=True)
class XtcParams:
    """Speaker geometry + regularization curve for one XTC filter bake.

    Listener-relative speaker azimuths in degrees (0 = dead ahead, positive =
    left — matches :mod:`upmixer.binaural.geometry`'s convention). Symmetric
    profiles set ``azimuth_left_deg == -azimuth_right_deg``; ``car`` does not
    — an off-center driver-seat sweet spot has four independent speaker-to-ear
    paths, not a mirror pair.
    """

    azimuth_left_deg: float
    azimuth_right_deg: float
    beta_mid: float
    """Tikhonov regularization floor in the well-conditioned mid-band (see
    ``scripts/build_crosstalk_filters.py``). Higher = shallower cancellation
    but less spectral coloration — the core BACCH-style tradeoff
    (docs/standards/transaural_speakers.md §4)."""
    low_boost_hz: float = 300.0
    low_boost_factor: float = 8.0
    high_boost_hz: float = 8000.0
    high_boost_factor: float = 4.0
    taps: int = 512


XTC_FILTER_SET: dict[CrosstalkProfile, str] = {
    CrosstalkProfile.STEREO: "stereo_xtc",
    CrosstalkProfile.SMART_SPEAKER: "smart_speaker_xtc",
    CrosstalkProfile.CAR: "car_xtc",
}

XTC_PARAMS: dict[CrosstalkProfile, XtcParams] = {
    # Symmetric hi-fi pair, near-field mixing-triangle span (~60 degrees
    # total). Widest angle of the three profiles, so it earns the deepest,
    # least-regularized cancellation.
    CrosstalkProfile.STEREO: XtcParams(
        azimuth_left_deg=30.0, azimuth_right_deg=-30.0, beta_mid=0.05,
    ),
    # A single cabinet's two drivers are only centimeters apart — a much
    # narrower span than a stereo pair. Narrow span makes low-frequency
    # cancellation expensive (near-singular C at low f), so this profile
    # trades cancellation depth for coloration safety via a higher beta_mid;
    # VOICING_PARAMS below leans on stereo widening to compensate perceived
    # narrowness instead.
    CrosstalkProfile.SMART_SPEAKER: XtcParams(
        azimuth_left_deg=12.0, azimuth_right_deg=-12.0, beta_mid=0.20,
    ),
    # Asymmetric driver-seat sweet spot: the off-side (passenger-side)
    # speaker sits at a much wider effective angle than the near-side one.
    CrosstalkProfile.CAR: XtcParams(
        azimuth_left_deg=22.0, azimuth_right_deg=-42.0, beta_mid=0.10,
    ),
}

VOICING_PARAMS: dict[CrosstalkProfile, VoicingParams] = {
    CrosstalkProfile.STEREO: VoicingParams(),
    CrosstalkProfile.SMART_SPEAKER: VoicingParams(
        stereo_widen=0.20,
        bass_shelf_hz=150.0,
        bass_shelf_gain_db=1.5,
    ),
    CrosstalkProfile.CAR: VoicingParams(
        stereo_widen=0.10,
        bass_shelf_hz=120.0,
        bass_shelf_gain_db=2.5,
        presence_hz=2500.0,
        presence_gain_db=1.0,
        presence_q=0.9,
    ),
}

CROSSTALK_PROFILES: tuple[str, ...] = tuple(p.value for p in CrosstalkProfile)


def resolve_profile(name: str) -> CrosstalkProfile:
    try:
        return CrosstalkProfile(name)
    except ValueError as exc:
        raise ValueError(
            f"Unknown crosstalk profile '{name}'. Valid: {CROSSTALK_PROFILES}"
        ) from exc
