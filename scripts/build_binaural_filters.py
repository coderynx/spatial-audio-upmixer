#!/usr/bin/env python3
"""Build measured order-3 ambisonic-to-binaural decode banks.

The source is the SADIE II D1/KU100 48 kHz diffuse-field-compensated SOFA
file.  The SOFA file is a build-time input; only the derived WAV banks are
checked into the repository.  See ``docs/standards/measured_hrir_provenance.md``
for the source, license, and generation record.

Usage:
    uv run --with h5py python scripts/build_binaural_filters.py --sofa PATH
"""
from __future__ import annotations

import argparse
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

from upmixer.binaural.ambisonics import N_ACN_CHANNELS, encoding_matrix
from upmixer.direct_speakers import DIRECT_SPEAKER_LAYOUTS
from upmixer.formats import MEASURED_HRIR_LAYOUTS

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 48_000
DIRECT_TAPS = 256
ROOM_RT60_S = 0.02
ROOM_PRE_DELAY_S = 0.001
ROOM_TAIL_GAIN = 0.01
ROOM_PROFILE_LP_HZ = {"studio": 3500.0, "listening": 2500.0}
LAYOUTS = MEASURED_HRIR_LAYOUTS
LEGACY_LAYOUT = "__legacy_union__"
PROFILES = ("flat", "studio", "listening")

CORE_OUT_DIR = ROOT / "packages" / "core" / "src" / "binaural" / "hrir"
WEB_OUT_DIR = ROOT / "apps" / "web" / "public" / "hrir"

# SADIE's source files are diffuse-field compensated.  Keeping their gain is
# the calibration reference: an extra peak/RMS normalization would break the
# exact left-inverse reconstruction of the measured HRIRs.
NORMALIZATION_POLICY = "preserve SADIE II DFC calibration; no post-gain"


@dataclass(frozen=True)
class SofaHrirDataset:
    """Validated measured HRIR data loaded from one SimpleFreeFieldHRIR SOFA."""

    source_position_deg: np.ndarray
    ir: np.ndarray
    sample_rate: int
    path: Path


def _text(value: object) -> str:
    """Decode scalar HDF5 attributes without requiring a particular NumPy version."""
    if isinstance(value, np.ndarray) and value.ndim == 0:
        value = value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _sofa_attr(sofa: object, name: str) -> str:
    attrs = getattr(sofa, "attrs")
    return _text(attrs.get(name, ""))


