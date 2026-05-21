"""Spatial routing: maps separated stems to multichannel output positions.

Routing philosophy (Dolby Atmos Music best practices):
  Front bed (FL/FR/C) = song foundation — vocals, kick, snare, bass, melody.
    Center anchors: lead vocals, kick/snare transients, bass mono low-end.
    NOT vocals-only center — that sounds pasted-in and unnatural.

  LFE = effect send, not primary bass channel.
    Core low-end lives in FL/FR; LFE adds weight at specific transient moments.

  Surround (SL/SR/BL/BR) = diffuse only.
    Room reverb, ambience, crowd. Keep rhythmic core in front.
    NO dominant surround bass — muddy and disorienting.

  Heights (TFL/TFR/TBL/TBR) = space/elevation, not dry instruments.
    Reverb tails, ambient textures, pad swells, overhead mics simulation.
    Backing vocals in choruses get height for expansion.
    Wide sustained content belongs here more than transient direct sounds.

  Backing vocals ≠ lead vocals.
    Lead: center-anchored phantom (C dominant + light FL/FR).
    Backing: widened in front L/R + strong height for chorus expansion.

Zone-aware routing for multichannel input:
  Each stem tagged "StemName@zone" where zone ∈ {front, surround, back,
  height_front, height_back}. Zone stems route to their spatial home.

Center (C) and LFE from multichannel inputs are passed through directly and
excluded from stem routing via the passthrough_channels set.

Channel assignment within each zone:
  Left channels  (FL, SL, BL, TFL, TBL): receive stem_L
  Right channels (FR, SR, BR, TFR, TBR): receive stem_R
  C / LFE:                                receive (stem_L + stem_R) × 0.5
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt

from upmixer.formats import ChannelLabel, OutputFormat
from upmixer.config import UpmixConfig
from upmixer.separation.stem_analyzer import StemFeatures

_LEFT_CHANNELS  = {ChannelLabel.FL, ChannelLabel.SL, ChannelLabel.BL, ChannelLabel.TFL, ChannelLabel.TBL}
_RIGHT_CHANNELS = {ChannelLabel.FR, ChannelLabel.SR, ChannelLabel.BR, ChannelLabel.TFR, ChannelLabel.TBR}

# Channel type subsets for content-aware gain scaling
_FRONT_CHANNELS    = {ChannelLabel.FL, ChannelLabel.FR}
_SURROUND_CHANNELS = {ChannelLabel.SL, ChannelLabel.SR, ChannelLabel.BL, ChannelLabel.BR}
_HEIGHT_CHANNELS   = {ChannelLabel.TFL, ChannelLabel.TFR, ChannelLabel.TBL, ChannelLabel.TBR}

# Stem names considered "vocal" for C→FL/FR gain redistribution.
_VOCAL_STEM_NAMES: frozenset[str] = frozenset({
    "Vocals", "Lead Vocals", "Backing Vocals",
})


def _content_scale(features: StemFeatures, label: ChannelLabel) -> float:
    """Per-channel multiplicative scale driven by stem content analysis.

    Applied on top of the static routing table gain so spatial placement
    adapts to the actual audio rather than using fixed gains.

    LFE     : boosted by low-frequency energy (bass/kick).
    Center  : boosted when content is mono (vocals, bass, kick) — less for wide stereo.
    Height  : two paths — cymbal/air (HF+transient) OR reverb tail (wide+sustained).
              Takes the stronger path so both use-cases score well.
    Surround: wide + sustained (reverb, room ambience, diffuse guitars).
    Front   : stable anchor; slightly boosted for direct/percussive sounds.
    """
    w = features.stereo_width       # [0,1] — 1 = wide stereo
    h = features.high_freq_ratio    # [0,1] — 1 = treble-heavy
    b = features.low_freq_ratio     # [0,1] — 1 = bass-heavy
    t = features.transient_ratio    # [0,1] — 1 = percussive / transient

    if label == ChannelLabel.LFE:
        # Low-freq content → stronger weight send
        return 0.4 + 0.9 * b

    if label == ChannelLabel.C:
        # Mono content → stronger center (vocals, kick, bass, keys in mono)
        # Wide stereo content (guitars, pads) should phantom through FL/FR
        return 0.55 + 0.65 * (1.0 - w)

    if label in _HEIGHT_CHANNELS:
        # Path 1: cymbal / air — high-freq + transient (overhead mics, bright room)
        cymbal_score  = 0.6 * h + 0.4 * t
        # Path 2: reverb tail / ambience — wide + sustained (pads, room decay)
        ambient_score = 0.5 * w + 0.5 * (1.0 - t)
        # Take the stronger of the two so both cymbals and pads get height presence
        return 0.25 + 0.72 * max(cymbal_score, ambient_score)

    if label in _SURROUND_CHANNELS:
        # Wide + sustained content (room reverb, ambience, distant guitars)
        sustain = 1.0 - t
        return 0.20 + 0.80 * (0.60 * w + 0.40 * sustain)

    # Front channels (FL, FR): stable anchor — slight boost for direct/percussive
    return 0.60 + 0.40 * (0.55 * t + 0.45 * (1.0 - w))


# ── Zone routing tables ────────────────────────────────────────────────────────
# Design rules:
#  • front zone: direct/dry → front bed dominant. Bass and drums use center anchor.
#  • surround zone: room/reverb tails → surrounds + heights. NO surround bass.
#  • back zone: rear content → BL/BR + rear heights.
#  • height_front / height_back: overhead energy → TFL/TFR and TBL/TBR.

ZONE_ROUTING: dict[str, dict[str, dict[str, float]]] = {
    "front": {
        # Vocals from front speakers: center-anchored with phantom support
        "Vocals":         {"C": 0.72, "FL": 0.28, "FR": 0.28, "TFL": 0.08, "TFR": 0.08},
        # Bass: front L/R primary, center mono anchor, LFE as effect send
        "Bass":           {"FL": 0.65, "FR": 0.65, "C": 0.22, "LFE": 0.75},
        # Drums: kick/snare center anchor + front + slight height for cymbals/room
        "Drums":          {"C": 0.22, "FL": 0.58, "FR": 0.58, "LFE": 0.32,
                           "TFL": 0.18, "TFR": 0.18},
        # Other/pads: front presence + height for ambience/reverb component
        "Other":          {"FL": 0.38, "FR": 0.38, "SL": 0.18, "SR": 0.18,
                           "TFL": 0.30, "TFR": 0.30},
        # Guitar: strong front, slight surround room depth, subtle air
        "Guitar":         {"FL": 0.55, "FR": 0.55, "SL": 0.15, "SR": 0.15,
                           "TFL": 0.10, "TFR": 0.10},
        # Piano: melody anchor (slight C), natural room in FL/FR
        "Piano":          {"C": 0.18, "FL": 0.58, "FR": 0.58,
                           "TFL": 0.12, "TFR": 0.12},
        # Instrumental (2-stem): balanced front bed with slight height
        "Instrumental":   {"C": 0.12, "FL": 0.62, "FR": 0.62, "LFE": 0.40,
                           "TFL": 0.15, "TFR": 0.15},
        # Lead vocals: center-dominant phantom (C+FL+FR natural blend)
        "Lead Vocals":    {"C": 0.80, "FL": 0.22, "FR": 0.22,
                           "TFL": 0.07, "TFR": 0.07},
        # Backing vocals: widened front + height (chorus expansion)
        "Backing Vocals": {"FL": 0.48, "FR": 0.48,
                           "TFL": 0.25, "TFR": 0.25},
        # DrumSep sub-stems (direct/dry content → front-dominant)
        "Kick":           {"C": 0.35, "FL": 0.55, "FR": 0.55, "LFE": 0.90},
        "Snare":          {"C": 0.40, "FL": 0.62, "FR": 0.62,
                           "TFL": 0.10, "TFR": 0.10},
        "Toms":           {"C": 0.15, "FL": 0.58, "FR": 0.58, "LFE": 0.22},
        "Hi-Hat":         {"FL": 0.40, "FR": 0.40, "TFL": 0.50, "TFR": 0.50},
        "Ride":           {"FL": 0.35, "FR": 0.35, "TFL": 0.55, "TFR": 0.55},
        "Crash":          {"FL": 0.32, "FR": 0.32, "TFL": 0.60, "TFR": 0.60},
        # Crowd: front zone has minimal crowd — push to room/sides
        "Crowd":          {"SL": 0.30, "SR": 0.30, "TFL": 0.10, "TFR": 0.10},
    },
    # surround: room/reverb tails, wide ambience → surrounds + heights, no front injection
    "surround": {
        "Vocals":         {"SL": 0.22, "SR": 0.22, "TBL": 0.14, "TBR": 0.14},
        # Bass in surround zone → LFE send only (no surround bass = muddy)
        "Bass":           {"LFE": 0.20},
        "Drums":          {"SL": 0.52, "SR": 0.52, "BL": 0.22, "BR": 0.22,
                           "TFL": 0.15, "TFR": 0.15, "TBL": 0.22, "TBR": 0.22},
        "Other":          {"SL": 0.62, "SR": 0.62, "BL": 0.32, "BR": 0.32,
                           "TFL": 0.28, "TFR": 0.28, "TBL": 0.32, "TBR": 0.32},
        "Guitar":         {"SL": 0.58, "SR": 0.58, "BL": 0.22, "BR": 0.22,
                           "TBL": 0.12, "TBR": 0.12},
        "Piano":          {"SL": 0.38, "SR": 0.38, "TBL": 0.18, "TBR": 0.18},
        "Instrumental":   {"SL": 0.48, "SR": 0.48, "BL": 0.22, "BR": 0.22,
                           "LFE": 0.15, "TBL": 0.18, "TBR": 0.18},
        # Lead vocals: barely touch surround — don't defocus the lead
        "Lead Vocals":    {"SL": 0.10, "SR": 0.10},
        "Backing Vocals": {"SL": 0.38, "SR": 0.38,
                           "TFL": 0.18, "TFR": 0.18, "TBL": 0.22, "TBR": 0.22},
        # DrumSep sub-stems (room/reverb component)
        "Kick":           {"LFE": 0.25},
        "Snare":          {"SL": 0.30, "SR": 0.30},
        "Toms":           {"SL": 0.40, "SR": 0.40},
        "Hi-Hat":         {"SL": 0.22, "SR": 0.22,
                           "TFL": 0.28, "TFR": 0.28, "TBL": 0.18, "TBR": 0.18},
        "Ride":           {"SL": 0.18, "SR": 0.18, "TBL": 0.22, "TBR": 0.22},
        "Crash":          {"SL": 0.28, "SR": 0.28, "TBL": 0.30, "TBR": 0.30},
        # Crowd: surround zone is the primary home for audience noise (kept low)
        "Crowd":          {"SL": 0.32, "SR": 0.32, "BL": 0.20, "BR": 0.20,
                           "TBL": 0.18, "TBR": 0.18},
    },
    # back: deep rear energy → BL/BR + rear heights
    "back": {
        "Vocals":         {"BL": 0.20, "BR": 0.20},
        "Bass":           {"LFE": 0.15},
        "Drums":          {"BL": 0.50, "BR": 0.50, "TBL": 0.28, "TBR": 0.28},
        "Other":          {"BL": 0.58, "BR": 0.58, "TBL": 0.42, "TBR": 0.42},
        "Guitar":         {"BL": 0.42, "BR": 0.42, "TBL": 0.18, "TBR": 0.18},
        "Piano":          {"BL": 0.28, "BR": 0.28, "TBL": 0.15, "TBR": 0.15},
        "Instrumental":   {"BL": 0.42, "BR": 0.42, "TBL": 0.28, "TBR": 0.28},
        "Lead Vocals":    {"BL": 0.08, "BR": 0.08},
        "Backing Vocals": {"BL": 0.32, "BR": 0.32, "TBL": 0.25, "TBR": 0.25},
        # DrumSep sub-stems (deep rear room)
        "Kick":           {"LFE": 0.18},
        "Snare":          {"BL": 0.20, "BR": 0.20},
        "Toms":           {"BL": 0.35, "BR": 0.35},
        "Hi-Hat":         {"TBL": 0.42, "TBR": 0.42},
        "Ride":           {"BL": 0.20, "BR": 0.20, "TBL": 0.40, "TBR": 0.40},
        "Crash":          {"BL": 0.28, "BR": 0.28, "TBL": 0.48, "TBR": 0.48},
        # Crowd: rear zone → diffuse behind listener (kept subdued)
        "Crowd":          {"BL": 0.30, "BR": 0.30, "TBL": 0.22, "TBR": 0.22},
    },
    # height_front: overhead front energy → TFL/TFR primary (ambience, reverb, cymbals)
    "height_front": {
        "Vocals":         {"TFL": 0.32, "TFR": 0.32},
        "Bass":           {},
        "Drums":          {"TFL": 0.58, "TFR": 0.58, "TBL": 0.18, "TBR": 0.18},
        "Other":          {"TFL": 0.68, "TFR": 0.68, "TBL": 0.28, "TBR": 0.28},
        "Guitar":         {"TFL": 0.45, "TFR": 0.45, "TBL": 0.10, "TBR": 0.10},
        "Piano":          {"TFL": 0.42, "TFR": 0.42},
        "Instrumental":   {"TFL": 0.52, "TFR": 0.52, "TBL": 0.18, "TBR": 0.18},
        "Lead Vocals":    {"TFL": 0.22, "TFR": 0.22},
        "Backing Vocals": {"TFL": 0.50, "TFR": 0.50},  # chorus expansion overhead
        # DrumSep sub-stems (overhead/height content)
        "Kick":           {},
        "Snare":          {"TFL": 0.15, "TFR": 0.15},
        "Toms":           {"TFL": 0.22, "TFR": 0.22},
        "Hi-Hat":         {"TFL": 0.72, "TFR": 0.72},
        "Ride":           {"TFL": 0.68, "TFR": 0.68},
        "Crash":          {"TFL": 0.80, "TFR": 0.80, "TBL": 0.25, "TBR": 0.25},
        # Crowd: subtle front overhead diffusion
        "Crowd":          {"TFL": 0.20, "TFR": 0.20, "TBL": 0.12, "TBR": 0.12},
    },
    # height_back: rear overhead energy → TBL/TBR primary
    "height_back": {
        "Vocals":         {"TBL": 0.25, "TBR": 0.25},
        "Bass":           {},
        "Drums":          {"TBL": 0.52, "TBR": 0.52},
        "Other":          {"TBL": 0.72, "TBR": 0.72},
        "Guitar":         {"TBL": 0.42, "TBR": 0.42},
        "Piano":          {"TBL": 0.38, "TBR": 0.38},
        "Instrumental":   {"TBL": 0.58, "TBR": 0.58},
        "Lead Vocals":    {"TBL": 0.10, "TBR": 0.10},
        "Backing Vocals": {"TBL": 0.50, "TBR": 0.50},
        # DrumSep sub-stems (rear overhead content)
        "Kick":           {},
        "Snare":          {"TBL": 0.12, "TBR": 0.12},
        "Toms":           {"TBL": 0.18, "TBR": 0.18},
        "Hi-Hat":         {"TBL": 0.52, "TBR": 0.52},
        "Ride":           {"TBL": 0.62, "TBR": 0.62},
        "Crash":          {"TBL": 0.75, "TBR": 0.75},
        # Crowd: rear height diffusion (subdued)
        "Crowd":          {"TBL": 0.28, "TBR": 0.28},
    },
}

# ── Default routing (stereo input → full multichannel) ─────────────────────────
# Designed for stereo source material upmixed to full 3D bed.
# Key principle: instruments must anchor front first; spatial spread is secondary.

DEFAULT_ROUTING: dict[str, dict[str, float]] = {
    # Generic vocals: center-anchored with natural phantom support
    # Center not at 1.0 — phantom center (L+R) feels more natural than single speaker
    "Vocals": {
        "C":   0.70,
        "FL":  0.28,
        "FR":  0.28,
        "TFL": 0.12,   # subtle reverb tail elevation
        "TFR": 0.12,
    },
    # Bass: front L/R primary (the body of bass lives here), LFE for weight/transient
    # Adding center anchor for mono fundamental coherence
    "Bass": {
        "FL":  0.65,
        "FR":  0.65,
        "C":   0.20,   # mono low-end anchor (kick sub fundamental)
        "LFE": 0.80,   # effect send for sub weight
    },
    # Drums: center anchor (kick/snare), strong front, overhead sim in heights
    # Room mics → surround sends; LFE = kick sub weight
    "Drums": {
        "C":   0.20,   # kick/snare direct anchor
        "FL":  0.55,
        "FR":  0.55,
        "SL":  0.18,   # room overhead sim
        "SR":  0.18,
        "LFE": 0.32,   # kick sub weight send (not dominant)
        "TFL": 0.20,   # cymbal / overhead mic sim
        "TFR": 0.20,
        "TBL": 0.08,   # rear room bloom
        "TBR": 0.08,
    },
    # Other: pads, textures, atmospherics, synths → diffuse + STRONG height
    # This is the reverb/ambience stem — heights are its natural home
    "Other": {
        "FL":  0.28,
        "FR":  0.28,
        "SL":  0.48,
        "SR":  0.48,
        "BL":  0.22,
        "BR":  0.22,
        "TFL": 0.42,   # strong height for ambience/reverb
        "TFR": 0.42,
        "TBL": 0.28,
        "TBR": 0.28,
    },
    # Guitar: front-dominant (it IS the song), natural room in surrounds
    # Previous: FL=0.30 SL=0.60 — guitar more surround than front = wrong
    "Guitar": {
        "FL":  0.52,
        "FR":  0.52,
        "SL":  0.35,   # room width / reverb
        "SR":  0.35,
        "BL":  0.12,
        "BR":  0.12,
        "TFL": 0.12,   # subtle air
        "TFR": 0.12,
    },
    # Piano: melody instrument → front-dominant + slight center for mono melody
    "Piano": {
        "C":   0.18,   # slight center for mono melody lines
        "FL":  0.55,
        "FR":  0.55,
        "SL":  0.22,   # natural room decay
        "SR":  0.22,
        "TFL": 0.15,
        "TFR": 0.15,
    },
    # Instrumental (2-stem / roformer): full mix without vocals
    "Instrumental": {
        "C":   0.15,
        "FL":  0.55,
        "FR":  0.55,
        "SL":  0.38,
        "SR":  0.38,
        "BL":  0.18,
        "BR":  0.18,
        "LFE": 0.45,
        "TFL": 0.22,
        "TFR": 0.22,
        "TBL": 0.12,
        "TBR": 0.12,
    },
    # Lead vocals: center-dominant phantom — C strong but not hard-panned
    # Light FL/FR phantom prevents the "pasted in mono" effect
    # TFL/TFR: reverb tail elevation only (not the dry vocal)
    "Lead Vocals": {
        "C":   0.80,
        "FL":  0.20,   # phantom support for naturalness
        "FR":  0.20,
        "TFL": 0.08,   # reverb elevation
        "TFR": 0.08,
    },
    # Backing vocals: widened in front, height for chorus expansion
    # NOT center-anchored — contrast with lead, give width and elevation
    "Backing Vocals": {
        "FL":  0.45,
        "FR":  0.45,
        "SL":  0.22,   # gentle diffusion
        "SR":  0.22,
        "TFL": 0.30,   # chorus overhead expansion
        "TFR": 0.30,
        "TBL": 0.12,
        "TBR": 0.12,
    },
    # ── DrumSep sub-stems ──────────────────────────────────────────────────────
    # Kick: sub-bass punch in front + strong LFE send
    "Kick": {
        "C":   0.30,
        "FL":  0.55,
        "FR":  0.55,
        "LFE": 0.85,
    },
    # Snare: centre-anchored crack + front transient
    "Snare": {
        "C":   0.35,
        "FL":  0.60,
        "FR":  0.60,
        "TFL": 0.08,   # subtle overhead snap
        "TFR": 0.08,
    },
    # Toms: front-focused with natural room spill
    "Toms": {
        "C":   0.12,
        "FL":  0.62,
        "FR":  0.62,
        "SL":  0.15,
        "SR":  0.15,
        "LFE": 0.20,
    },
    # Hi-Hat: strong overhead height + front body
    "Hi-Hat": {
        "FL":  0.42,
        "FR":  0.42,
        "TFL": 0.55,
        "TFR": 0.55,
        "TBL": 0.10,
        "TBR": 0.10,
    },
    # Ride: height-dominant bright overhead
    "Ride": {
        "FL":  0.38,
        "FR":  0.38,
        "TFL": 0.60,
        "TFR": 0.60,
    },
    # Crash: wide impact burst across front + heights
    "Crash": {
        "FL":  0.35,
        "FR":  0.35,
        "SL":  0.20,
        "SR":  0.20,
        "TFL": 0.65,
        "TFR": 0.65,
        "TBL": 0.12,
        "TBR": 0.12,
    },
    # ── Crowd pre-isolation stem ───────────────────────────────────────────────
    # Crowd: ambient audience noise → immersive surround envelope.
    # Gains deliberately conservative (~half of instrument stems) so crowd
    # stays clearly in the background.  Surround/rear placement creates the
    # live-venue atmosphere without competing with the instruments.
    "Crowd": {
        "SL":  0.28,
        "SR":  0.28,
        "BL":  0.20,
        "BR":  0.20,
        "TBL": 0.15,
        "TBR": 0.15,
        "TFL": 0.06,
        "TFR": 0.06,
    },
}


class StemRouter:
    """Mix separated stems into output channels using spatial routing tables.

    Stems keyed as "StemName@zone" are routed via ZONE_ROUTING[zone][StemName].
    Unzoned stems (no "@") fall back to DEFAULT_ROUTING or custom_routing.

    Channels listed in passthrough_channels are skipped during routing; the
    pipeline injects those channels directly from the source material.
    """

    def __init__(
        self,
        config: UpmixConfig,
        output_fmt: OutputFormat,
        sample_rate: int,
        routing: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._config = config
        self._fmt = output_fmt
        self._fallback_routing = routing or DEFAULT_ROUTING
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
        passthrough_channels: set[str] | None = None,
        stem_features: dict[str, StemFeatures] | None = None,
    ) -> dict[str, np.ndarray]:
        """Mix stems into output channels.

        Args:
            stems: Dict "StemName[@zone]" → ndarray (n_samples, 2) stereo float.
            n_samples: Expected output length.
            passthrough_channels: Channel names to skip (injected directly by caller).
            stem_features: Optional per-stem content analysis (from stem_analyzer).
                When provided, static routing table gains are scaled per channel type
                based on the stem's stereo width, frequency content, and transient
                density — making spatial placement adapt to the actual audio.

        Returns:
            Dict channel_name → 1D float64 array of length n_samples.
        """
        skip = passthrough_channels or set()
        channels: dict[str, np.ndarray] = {
            label.value: np.zeros(n_samples, dtype=np.float64)
            for label in self._fmt.channels
        }

        for stem_key, audio in stems.items():
            if "@" in stem_key:
                stem_name, zone = stem_key.rsplit("@", 1)
                stem_routing = (
                    ZONE_ROUTING.get(zone, {}).get(stem_name)
                    or self._fallback_routing.get(stem_name)
                )
            else:
                stem_name = stem_key
                stem_routing = self._fallback_routing.get(stem_name)

            if not stem_routing:
                continue

            # Content-aware features for this stem (None → no scaling, gains unchanged)
            features = stem_features.get(stem_key) if stem_features else None

            n = min(len(audio), n_samples)
            stem_L = audio[:n, 0].astype(np.float64)
            stem_R = audio[:n, 1].astype(np.float64) if audio.shape[1] > 1 else stem_L.copy()
            stem_mono = (stem_L + stem_R) * 0.5

            # C-to-FL/FR gain redirect — vocal stems only.
            #
            # When C is passthrough (multichannel input), the Vocals→C routing
            # gain (e.g. 0.72) is discarded by the skip check below.  FL/FR
            # only receive the "phantom support" gain (e.g. 0.28), leaving
            # vocals underrepresented in the front bed vs. the original mix.
            #
            # Fix: for vocal stems, redirect the C gain to FL and FR at 0.5×
            # each (equal L/R split of the mono centre signal).
            # Result: FL gain 0.28 → 0.64 — closer to original FL vocal content.
            # The redirect can never exceed original FL level (0.64 < 1.0).
            #
            # Non-vocal stems (Bass, Drums, Other…): never redirected.
            # Stereo input: C not in skip → c_redirect = 0 → unchanged.
            c_redirect: float = 0.0
            if "C" in skip and "C" in stem_routing and stem_name in _VOCAL_STEM_NAMES:
                c_redirect = stem_routing["C"] * 0.5

            lfe_signal: np.ndarray | None = None

            for label in self._fmt.channels:
                ch = label.value
                if ch in skip or ch not in stem_routing:
                    continue

                # Apply content scale on top of static table gain
                gain = stem_routing[ch]
                # Add redirected C gain to FL/FR when C is passthrough
                if c_redirect > 0.0 and label in (ChannelLabel.FL, ChannelLabel.FR):
                    gain += c_redirect
                if features is not None:
                    gain = gain * _content_scale(features, label)

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

    def get_routing(self, stem_key: str) -> dict[str, float] | None:
        """Return effective routing dict for a stem key ("StemName" or "StemName@zone")."""
        if "@" in stem_key:
            stem_name, zone = stem_key.rsplit("@", 1)
            return (
                ZONE_ROUTING.get(zone, {}).get(stem_name)
                or self._fallback_routing.get(stem_name)
            )
        return self._fallback_routing.get(stem_key)
