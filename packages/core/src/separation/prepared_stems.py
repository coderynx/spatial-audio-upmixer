"""Render a speaker bed from a caller-owned prepared stem store."""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
from scipy.signal import resample_poly

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, detect_input_format
from upmixer.io.adm_writer import render_adm_programme
from upmixer.io.reader import AudioReader
from upmixer.loudness import measure_integrated_loudness
from upmixer.separation.source_anchor import apply_source_anchor
from upmixer.separation.stem_router import StemRouter
from upmixer.separation.stem_store import PlainStemStore
from upmixer.separation.stem_zones import _as_stereo_pair, _extract_zones, _resample_zones
from upmixer.utils import itu_downmix_stereo


def render_prepared_stem_bed(config: UpmixConfig, input_path: str) -> tuple[dict[str, np.ndarray], int]:
    """Route prepared stems into the pre-mastering speaker bed for *input_path*."""
    if not config.stem_input_dir:
        raise ValueError("stem_input_dir is required to render prepared stems")
    loaded = PlainStemStore(config.stem_input_dir).load()
    if loaded is None:
        raise RuntimeError("Prepared stem store is unavailable")
    all_stems, stem_sr = loaded

    reader = AudioReader(input_path)
    source_audio, source_sr = reader.read(dtype="float32")
    input_fmt = detect_input_format(reader.n_channels)
    output_fmt = FORMAT_MAP[config.output_format]
    if output_fmt.n_channels == 2 and input_fmt.n_channels > 2:
        left, right = itu_downmix_stereo(
            {label.value: source_audio[:, i] for i, label in enumerate(input_fmt.channels)},
            surround_coeff=config.surround_downmix_coeff,
            height_coeff=config.height_downmix_coeff,
        )
        source_audio = np.column_stack([left, right]).astype(np.float32, copy=False)
        input_fmt = detect_input_format(2)

    if input_fmt.n_channels <= 2:
        source_zones = {"front": _as_stereo_pair(source_audio)}
        passthrough: dict[str, np.ndarray] = {}
    else:
        source_zones, passthrough = _extract_zones(source_audio, input_fmt)

    all_stems = {
        stem: audio for stem, audio in all_stems.items()
        if stem.split("@", 1)[0] in (config.stems or all_stems)
    }
    if not all_stems:
        raise RuntimeError("Prepared stem store has no requested stems")
    n_samples = max(len(stem) for stem in all_stems.values())
    all_stems = _process_stems(config, all_stems, stem_sr)
    n_samples = max(len(stem) for stem in all_stems.values())

    if source_sr != stem_sr:
        divisor = math.gcd(source_sr, stem_sr)
        source_zones = _resample_zones(source_zones, source_sr, stem_sr)
        passthrough = {
            name: resample_poly(audio, stem_sr // divisor, source_sr // divisor).astype(np.float32, copy=False)
            for name, audio in passthrough.items()
        }
    router = StemRouter(config, output_fmt, stem_sr)
    programme = router.route(all_stems, n_samples, passthrough_channels=set(passthrough))
    channels = programme.bed
    for name, audio in passthrough.items():
        if name in channels:
            channels[name][:min(len(audio), n_samples)] += audio[:n_samples]

    linked = {str(index): obj.audio for index, obj in enumerate(programme.objects)} or None

    def render(bed: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        return render_adm_programme(
            bed,
            output_fmt,
            [replace(obj, audio=linked[str(index)]) for index, obj in enumerate(programme.objects)],
        )

    channels = apply_source_anchor(
        channels, source_zones, output_fmt, 0.0 if linked else config.stem_source_anchor_strength,
    )
    if config.normalize_output:
        channels = _normalize_to_source(
            channels, source_audio, source_sr, stem_sr, output_fmt, linked, render if linked else None,
        )
    return (render(channels) if linked else channels), stem_sr


def _process_stems(config: UpmixConfig, stems: dict[str, np.ndarray], sample_rate: int) -> dict[str, np.ndarray]:
    if config.stem_rebalance:
        from upmixer.separation.stem_rebalance import StemRebalancer
        stems = StemRebalancer(config.stem_rebalance, sample_rate).process(stems)
    if config.stem_eq_profiles:
        from upmixer.separation.stem_eq import StemEQ
        stems = StemEQ(config.stem_eq_profiles, sample_rate).process(stems)
    if config.stem_dynamic_eq:
        from upmixer.separation.stem_dynamic_eq import StemDynamicEq
        stems = StemDynamicEq(config.stem_dynamic_eq, sample_rate).process(stems)
    if config.stem_dynamics:
        from upmixer.separation.stem_dynamics import StemDynamics
        stems = StemDynamics(config.stem_dynamics, sample_rate).process(stems)
    return stems


def _normalize_to_source(
    channels: dict[str, np.ndarray], source: np.ndarray, source_sr: int, stem_sr: int,
    output_fmt, linked: dict[str, np.ndarray] | None,
    render,
) -> dict[str, np.ndarray]:
    if source_sr != stem_sr:
        divisor = math.gcd(source_sr, stem_sr)
        source = resample_poly(source, stem_sr // divisor, source_sr // divisor, axis=0)
    if source.ndim == 1:
        source = source[:, None]
    output = render(channels) if linked else channels

    def scale(value: float) -> dict[str, np.ndarray]:
        if linked:
            linked.update({name: audio * value for name, audio in linked.items()})
        return {name: audio * value for name, audio in channels.items()}

    try:
        source_fmt = detect_input_format(source.shape[1])
    except ValueError:
        source_fmt = None
    if source_fmt is not None:
        source_lkfs = measure_integrated_loudness(
            {label.value: source[:, i] for i, label in enumerate(source_fmt.channels)}, stem_sr, source_fmt,
        )
        output_lkfs = measure_integrated_loudness(output, stem_sr, output_fmt)
        if min(source_lkfs, output_lkfs) > -70.0:
            return scale(10.0 ** ((source_lkfs - output_lkfs) / 20.0))
    source_energy = float(np.vdot(source, source).real)
    output_energy = sum(float(np.vdot(audio, audio).real) for audio in output.values())
    return scale(np.sqrt(source_energy / output_energy)) if source_energy > 1e-20 and output_energy > 1e-20 else channels
