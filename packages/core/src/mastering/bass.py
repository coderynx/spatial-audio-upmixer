"""Multichannel bass management for the mastering bus.

Shaped for spatial audio, where the low end has to behave as one source
arriving from the whole speaker array rather than as N independent copies.
The reference architecture is the one the Dolby Atmos Renderer exposes under
Speaker preferences → Bass management: extract the low band *before* it is
distributed across speaker feeds, then hand it back out deliberately.

Four processing stages (all optional):

1. **Low-end EQ** — Butterworth bandpass/lowpass gain on sub-bass (<80 Hz)
   and mid-bass (80–200 Hz) across every non-LFE channel.
2. **LF unification** — the low band of every non-LFE channel is summed to one
   mono bus and redistributed over :data:`LF_SPREADS`. The complement is taken
   by subtraction (``high = x - low``), which is exact for any low-pass, so
   nothing is lost at the crossover. Target weights sum to 1, which is what
   leaves the *coherent* low-frequency level unchanged: N channels carrying
   correlated bass sum at 20·log10(N) dB, so redistributing without that
   invariant is a level error, not a spread.
3. **Mid-bass decorrelation** — an ERB-warped allpass cascade per channel over
   the 100-300 Hz band, gated to the sustained part of the signal. Below
   ``unify_hz`` nothing is touched: that band is mono by design. Decorrelating
   here is what the research attributes the "enveloping" multichannel low end
   to, and it deliberately lowers the *summed* level in the band while leaving
   each channel's own level alone.
4. **Transient shaping and harmonic excitation** — both run once, on the mono
   bus, rather than per channel. The exciter is kept off the LFE: tanh's third
   and fifth harmonics land above the 120 Hz the channel is band-limited to.
5. **LFE gain trim** — simple linear gain on the LFE channel only (dB).

LFE modes
---------
``off``    LFE keeps whatever the router put there; unification stays on the bed.
``add``    Mains keep the whole bus (weights sum to 1); the LFE receives an
           extra copy at ``lfe_send``. Downmix-safe — BS.775 fold-down carries
           no LFE term — at the cost of inflating total low-frequency energy.
``split``  The LFE joins the weight set at ``lfe_send`` and the mains share
           what is left. Energy-preserving on a bass-managed system, and
           LF-light on a stereo fold-down.

An LFE share carries :attr:`~upmixer.config.UpmixConfig.lfe_gain` (−10 dB per
BS.775-4 Annex 7) so that playback's +10 dB replay gain restores exactly the
share that was taken from the mains.

Built-in profiles
-----------------
boost      Sub +2 dB, mid +1 dB, LFE +1.5 dB
cut        Sub −2.5 dB, mid −1.5 dB, LFE −1 dB
mono       Unify at 90 Hz into the front pair, no EQ
enhance    Sub +1.5 dB, mid +0.5 dB, unified across the bed, punch, exciter
deep       The native-multichannel low end: unified across the bed with punch,
           exciter, and an LFE send
cinema     Unify at 80 Hz and split the low end into the LFE

All profiles can be overridden by individual config params
(``mastering_bass_sub_gain_db``, etc.); ``None`` = use profile default.
"""
from __future__ import annotations

import logging

import numpy as np
import upmixer_dsp

_log = logging.getLogger("upmixer")

from upmixer.manifest import register_block_keys as _rbk
_rbk("mastering", {
    "bass": {
        "profile":     ("config", "mastering_bass_profile"),
        "sub_gain_db": ("config", "mastering_bass_sub_gain_db"),
        "mid_gain_db": ("config", "mastering_bass_mid_gain_db"),
        "unify_hz":    ("config", "mastering_bass_unify_hz"),
        "spread":      ("config", "mastering_bass_spread"),
        "punch":       ("config", "mastering_bass_punch"),
        "harmonics":   ("config", "mastering_bass_harmonics"),
        "excite":      ("config", "mastering_bass_excite"),
        "lfe_mode":    ("config", "mastering_bass_lfe_mode"),
        "lfe_send":    ("config", "mastering_bass_lfe_send"),
        "lfe_gain_db": ("config", "mastering_bass_lfe_gain_db"),
        "decorrelate": ("config", "mastering_bass_decorrelate"),
    },
})
del _rbk