def load_sofa_dataset(path: Path) -> SofaHrirDataset:
    """Load and validate the measured SADIE II D1/KU100 48 kHz SOFA input."""
    try:
        import h5py
    except ImportError as exc:  # build-time dependency; never a runtime dependency
        raise RuntimeError(
            "Reading SOFA requires build-time h5py; run with "
            "'uv run --with h5py python scripts/build_binaural_filters.py --sofa PATH'"
        ) from exc

    path = Path(path)
    with h5py.File(path, "r") as sofa:
        conventions = _sofa_attr(sofa, "SOFAConventions")
        room_type = _sofa_attr(sofa, "RoomType").lower()
        database = _sofa_attr(sofa, "DatabaseName")
        listener = _sofa_attr(sofa, "ListenerShortName")
        comment = _sofa_attr(sofa, "Comment").lower()
        history = _sofa_attr(sofa, "History").lower()
        license_text = _sofa_attr(sofa, "License").lower()
        data_type = _sofa_attr(sofa, "DataType")
        source = sofa["SourcePosition"]
        source_type = _text(source.attrs.get("Type", "")).lower()
        source_units = _text(source.attrs.get("Units", "")).lower()
        receiver = sofa["ReceiverPosition"]
        receiver_type = _text(receiver.attrs.get("Type", "")).lower()
        receiver_units = _text(receiver.attrs.get("Units", "")).lower()
        sampling_units = _text(sofa["Data.SamplingRate"].attrs.get("Units", "")).lower()
        if conventions != "SimpleFreeFieldHRIR":
            raise ValueError(f"Expected SimpleFreeFieldHRIR SOFA, got {conventions!r}")
        if "free field" not in room_type:
            raise ValueError(f"Expected free-field HRIRs, got RoomType={room_type!r}")
        if database != "SADIE II" or listener != "D1" or "ku100" not in comment:
            raise ValueError("SOFA is not SADIE II D1/KU100")
        if "apache license, version 2.0" not in license_text:
            raise ValueError("SADIE source does not declare Apache-2.0 redistribution rights")
        if "diffuse field" not in f"{comment} {history}":
            raise ValueError("Expected SADIE diffuse-field-compensated HRIRs")
        if data_type != "FIR":
            raise ValueError(f"Expected FIR SOFA data, got DataType={data_type!r}")
        if source_type != "spherical" or source_units != "degree, degree, metre":
            raise ValueError(
                "Expected spherical source positions in degree, degree, metre units"
            )
        if receiver_type != "cartesian" or receiver_units != "metre":
            raise ValueError("Expected cartesian receiver positions in metre units")
        if sampling_units != "hertz":
            raise ValueError("Expected Data.SamplingRate in hertz")

        sample_rate = int(round(float(np.asarray(sofa["Data.SamplingRate"])[0])))
        source_position = np.asarray(source[:], dtype=np.float64)
        receiver_position = np.asarray(receiver[:], dtype=np.float64)
        ir = np.asarray(sofa["Data.IR"][:], dtype=np.float64)

    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"Expected {SAMPLE_RATE} Hz SOFA, got {sample_rate} Hz")
    if source_position.ndim != 2 or source_position.shape[1] != 3 or not source_position.shape[0]:
        raise ValueError(f"Expected spherical source positions, got {source_position.shape}")
    if source_position[0, 2] <= 0.0 or not np.allclose(
        source_position[:, 2], source_position[0, 2], rtol=0.0, atol=1e-7
    ):
        raise ValueError("Expected all SADIE source positions at one measurement distance")
    if receiver_position.shape != (2, 3, 1):
        raise ValueError(f"Expected two cartesian ear receivers, got {receiver_position.shape}")
    if not (
        np.allclose(receiver_position[:, 0, :], 0.0, atol=1e-7)
        and np.allclose(receiver_position[:, 2, :], 0.0, atol=1e-7)
        and receiver_position[0, 1, 0] > 0.0
        and receiver_position[1, 1, 0] < 0.0
        and np.isclose(abs(receiver_position[0, 1, 0]), abs(receiver_position[1, 1, 0]), atol=1e-7)
    ):
        raise ValueError("Expected SOFA receiver 0=left (+Y), receiver 1=right (-Y)")
    if ir.ndim != 3 or ir.shape[0] != source_position.shape[0] or ir.shape[1] != 2:
        raise ValueError(f"Expected Data.IR shape (M, 2, N), got {ir.shape}")
    if ir.shape[2] != DIRECT_TAPS:
        raise ValueError(f"Expected {DIRECT_TAPS}-tap HRIRs, got {ir.shape[2]} taps")
    if not np.all(np.isfinite(source_position)) or not np.all(np.isfinite(ir)):
        raise ValueError("SOFA contains non-finite positions or HRIR samples")

    return SofaHrirDataset(source_position[:, :2], ir, sample_rate, path)


def _azimuth_distance_deg(actual: np.ndarray, target: float) -> np.ndarray:
    """Return shortest circular azimuth distance in degrees."""
    return np.abs((actual - target + 180.0) % 360.0 - 180.0)


def _unit_vectors(position_deg: np.ndarray) -> np.ndarray:
    az = np.deg2rad(position_deg[:, 0])
    el = np.deg2rad(position_deg[:, 1])
    return np.column_stack((np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)))


