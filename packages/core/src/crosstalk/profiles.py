"""Stereo / Smart-speaker / Car / Laptop / Phone crosstalk-cancellation (transaural) profiles.

Each profile selects an XTC filter set (a regularized speaker-to-ear inverse,
baked offline by ``scripts/build_crosstalk_filters.py``) and a voicing chain
applied to the ear signals before cancellation. The voicing chain reuses
:class:`upmixer.binaural.profiles.VoicingParams` and
:func:`upmixer.binaural.voicing.apply_voicing` unchanged — tone/width shaping
is the same problem for speakers as for headphones. See
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
    LAPTOP = "laptop"
    PHONE = "phone"


@dataclass(frozen=True)
class XtcParams:
    """Speaker geometry + coloration budget for one XTC filter bake.

    Listener-relative speaker azimuths in degrees (0 = dead ahead, positive =
    left — matches :mod:`upmixer.binaural.geometry`'s convention). Symmetric
    profiles set ``azimuth_left_deg == -azimuth_right_deg``; ``car`` does not
    — an off-center driver-seat sweet spot has four independent speaker-to-ear
    paths, not a mirror pair.
    """

    azimuth_left_deg: float
    azimuth_right_deg: float
    gamma_db: float
    """Maximum tolerable spectral coloration at the speakers, in dB. The bake
    regularizes each frequency bin by exactly as much as it takes to keep the
    filter matrix norm under this ceiling, and no more — Choueiri's
    frequency-dependent prescription (docs/standards/transaural_speakers.md
    §4.2). Higher = deeper cancellation, more coloration."""
    xtc_lo_hz: float = 150.0
    """Cancellation fades out below this; the band carries no usable
    localization cues and inverting it is hopeless for any real span."""
    xtc_hi_hz: float = 6000.0
    """Cancellation fades out above this: the head already separates the ears
    there, and forcing more only shrinks the sweet spot."""
    taps: int = 1024


XTC_FILTER_SET: dict[CrosstalkProfile, str] = {
    CrosstalkProfile.STEREO: "stereo_xtc",
    CrosstalkProfile.SMART_SPEAKER: "smart_speaker_xtc",
    CrosstalkProfile.CAR: "car_xtc",
    CrosstalkProfile.LAPTOP: "laptop_xtc",
    CrosstalkProfile.PHONE: "phone_xtc",
}

XTC_PARAMS: dict[CrosstalkProfile, XtcParams] = {
    # Symmetric hi-fi pair, near-field mixing-triangle span (~60 degrees
    # total). Widest, best-conditioned span, so it can spend the largest
    # coloration budget on cancellation depth.
    CrosstalkProfile.STEREO: XtcParams(
        azimuth_left_deg=30.0, azimuth_right_deg=-30.0, gamma_db=7.0,
    ),
    # A single cabinet's two drivers are only centimeters apart — a much
    # narrower span than a stereo pair, so the same budget buys far less
    # depth. VOICING_PARAMS below leans on stereo widening to compensate
    # perceived narrowness instead.
    CrosstalkProfile.SMART_SPEAKER: XtcParams(
        azimuth_left_deg=12.0, azimuth_right_deg=-12.0, gamma_db=4.0,
        xtc_lo_hz=180.0,
    ),
    # Asymmetric driver-seat sweet spot: the off-side (passenger-side)
    # speaker sits at a much wider effective angle than the near-side one.
    CrosstalkProfile.CAR: XtcParams(
        azimuth_left_deg=22.0, azimuth_right_deg=-42.0, gamma_db=6.0,
    ),
    # A laptop's two speakers sit near the chassis's front edge, only
    # centimeters apart — narrower than a stereo pair but slightly wider than
    # a soundbar cabinet's span. Thin, bass-poor drivers, so voicing leans on
    # a bass shelf plus a presence lift in addition to widening.
    CrosstalkProfile.LAPTOP: XtcParams(
        azimuth_left_deg=14.0, azimuth_right_deg=-14.0, gamma_db=5.0,
        xtc_lo_hz=180.0,
    ),
    # A phone's two speakers are only ~5cm apart — the narrowest, most
    # ill-conditioned span of any profile, so it gets the tightest coloration
    # budget and leans hardest on voicing to compensate: phone drivers have
    # essentially no low end.
    CrosstalkProfile.PHONE: XtcParams(
        azimuth_left_deg=6.0, azimuth_right_deg=-6.0, gamma_db=3.0,
        xtc_lo_hz=200.0,
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
    CrosstalkProfile.LAPTOP: VoicingParams(
        stereo_widen=0.25,
        bass_shelf_hz=160.0,
        bass_shelf_gain_db=2.0,
        presence_hz=3000.0,
        presence_gain_db=1.0,
        presence_q=0.9,
    ),
    CrosstalkProfile.PHONE: VoicingParams(
        stereo_widen=0.30,
        bass_shelf_hz=180.0,
        bass_shelf_gain_db=3.0,
        presence_hz=3000.0,
        presence_gain_db=1.5,
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