BASS_PROFILES: dict[str, dict] = {
    "boost": dict(
        sub_gain_db=2.0, mid_gain_db=1.0, unify_hz=None, spread="bed",
        punch=0.0, excite=False, lfe_mode="off", lfe_send=0.0, lfe_gain_db=1.5, decorrelate=0.0,
    ),
    "cut": dict(
        sub_gain_db=-2.5, mid_gain_db=-1.5, unify_hz=None, spread="bed",
        punch=0.0, excite=False, lfe_mode="off", lfe_send=0.0, lfe_gain_db=-1.0, decorrelate=0.0,
    ),
    "mono": dict(
        sub_gain_db=0.0, mid_gain_db=0.0, unify_hz=90.0, spread="front",
        punch=0.0, excite=False, lfe_mode="off", lfe_send=0.0, lfe_gain_db=0.0,
        decorrelate=0.0,
    ),
    "enhance": dict(
        sub_gain_db=1.5, mid_gain_db=0.5, unify_hz=90.0, spread="bed",
        punch=0.2, excite=True, lfe_mode="add", lfe_send=0.25, lfe_gain_db=1.0,
        decorrelate=0.0,
    ),
    "deep": dict(
        sub_gain_db=1.0, mid_gain_db=0.5, unify_hz=90.0, spread="bed",
        punch=0.25, excite=True, lfe_mode="add", lfe_send=0.3, lfe_gain_db=1.0,
        decorrelate=0.0,
    ),
    "cinema": dict(
        sub_gain_db=1.0, mid_gain_db=0.0, unify_hz=80.0, spread="bed",
        punch=0.0, excite=False, lfe_mode="split", lfe_send=0.5, lfe_gain_db=0.0,
        decorrelate=0.0,
    ),
}

BASS_PROFILE_NAMES: tuple[str, ...] = tuple(sorted(BASS_PROFILES.keys()))