def select_hrir(
    dataset: SofaHrirDataset,
    azimuth_deg: float,
    elevation_deg: float,
    *,
    interpolate: bool = True,
) -> np.ndarray:
    """Select an exact measured direction, or interpolate nearby measurements."""
    positions = dataset.source_position_deg
    target_azimuth = azimuth_deg % 360.0
    exact = np.flatnonzero(
        (_azimuth_distance_deg(positions[:, 0], target_azimuth) <= 1e-7)
        & (np.abs(positions[:, 1] - elevation_deg) <= 1e-7)
    )
    if exact.size:
        return dataset.ir[int(exact[0])].copy()
    if not interpolate:
        raise KeyError(f"No measured HRIR at azimuth={azimuth_deg}, elevation={elevation_deg}")

    target = _unit_vectors(np.asarray([[target_azimuth, elevation_deg]], dtype=np.float64))[0]
    distances = np.arccos(np.clip(_unit_vectors(positions) @ target, -1.0, 1.0))
    nearest = np.argsort(distances)[: min(4, len(distances))]
    if distances[nearest[0]] <= 1e-12:
        return dataset.ir[int(nearest[0])].copy()
    weights = 1.0 / np.maximum(distances[nearest], 1e-6) ** 2
    return np.average(dataset.ir[nearest], axis=0, weights=weights)


def layout_directions(layout: str) -> list[tuple[float, float]]:
    """Return non-LFE nominal directions for a bed or legacy union bank."""
    if layout == LEGACY_LAYOUT:
        pairs = sorted({
            (abs(float(speaker.azimuth_deg)), float(speaker.elevation_deg))
            for speakers in DIRECT_SPEAKER_LAYOUTS.values()
            for speaker in speakers
            if speaker.azimuth_deg not in (None, 0.0)
            and speaker.elevation_deg is not None
        })
        return [
            (azimuth, elevation)
            for abs_azimuth, elevation in pairs
            for azimuth in (abs_azimuth, -abs_azimuth)
        ] + [(0.0, 0.0)]
    if layout not in LAYOUTS:
        raise ValueError(f"Unsupported measured-HRIR layout: {layout}")
    return [
        (speaker.azimuth_deg, speaker.elevation_deg)
        for speaker in DIRECT_SPEAKER_LAYOUTS[layout]
        if speaker.azimuth_deg is not None and speaker.elevation_deg is not None
    ]


def layout_hrirs(dataset: SofaHrirDataset, layout: str) -> tuple[list[tuple[float, float]], np.ndarray]:
    """Return nominal directions and measured HRIRs in bed channel order."""
    directions = layout_directions(layout)
    hrirs = np.stack(
        [select_hrir(dataset, azimuth, elevation) for azimuth, elevation in directions],
        axis=0,
    )
    return directions, hrirs


def exact_left_inverse(encode: np.ndarray) -> np.ndarray:
    """Return ``D`` with ``D @ encode == I`` for a full-column-rank encoder."""
    if encode.ndim != 2 or encode.shape[1] > encode.shape[0]:
        raise ValueError(f"Left inverse needs a tall encoder, got {encode.shape}")
    rank = np.linalg.matrix_rank(encode)
    if rank != encode.shape[1]:
        raise ValueError(f"Encoder is rank deficient ({rank} of {encode.shape[1]} columns)")
    inverse = np.linalg.solve(encode.T @ encode, encode.T)
    if not np.allclose(inverse @ encode, np.eye(encode.shape[1]), rtol=1e-10, atol=1e-10):
        raise RuntimeError("Speaker encoder left-inverse assertion failed")
    return inverse


# Bright-tail reference lowpass.  The darker profile is energy-matched to this
# same realization so its room amount changes only by timbre.
REF_TAIL_LP_HZ = 3500.0


def synth_room_tail(
    sr: int,
    rt60_s: float,
    pre_delay_s: float,
    seed: int,
    lp_hz: float = REF_TAIL_LP_HZ,
) -> np.ndarray:
    """Return a deterministic, high-passed, decaying early-ambience tail."""
    n = int(round(rt60_s * sr))
    rng = np.random.default_rng(seed)
    raw = rng.standard_normal(n)
    sos_hp = butter(2, 200.0 / (sr / 2.0), btype="high", output="sos")
    sos_lp = butter(2, lp_hz / (sr / 2.0), btype="low", output="sos")
    noise = sosfilt(sos_hp, sosfilt(sos_lp, raw))
    if lp_hz != REF_TAIL_LP_HZ:
        sos_ref = butter(2, REF_TAIL_LP_HZ / (sr / 2.0), btype="low", output="sos")
        reference = sosfilt(sos_hp, sosfilt(sos_ref, raw))
        rms = float(np.sqrt(np.mean(noise**2)))
        if rms > 0:
            noise *= float(np.sqrt(np.mean(reference**2))) / rms
    decay = np.exp(-6.91 * np.arange(n) / (rt60_s * sr))
    return np.concatenate((np.zeros(int(round(pre_delay_s * sr))), noise * decay))


