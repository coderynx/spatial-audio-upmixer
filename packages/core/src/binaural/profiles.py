"""Studio / Listening / Flat spatial audio profiles.

Each profile selects a decode-filter set (baked room + HRTF) and a voicing
chain applied after decode. See
``docs/standards/spatial_audio_engine.md`` §5 for the authoritative
parameter table this module implements; the web preview's
``masteringProfiles.ts`` mirrors these values.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BinauralProfile(str, Enum):
    STUDIO = "studio"
    LISTENING = "listening"
    FLAT = "flat"


@dataclass(frozen=True)
class VoicingParams:
    """Post-decode voicing chain parameters. All-zero/None fields = bypass."""

    crossfeed_amount: float = 0.0
    """0..1 blend of the opposite ear, low-passed — reduces excess width."""
    crossfeed_cutoff_hz: float = 700.0

    bass_shelf_hz: float = 120.0
    bass_shelf_gain_db: float = 0.0

    air_shelf_hz: float = 9000.0
    air_shelf_gain_db: float = 0.0

    presence_hz: float = 3000.0
    presence_gain_db: float = 0.0
    presence_q: float = 0.9

    stereo_widen: float = 0.0
    """0 = no change, >0 widens the mid/side balance toward side."""

    loudness_target_lkfs: float | None = None
    """Optional loudness target for the voicing stage's own gain compensation.
    ``None`` disables the extra pass (delivery-stage loudness still runs)."""


DECODE_FILTER_SET: dict[BinauralProfile, str] = {
    BinauralProfile.FLAT: "flat_o3_decode",
    BinauralProfile.STUDIO: "studio_o3_decode",
    BinauralProfile.LISTENING: "listening_o3_decode",
}

VOICING_PARAMS: dict[BinauralProfile, VoicingParams] = {
    BinauralProfile.FLAT: VoicingParams(),
    BinauralProfile.STUDIO: VoicingParams(),
    BinauralProfile.LISTENING: VoicingParams(
        crossfeed_amount=0.10,
        crossfeed_cutoff_hz=700.0,
        bass_shelf_hz=100.0,
        bass_shelf_gain_db=1.0,
        air_shelf_hz=10000.0,
        air_shelf_gain_db=4.0,
        presence_hz=3000.0,
        presence_gain_db=2.0,
        presence_q=0.9,
        stereo_widen=0.15,
        loudness_target_lkfs=None,
    ),
}

BINAURAL_PROFILES: tuple[str, ...] = tuple(p.value for p in BinauralProfile)


def resolve_profile(name: str) -> BinauralProfile:
    try:
        return BinauralProfile(name)
    except ValueError as exc:
        raise ValueError(
            f"Unknown binaural profile '{name}'. Valid: {BINAURAL_PROFILES}"
        ) from exc