# Public (not module-private) so the web engine-constants endpoint (apps/api
# system slice) can serve the exact values — see
# docs/contracts/preview_export_parity.md.
LF_SPREADS: dict[str, tuple[str, ...]] = {
    "front": ("FL", "FR"),
    "bed": ("FL", "FR", "C", "SL", "SR", "BL", "BR"),
    "all": ("FL", "FR", "C", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR"),
}

LF_SPREAD_NAMES: tuple[str, ...] = tuple(LF_SPREADS)
LFE_MODES: tuple[str, ...] = ("off", "add", "split")

UNIFY_MIN_HZ: float = 40.0
UNIFY_MAX_HZ: float = 120.0
DEFAULT_UNIFY_HZ: float = 90.0

SUB_CUTOFF_HZ: float = 80.0
MID_CUTOFF_HZ: float = 200.0

EXCITE_BLEND: float = 0.15
EXCITE_DRIVE: float = 3.0

PUNCH_FAST_MS: float = 10.0
PUNCH_SLOW_MS: float = 120.0
PUNCH_MAX_DB: float = 6.0

DECORR_LOW_HZ: float = 100.0
DECORR_HIGH_HZ: float = 300.0
DECORR_SECTIONS: int = 32
# Kermit-Canfield & Abel put the reverb-vs-width threshold near here; past it
# the cascade reads as a room rather than as spread.
DECORR_MAX_DELAY_MS: float = 30.0
# Slow enough not to track the band's own 100-300 Hz carrier, which would ride
# the gate down on steady tones.
DECORR_FAST_MS: float = 30.0
DECORR_SLOW_MS: float = 300.0


def resolve_lf_targets(
    names: list[str],
    spread: str,
    lfe_mode: str,
    lfe_send: float,
    lfe_authoring_gain: float,
    lfe_key: str = "LFE",
) -> list[tuple[int, float]]:
    """Resolve `(channel index, weight)` pairs for the LF redistribution.

    Args:
        names:              Channel names in bed order.
        spread:             Key into :data:`LF_SPREADS`.
        lfe_mode:           One of :data:`LFE_MODES`.
        lfe_send:           LFE share [0.0-1.0].
        lfe_authoring_gain: BS.775 LFE authoring gain (−10 dB linear).
        lfe_key:            Name of the LFE channel.

    Returns:
        Target list. Non-LFE weights sum to 1.0 in ``off``/``add`` and to
        ``1 - lfe_send`` in ``split``; the LFE entry, when present, carries
        ``lfe_authoring_gain`` folded in.

    Raises:
        KeyError:   if *spread* is not in :data:`LF_SPREADS`.
        ValueError: if *lfe_mode* is not in :data:`LFE_MODES`.
    """
    if spread not in LF_SPREADS:
        raise KeyError(f"Unknown bass spread '{spread}'. Valid choices: {LF_SPREAD_NAMES}")
    if lfe_mode not in LFE_MODES:
        raise ValueError(f"Unknown bass LFE mode '{lfe_mode}'. Valid choices: {LFE_MODES}")

    index = {name: i for i, name in enumerate(names)}
    present = [index[name] for name in LF_SPREADS[spread] if name in index]
    if not present:
        return []

    send = float(np.clip(lfe_send, 0.0, 1.0)) if lfe_key in index else 0.0
    if lfe_mode == "off":
        send = 0.0
    bed_share = 1.0 - send if lfe_mode == "split" else 1.0

    targets = [(i, bed_share / len(present)) for i in present]
    if send > 0.0:
        targets.append((index[lfe_key], send * lfe_authoring_gain))
    return targets


class BassController:
    """Multichannel bass management for the mastering bus.

    Designed for spatial audio — each processing stage is LFE-aware.
    LFE is treated separately from the main bed at all times.

    Args:
        sub_gain_db:        Gain applied to the <80 Hz band of all non-LFE
                            channels (dB).  0.0 = bypass.
        mid_gain_db:        Gain applied to the 80–200 Hz band of all non-LFE
                            channels (dB).  0.0 = bypass.
        unify_hz:           Crossover for LF unification (Hz), clamped to
                            40–120.  ``None`` = unification disabled, which
                            also disables punch, the exciter and the LFE send.
        spread:             Where the unified low end is redistributed; key
                            into :data:`LF_SPREADS`.
        punch:              Transient shaping on the LF bus [−1.0…1.0].
                            Positive favours attacks, negative densifies,
                            0.0 = bypass.
        excite:             Enable the harmonic exciter on the LF bus.
        harmonics:          Exciter amount [0.0-1.0]. ``None`` preserves the
                            legacy on/off value from ``excite``.
        lfe_mode:           One of :data:`LFE_MODES`.
        lfe_send:           LFE share of the LF bus [0.0-1.0].
        lfe_gain_db:        dB gain trim applied to the LFE channel only.
                            0.0 = no change.
        decorrelate:        Mid-bass decorrelation depth [0.0-1.0], applied to
                            the sustained part of the 100-300 Hz band of every
                            non-LFE channel. 0.0 = bypass.
        lfe_authoring_gain: BS.775-4 Annex 7 LFE authoring gain, linear.
        sample_rate:        Audio sample rate in Hz.
    """

    def __init__(
        self,
        sub_gain_db: float,
        mid_gain_db: float,
        unify_hz: float | None,
        spread: str,
        punch: float,
        excite: bool,
        lfe_mode: str,
        lfe_send: float,
        lfe_gain_db: float,
        decorrelate: float,
        lfe_authoring_gain: float,
        sample_rate: int,
        harmonics: float | None = None,
    ) -> None:
        self._sub_db = float(sub_gain_db)
        self._mid_db = float(mid_gain_db)
        self._unify_hz = (
            float(np.clip(unify_hz, UNIFY_MIN_HZ, UNIFY_MAX_HZ))
            if unify_hz is not None
            else None
        )
        self._spread = spread
        self._punch = float(np.clip(punch, -1.0, 1.0))
        self._harmonics = float(np.clip(
            float(excite) if harmonics is None else harmonics, 0.0, 1.0
        ))
        self._lfe_mode = lfe_mode
        self._lfe_send = float(lfe_send)
        self._lfe_db = float(lfe_gain_db)
        self._decorrelate = float(np.clip(decorrelate, 0.0, 1.0))
        self._lfe_authoring_gain = float(lfe_authoring_gain)
        self._sr = sample_rate

    def process(
        self,
        channels: dict[str, np.ndarray],
        lfe_key: str = "LFE",
        spatial_channels: int | None = None,
    ) -> dict[str, np.ndarray]:
        """Apply bass control to the multichannel bed.

        Args:
            channels: Dict channel_name → 1-D float64 array.
            lfe_key:  Name of the LFE channel (default ``"LFE"``).
            spatial_channels: Number of leading bed channels. Source-wide tone
                shaping includes later objects; LF redistribution stays here.

        Returns:
            Modified channel dict (new arrays for processed channels;
            unmodified channels share the original array objects).
        """
        names = list(channels)
        targets = (
            resolve_lf_targets(
                names,
                self._spread,
                self._lfe_mode,
                self._lfe_send,
                self._lfe_authoring_gain,
                lfe_key,
            )
            if self._unify_hz is not None
            else []
        )

        shaped = upmixer_dsp.bass_control(
            [np.ascontiguousarray(channels[name], dtype=np.float64) for name in names],
            names.index(lfe_key) if lfe_key in names else None,
            len(names) if spatial_channels is None else spatial_channels,
            targets,
            self._sr,
            self._sub_db,
            self._mid_db,
            self._unify_hz,
            self._punch,
            self._harmonics > 0.0,
            self._lfe_db,
            SUB_CUTOFF_HZ,
            MID_CUTOFF_HZ,
            EXCITE_BLEND * self._harmonics,
            EXCITE_DRIVE,
            PUNCH_FAST_MS,
            PUNCH_SLOW_MS,
            PUNCH_MAX_DB,
            self._decorrelate,
            DECORR_LOW_HZ,
            DECORR_HIGH_HZ,
            DECORR_SECTIONS,
            DECORR_MAX_DELAY_MS,
            DECORR_FAST_MS,
            DECORR_SLOW_MS,
        )

        if self._sub_db != 0.0 or self._mid_db != 0.0:
            _log.debug(
                "  BassController: sub=%+.1f dB  mid=%+.1f dB",
                self._sub_db, self._mid_db,
            )
        if self._unify_hz is not None:
            _log.debug(
                "  BassController: LF unified at %.0f Hz over %s (%d targets), LFE %s",
                self._unify_hz, self._spread, len(targets), self._lfe_mode,
            )
        if self._punch != 0.0:
            _log.debug("  BassController: punch %+.2f", self._punch)
        if self._harmonics > 0.0:
            _log.debug("  BassController: harmonics %.0f%%", self._harmonics * 100.0)
        if self._decorrelate > 0.0:
            _log.debug(
                "  BassController: %.0f-%.0f Hz decorrelated %.2f over %d sections",
                DECORR_LOW_HZ, DECORR_HIGH_HZ, self._decorrelate, DECORR_SECTIONS,
            )
        if self._lfe_db != 0.0:
            _log.debug("  BassController: LFE %+.1f dB", self._lfe_db)

        return dict(zip(names, shaped))
