"""Reference corpora for the separation evaluation harness.

A corpus is a set of (mixture, per-stem reference) pairs, each tagged with a
category so results can be grouped and regression-probed per category (see
``docs/evaluation_harness.md``). No copyrighted audio ships with this
package — ``ReferenceCorpus.from_dir`` points at a user-supplied, lawfully
licensed directory (MUSDB18-HQ is research-only and not bundled), and
``synthetic_corpus`` generates a small lawful corpus in-process for
deterministic testing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf


@dataclass
class CorpusItem:
    """One evaluation item: a mixture and its true per-stem references.

    Attributes:
        mixture: Path to the mixture audio file.
        stems:   Canonical stem name -> path to the reference stem audio.
        category: Regression-probe category label (e.g. "dense_synth",
            "choir_cluster"); "default" for ordinary material.
    """

    mixture: str
    stems: dict[str, str]
    category: str = "default"


@dataclass
class ReferenceCorpus:
    """Ordered collection of evaluation items."""

    items: list[CorpusItem]

    @classmethod
    def from_dir(cls, path: str) -> "ReferenceCorpus":
        """Load a corpus from a ``corpus.json`` manifest in ``path``.

        Manifest layout::

            {
              "items": [
                {"mixture": "song1/mix.wav",
                 "stems": {"Vocals": "song1/vocals.wav", "Bass": "song1/bass.wav"},
                 "category": "default"}
              ]
            }

        Relative paths in the manifest are resolved against ``path``.
        """
        base = Path(path)
        manifest = json.loads((base / "corpus.json").read_text(encoding="utf-8"))
        items = []
        for raw in manifest["items"]:
            mixture = str(base / raw["mixture"])
            stems = {name: str(base / rel) for name, rel in raw["stems"].items()}
            items.append(CorpusItem(mixture=mixture, stems=stems, category=raw.get("category", "default")))
        return cls(items=items)


def _write_wav(path: Path, signal: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), signal, sample_rate, subtype="FLOAT")


def synthetic_corpus(sample_rate: int, out_dir: str) -> ReferenceCorpus:
    """Generate a small, lawful, fully-synthetic corpus for the eval harness.

    Every mixture is the exact sum of its known stems, so metrics measure the
    harness itself (or a real separator) against ground truth with no
    licensing ambiguity. Includes two AI-killer-style regression-probe
    categories (dense synth stack, detuned choir cluster) alongside a
    "default" item, per the category-probe requirement in
    ``docs/evaluation_harness.md``.

    Args:
        sample_rate: Sample rate for generated audio.
        out_dir:     Directory to write generated WAV files into.

    Returns:
        A ReferenceCorpus with absolute paths under out_dir.
    """
    base = Path(out_dir)
    rng = np.random.default_rng(20260728)
    # BS-Roformer-SW (and MDXC models generally) silently return zero output
    # stems on very short clips at some segment_size/sample_rate combinations
    # (observed below ~3s); keep items comfortably longer than that floor.
    duration_s = 4.0
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate

    def stereo(mono: np.ndarray) -> np.ndarray:
        return np.stack([mono, mono], axis=1).astype(np.float32)

    items = []

    # "default": a simple vocal + bass + drums + other mix.
    vocals = 0.3 * np.sin(2 * np.pi * 220 * t)
    bass = 0.4 * np.sin(2 * np.pi * 55 * t)
    drums = np.zeros(n, dtype=np.float64)
    for hit in np.arange(0, duration_s, 0.5):
        idx = int(hit * sample_rate)
        width = int(0.02 * sample_rate)
        drums[idx : idx + width] += rng.standard_normal(width) * 0.5
    other = 0.1 * rng.standard_normal(n)
    default_stems = {"Vocals": vocals, "Bass": bass, "Drums": drums, "Other": other}
    default_mix = sum(default_stems.values())
    stem_paths = {}
    for name, sig in default_stems.items():
        p = base / "default" / f"{name.lower()}.wav"
        _write_wav(p, stereo(sig), sample_rate)
        stem_paths[name] = str(p)
    mix_path = base / "default" / "mix.wav"
    _write_wav(mix_path, stereo(default_mix), sample_rate)
    items.append(CorpusItem(mixture=str(mix_path), stems=stem_paths, category="default"))

    # "dense_synth": many overlapping partials — a known model-killer texture.
    synth_partials = sum(
        (0.15 / k) * np.sin(2 * np.pi * (110 * k + rng.uniform(-2, 2)) * t)
        for k in range(1, 13)
    )
    synth_vocals = 0.25 * np.sin(2 * np.pi * 330 * t)
    dense_stems = {"Vocals": synth_vocals, "Other": synth_partials}
    dense_mix = sum(dense_stems.values())
    stem_paths = {}
    for name, sig in dense_stems.items():
        p = base / "dense_synth" / f"{name.lower()}.wav"
        _write_wav(p, stereo(sig), sample_rate)
        stem_paths[name] = str(p)
    mix_path = base / "dense_synth" / "mix.wav"
    _write_wav(mix_path, stereo(dense_mix), sample_rate)
    items.append(CorpusItem(mixture=str(mix_path), stems=stem_paths, category="dense_synth"))

    # "choir_cluster": several detuned unison voices — another known killer.
    detunes = [-6, -2, 0, 3, 7]
    choir = sum(
        0.12 * np.sin(2 * np.pi * 246 * (1 + cents / 1200) * t) for cents in detunes
    )
    choir_other = 0.1 * rng.standard_normal(n)
    choir_stems = {"Vocals": choir, "Other": choir_other}
    choir_mix = sum(choir_stems.values())
    stem_paths = {}
    for name, sig in choir_stems.items():
        p = base / "choir_cluster" / f"{name.lower()}.wav"
        _write_wav(p, stereo(sig), sample_rate)
        stem_paths[name] = str(p)
    mix_path = base / "choir_cluster" / "mix.wav"
    _write_wav(mix_path, stereo(choir_mix), sample_rate)
    items.append(CorpusItem(mixture=str(mix_path), stems=stem_paths, category="choir_cluster"))

    return ReferenceCorpus(items=items)