def _pair_ids(directions: list[tuple[float, float]]) -> list[int]:
    keys = sorted({(round(abs(azimuth), 7), round(elevation, 7)) for azimuth, elevation in directions})
    lookup = {key: index for index, key in enumerate(keys)}
    return [lookup[(round(abs(azimuth), 7), round(elevation, 7))] for azimuth, elevation in directions]


def _add_room_tails(
    hrirs: np.ndarray,
    directions: list[tuple[float, float]],
    *,
    rt60_s: float,
    pre_delay_s: float,
    lp_hz: float,
) -> np.ndarray:
    """Append subtle mirrored early ambience without changing direct HRIR taps."""
    tail_length = int(round(pre_delay_s * SAMPLE_RATE)) + int(round(rt60_s * SAMPLE_RATE))
    brirs = np.zeros((hrirs.shape[0], 2, DIRECT_TAPS + tail_length), dtype=np.float64)
    brirs[:, :, :DIRECT_TAPS] = hrirs
    pair_ids = _pair_ids(directions)
    n_pairs = max(pair_ids) + 1
    near_tails = [synth_room_tail(SAMPLE_RATE, rt60_s, pre_delay_s, 2 * i + 1, lp_hz) for i in range(n_pairs)]
    far_tails = [synth_room_tail(SAMPLE_RATE, rt60_s, pre_delay_s, 2 * i + 2, lp_hz) for i in range(n_pairs)]

    for index, (azimuth, _elevation) in enumerate(directions):
        if azimuth == 0.0:
            left_tail = right_tail = near_tails[pair_ids[index]]
        elif azimuth > 0.0:
            left_tail, right_tail = near_tails[pair_ids[index]], far_tails[pair_ids[index]]
        else:
            left_tail, right_tail = far_tails[pair_ids[index]], near_tails[pair_ids[index]]
        gain_l = max(float(np.sum(np.abs(hrirs[index, 0]))), 0.05) * ROOM_TAIL_GAIN
        gain_r = max(float(np.sum(np.abs(hrirs[index, 1]))), 0.05) * ROOM_TAIL_GAIN
        brirs[index, 0, DIRECT_TAPS:] += gain_l * left_tail
        brirs[index, 1, DIRECT_TAPS:] += gain_r * right_tail
    return brirs


def normalize_filter_bank(matrix: np.ndarray) -> np.ndarray:
    """Apply the documented DFC policy: validate and preserve source gain."""
    if not np.all(np.isfinite(matrix)):
        raise ValueError("Generated decode bank contains non-finite values")
    return matrix


def build_filter_set(
    dataset: SofaHrirDataset,
    layout: str,
    *,
    room_rt60_s: float | None = None,
    room_pre_delay_s: float = ROOM_PRE_DELAY_S,
    room_tail_lp_hz: float = REF_TAIL_LP_HZ,
) -> np.ndarray:
    """Return one measured ``(taps, 32)`` decode bank."""
    directions, hrirs = layout_hrirs(dataset, layout)
    if room_rt60_s is not None:
        hrirs = _add_room_tails(
            hrirs,
            directions,
            rt60_s=room_rt60_s,
            pre_delay_s=room_pre_delay_s,
            lp_hz=room_tail_lp_hz,
        )

    encode = encoding_matrix([(math.radians(az), math.radians(el)) for az, el in directions])
    decode = np.linalg.pinv(encode) if layout == LEGACY_LAYOUT else exact_left_inverse(encode)
    filters = np.einsum("ma,met->aet", decode, hrirs)
    if layout != LEGACY_LAYOUT:
        reconstructed = np.einsum("am,aet->met", encode, filters)
        if not np.allclose(reconstructed, hrirs, rtol=1e-9, atol=1e-12):
            raise RuntimeError(f"Measured HRIR reconstruction failed for layout {layout}")

    matrix = filters.transpose(2, 0, 1).reshape(filters.shape[-1], 2 * N_ACN_CHANNELS)
    return normalize_filter_bank(matrix)


