#!/usr/bin/env python3
"""Generates the order-3 ambisonic-to-binaural decode filter sets.

Dev-only tool — not imported by production code. Synthesizes three decode
filter sets (flat / studio / listening) from a parametric spherical-head HRTF
model plus a synthesized room tail for the two room-emulation profiles, and
writes them as the 4x8-channel WAV file layout ``docs/standards/
spatial_audio_engine.md`` §4 documents. Also copies the results into
``apps/web/public/hrir/`` so the browser preview uses byte-identical filters.

Usage:
    uv run python scripts/build_binaural_filters.py
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

from upmixer.binaural.ambisonics import N_ACN_CHANNELS, encoding_matrix
from upmixer.binaural.geometry import SPEAKER_AZIMUTH_ELEVATION
from upmixer.binaural.head_model import synth_hrir
from upmixer.formats import ChannelLabel

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 48_000

CORE_OUT_DIR = ROOT / "packages" / "core" / "src" / "binaural" / "hrir"
WEB_OUT_DIR = ROOT / "apps" / "web" / "public" / "hrir"

DIRECT_TAPS = 128

# Bright-tail reference lowpass. A profile's room tail is energy-normalized to
# this cutoff (see `synth_room_tail`) so a darker per-profile `lp_hz` changes
# only the tail's timbre, not its reverberant amount. `studio` uses this cutoff
# directly, so its tail is unaffected by the normalization.
REF_TAIL_LP_HZ = 3500.0

# The pinv decode matrix (`build_filter_set`) and per-direction BRIRs are fit
# to exactly this direction set — not to arbitrary points on the sphere. That
# set should therefore be exactly the directions the renderer ever actually
# encodes from: the 11 fixed virtual-loudspeaker positions (even the
# nearest-3-speaker routing fallback only ever weights these same 11 fixed
# encoder directions, never a novel one). An earlier revision instead fit a
# generic 32-point Fibonacci-sphere lattice, which has no sample point at
# azimuth 0 (closest points sit at +-5 degrees) — reconstructing a direction
# the fit set doesn't cover leaves residual error, and that error happened to
# fall on dead center, i.e. exactly where `C` (and therefore vocals, which
# are almost always routed there) sits. Fitting the real 11 directions
# instead makes the decode an exact left-inverse for all of them
# (`decode @ encode == I_11` to double-precision) — zero reconstruction
# residual, so no direction is disproportionately "roomier" than another.
#
# Grouped in mirror pairs (mirror symmetry is required for L/R-equal decode
# of a centered signal — see `synth_hrir`/`build_filter_set`'s near/far
# comments) with `C` last, since it has no mirror partner.
REAL_SPEAKER_ORDER: tuple[ChannelLabel, ...] = (
    ChannelLabel.FL, ChannelLabel.FR,
    ChannelLabel.SL, ChannelLabel.SR,
    ChannelLabel.BL, ChannelLabel.BR,
    ChannelLabel.TFL, ChannelLabel.TFR,
    ChannelLabel.TBL, ChannelLabel.TBR,
    ChannelLabel.C,
)
N_VIRTUAL_SPEAKERS = len(REAL_SPEAKER_ORDER)
# Room-tail pair id per speaker index: mirror pairs share id `i // 2`; `C`
# (last, unpaired) gets its own id — see `build_filter_set`'s room-tail loop.
PAIR_ID: tuple[int, ...] = tuple(i // 2 for i in range(N_VIRTUAL_SPEAKERS - 1)) + (
    (N_VIRTUAL_SPEAKERS - 1) // 2,
)


def real_speaker_directions() -> list[tuple[float, float]]:
    """Return the 11 fixed virtual-loudspeaker (azimuth, elevation) directions, in radians."""
    return [
        (SPEAKER_AZIMUTH_ELEVATION[label].azimuth_rad, SPEAKER_AZIMUTH_ELEVATION[label].elevation_rad)
        for label in REAL_SPEAKER_ORDER
    ]


def synth_room_tail(sr: int, rt60_s: float, pre_delay_s: float, seed: int, lp_hz: float = 3500.0) -> np.ndarray:
    """Exponentially-decaying band-passed noise burst approximating a room tail.

    Highpassed at 200 Hz on top of a per-profile lowpass (`lp_hz`): with no low
    end removed, bass content got a sustained, smeared low-frequency tail
    riding under the direct hit — reverberant *decay* on the bass, not extra
    level, which is what read as "boomy" (a bass note ringing via the tail
    instead of stopping cleanly), even though the tail's overall energy was
    already low. Real reverbs are almost always highpassed for exactly this
    reason. `flat`'s direct sound (and every profile's direct sound, being
    identical — see `build_filter_set`'s stage-1 normalization) is untouched
    by this: only the tail's ambience loses its low end, not the bass hit
    itself.

    `lp_hz` sets the tail's high-frequency rolloff, the main lever that gives
    two profiles with the *same* decay time (RT60/pre-delay/level) a different
    room *character*: a treated near-field monitor room keeps a bright tail
    (`REF_TAIL_LP_HZ` = 3500 Hz), while a larger cinema space loses more highs
    over its longer air paths and plush absorption, giving a warmer, darker
    tail (2500 Hz). To keep this a purely tonal change — same reverberant
    *amount*, not less room — the darkened tail is renormalized to the energy
    the bright reference tail would have had for the same noise realization, so
    `lp_hz` shifts the tail's spectral tilt without lowering its total energy
    (and leaves the reference-bright `studio` tail bit-identical: its scale is
    exactly 1).
    """
    n = int(rt60_s * sr)
    rng = np.random.default_rng(seed)
    raw = rng.standard_normal(n)
    sos_hp = butter(2, 200.0 / (sr / 2.0), btype="high", output="sos")
    sos_lp = butter(2, lp_hz / (sr / 2.0), btype="low", output="sos")
    noise = sosfilt(sos_hp, sosfilt(sos_lp, raw))
    if lp_hz != REF_TAIL_LP_HZ:
        sos_ref = butter(2, REF_TAIL_LP_HZ / (sr / 2.0), btype="low", output="sos")
        ref = sosfilt(sos_hp, sosfilt(sos_ref, raw))
        noise_rms = float(np.sqrt(np.mean(noise ** 2)))
        if noise_rms > 0:
            noise *= float(np.sqrt(np.mean(ref ** 2))) / noise_rms
    decay = np.exp(-6.91 * np.arange(n) / (rt60_s * sr))  # -60 dB at rt60
    tail = noise * decay
    pre_delay_n = int(pre_delay_s * sr)
    return np.concatenate([np.zeros(pre_delay_n), tail])


def build_filter_set(
    room_rt60_s: float | None,
    room_pre_delay_s: float = 0.005,
    target_energy: float | None = None,
    room_tail_lp_hz: float = 3500.0,
) -> np.ndarray:
    """Return the (n_taps, 32) decode filter matrix for one profile.

    Two-stage normalization:

    1. Scale so the *direct* portion's peak (first ``DIRECT_TAPS`` samples,
       before the room tail's pre-delay even starts) hits 0.9 — the same
       criterion ``flat`` uses on its only content. Earlier revisions
       normalized by the *whole* matrix's peak or total energy instead:
       whole-matrix peak normalization let a room-tail profile's soft,
       spread-out tail sit near the same peak as the direct impulse, driving
       far more total energy/RMS through real (non-impulse) program
       material than `flat` got for the same input (heavy soft-limiter
       saturation). Whole-matrix *energy* normalization fixed that blowup
       but overcorrected: at that point the tail (spread over thousands of
       samples) held 60-70% of total energy even at a modest per-sample
       level, so matching *total* energy to `flat` crushed the direct/HRIR
       component — the transient, high-frequency part — to a fraction of
       `flat`'s loudness, which read as "muffled". Direct-only peak
       normalization fixes both: the transient always matches `flat`.
    2. When `target_energy` is given (the room-tail profiles, matched to
       `flat`'s own total energy), apply one small additional uniform
       scale-down so the *tail*'s now-modest energy contribution (with the
       tail level kept low enough that this is only ~1-2 dB, unlike the
       8 dB step (1) alone would have needed) doesn't leave the room-tail
       profiles measurably louder overall than `flat` for the same input.
    """
    directions = real_speaker_directions()
    encode = encoding_matrix(directions)  # (16, M)
    decode = np.linalg.pinv(encode)  # (M, 16): decode @ encode ~= I_16

    n_taps = DIRECT_TAPS
    if room_rt60_s is not None:
        n_taps = DIRECT_TAPS + int(room_rt60_s * SAMPLE_RATE) + int(room_pre_delay_s * SAMPLE_RATE)

    # Keyed to near/far-of-source (ipsilateral/contralateral), not to a fixed
    # physical L/R channel: with a mirror-symmetric direction set (above), a
    # source and its mirror swap which physical ear is "near" — using a
    # fixed room_l/room_r pair regardless of direction would apply a
    # different random tail to that swapped ear than its mirror counterpart
    # used, breaking the L/R symmetry the direction-set fix establishes.
    # Keying to near/far instead guarantees the mirror pair's BRIRs are
    # themselves exact mirrors, so a centered signal decodes to equal L/R.
    #
    # One tail *per mirror pair* (not one global pair reused by every
    # direction): `decode` (the pinv of the encode matrix) is a fixed set of
    # per-direction weights, and summing the *same* two noise realizations
    # across every direction's row makes that sum constructive (the decode
    # weights just add arithmetically) instead of the sqrt(N)-decorrelated
    # growth independent per-direction noise would give. That inflated the
    # room tail's contribution to the decode filters by roughly an order of
    # magnitude relative to the direct sound, which is what drove the
    # binaural render into heavy soft-limiter saturation for the room-tail
    # profiles (studio/listening) — flat has no room tail and was unaffected.
    # Seeding per pair keeps every pair's BRIRs exact mirrors (L/R symmetry
    # intact) while decorrelating the tail across directions. `C` has no
    # mirror partner (see `PAIR_ID`) and sits at azimuth 0, so its near/far
    # ears are physically identical — its pair gets one tail shared by both
    # ears (`room_far_by_pair[center_pair] = room_near_by_pair[...]` below)
    # rather than two independent draws, which would otherwise put an
    # arbitrary L/R difference on a source that should render perfectly
    # centered.
    num_pairs = max(PAIR_ID) + 1
    room_near_by_pair = (
        [synth_room_tail(SAMPLE_RATE, room_rt60_s, room_pre_delay_s, seed=2 * i + 1, lp_hz=room_tail_lp_hz) for i in range(num_pairs)]
        if room_rt60_s else None
    )
    room_far_by_pair = (
        [synth_room_tail(SAMPLE_RATE, room_rt60_s, room_pre_delay_s, seed=2 * i + 2, lp_hz=room_tail_lp_hz) for i in range(num_pairs)]
        if room_rt60_s else None
    )
    if room_rt60_s is not None:
        center_pair = PAIR_ID[REAL_SPEAKER_ORDER.index(ChannelLabel.C)]
        room_far_by_pair[center_pair] = room_near_by_pair[center_pair]

    out = np.zeros((n_taps, 2 * N_ACN_CHANNELS), dtype=np.float64)
    for speaker_idx, (az, el) in enumerate(directions):
        hrir_l, hrir_r = synth_hrir(az, el, SAMPLE_RATE, DIRECT_TAPS)
        if room_rt60_s is not None:
            brir_l = np.zeros(n_taps)
            brir_r = np.zeros(n_taps)
            brir_l[:DIRECT_TAPS] = hrir_l
            brir_r[:DIRECT_TAPS] = hrir_r
            direct_gain_l = float(np.sum(np.abs(hrir_l)))
            direct_gain_r = float(np.sum(np.abs(hrir_r)))
            room_near = room_near_by_pair[PAIR_ID[speaker_idx]]
            room_far = room_far_by_pair[PAIR_ID[speaker_idx]]
            # az >= 0 (source left): left ear is near, right is far. az < 0: swapped.
            left_tail, right_tail = (room_near, room_far) if az >= 0 else (room_far, room_near)
            room_scaled_l = left_tail * max(direct_gain_l, 0.05) * 0.05
            room_scaled_r = right_tail * max(direct_gain_r, 0.05) * 0.05
            brir_l[: len(room_scaled_l)] += room_scaled_l
            brir_r[: len(room_scaled_r)] += room_scaled_r
        else:
            brir_l = hrir_l
            brir_r = hrir_r

        for acn in range(N_ACN_CHANNELS):
            out[:, 2 * acn] += decode[speaker_idx, acn] * brir_l
            out[:, 2 * acn + 1] += decode[speaker_idx, acn] * brir_r

    peak = float(np.max(np.abs(out[:DIRECT_TAPS]))) or 1.0
    out *= 0.9 / peak
    if target_energy is not None:
        energy = float(np.sum(out ** 2)) or 1.0
        out *= math.sqrt(target_energy / energy)
    return out


def write_filter_set(name: str, matrix: np.ndarray, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    splits = [(0, 8, "01-08ch"), (8, 16, "09-16ch"), (16, 24, "17-24ch"), (24, 32, "25-32ch")]
    for start, end, suffix in splits:
        path = out_dir / f"{name}_{suffix}.wav"
        sf.write(str(path), matrix[:, start:end], SAMPLE_RATE, subtype="FLOAT")
        print(f"  wrote {path.relative_to(ROOT)}  ({matrix.shape[0]} taps)")


def main() -> None:
    # (rt60_s, pre_delay_s, tail_lp_hz) per room-tail profile. `studio` and
    # `listening` share the *same* room amount (identical RT60, pre-delay, and
    # tail level) — the difference is timbre: `studio` keeps a bright, neutral
    # near-field monitor tail (3500 Hz), while `listening` darkens the tail
    # (2500 Hz) to read as a larger, warmer reference cinema room without
    # adding any extra reverberation. The hi-fi polish voicing (VOICING_PARAMS
    # in upmixer/binaural/profiles.py) is layered on top of `listening` only.
    room_profiles = {
        "studio_o3_decode": (0.12, 0.005, 3500.0),
        "listening_o3_decode": (0.12, 0.005, 2500.0),
    }
    print("Building flat_o3_decode (rt60=None)...")
    flat_matrix = build_filter_set(None)
    write_filter_set("flat_o3_decode", flat_matrix, CORE_OUT_DIR)
    # Overall-loudness reference every room-tail profile is matched to —
    # see `build_filter_set`'s docstring, stage 2.
    target_energy = float(np.sum(flat_matrix ** 2))
    for name, (rt60, pre_delay, tail_lp) in room_profiles.items():
        print(f"Building {name} (rt60={rt60}, pre_delay={pre_delay}, tail_lp={tail_lp})...")
        matrix = build_filter_set(
            rt60, room_pre_delay_s=pre_delay, target_energy=target_energy, room_tail_lp_hz=tail_lp
        )
        write_filter_set(name, matrix, CORE_OUT_DIR)

    WEB_OUT_DIR.mkdir(parents=True, exist_ok=True)
    for wav in CORE_OUT_DIR.glob("*.wav"):
        shutil.copyfile(wav, WEB_OUT_DIR / wav.name)
        print(f"  copied -> {(WEB_OUT_DIR / wav.name).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