def asset_name(profile: str, layout: str) -> str:
    """Return the contract name for one profile/layout bank."""
    if profile not in PROFILES or layout not in LAYOUTS:
        raise ValueError(f"Unsupported profile/layout: {profile}/{layout}")
    return f"{profile}_o3_decode_{layout.replace('.', '_')}"


def legacy_asset_name(profile: str) -> str:
    """Return the original profile-only name for layout-less callers."""
    if profile not in PROFILES:
        raise ValueError(f"Unsupported profile: {profile}")
    return f"{profile}_o3_decode"


def write_filter_set(name: str, matrix: np.ndarray, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    splits = ((0, 8, "01-08ch"), (8, 16, "09-16ch"), (16, 24, "17-24ch"), (24, 32, "25-32ch"))
    for start, end, suffix in splits:
        path = out_dir / f"{name}_{suffix}.wav"
        sf.write(str(path), matrix[:, start:end], SAMPLE_RATE, subtype="FLOAT")
        shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        print(f"  wrote {shown} ({matrix.shape[0]} taps)")


def _remove_stale_assets(out_dir: Path) -> None:
    expected = {
        f"{asset_name(profile, layout)}_{suffix}.wav"
        for profile in PROFILES
        for layout in LAYOUTS
        for suffix in ("01-08ch", "09-16ch", "17-24ch", "25-32ch")
    }
    expected.update(
        f"{legacy_asset_name(profile)}_{suffix}.wav"
        for profile in PROFILES
        for suffix in ("01-08ch", "09-16ch", "17-24ch", "25-32ch")
    )
    for path in out_dir.glob("*_o3_decode_*.wav"):
        if path.name not in expected:
            path.unlink()


def build_assets(
    sofa_path: Path,
    core_out_dir: Path = CORE_OUT_DIR,
    web_out_dir: Path = WEB_OUT_DIR,
) -> None:
    """Build all layout and legacy banks, then copy them to the web."""
    dataset = load_sofa_dataset(sofa_path)
    _remove_stale_assets(core_out_dir)
    _remove_stale_assets(web_out_dir)
    for profile in PROFILES:
        for layout in LAYOUTS:
            room_rt60 = None if profile == "flat" else ROOM_RT60_S
            matrix = build_filter_set(
                dataset,
                layout,
                room_rt60_s=room_rt60,
                room_tail_lp_hz=ROOM_PROFILE_LP_HZ.get(profile, REF_TAIL_LP_HZ),
            )
            write_filter_set(asset_name(profile, layout), matrix, core_out_dir)
        legacy_matrix = build_filter_set(
            dataset,
            LEGACY_LAYOUT,
            room_rt60_s=room_rt60,
            room_tail_lp_hz=ROOM_PROFILE_LP_HZ.get(profile, REF_TAIL_LP_HZ),
        )
        write_filter_set(legacy_asset_name(profile), legacy_matrix, core_out_dir)

    web_out_dir.mkdir(parents=True, exist_ok=True)
    for wav in core_out_dir.glob("*_o3_decode_*.wav"):
        destination = web_out_dir / wav.name
        shutil.copyfile(wav, destination)
        shown = destination.relative_to(ROOT) if destination.is_relative_to(ROOT) else destination
        print(f"  copied -> {shown}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sofa", type=Path, required=True, help="SADIE II D1/KU100 48 kHz SOFA input")
    parser.add_argument("--core-out", type=Path, default=CORE_OUT_DIR)
    parser.add_argument("--web-out", type=Path, default=WEB_OUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    build_assets(args.sofa, args.core_out, args.web_out)


if __name__ == "__main__":
    main()
