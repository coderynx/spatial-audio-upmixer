"""Model configuration loading for the in-core separation inference engine.

Each production model ships an MSST-style YAML config (bundled under
``inference/configs/``) with four top-level sections: ``audio`` (STFT and
sample-rate parameters), ``model`` (architecture hyperparameters — passed
directly as constructor kwargs to the Roformer archs), ``training``
(instrument list and ``target_instrument``, which determine stem naming and
single- vs multi-stem demix), and ``inference`` (the model's default segment
size). This module parses that YAML into a :class:`ModelConfig` value object
used throughout the engine, plus a namespace view for the vendored
``TFC_TDF_net`` architecture, which expects dotted attribute access
(``config.model.norm``, ``config.audio.dim_f``, ...).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

_CONFIGS_DIR = Path(__file__).parent / "configs"


class _TupleSafeLoader(yaml.SafeLoader):
    """SafeLoader that also understands the ``!!python/tuple`` YAML tag.

    The bundled MSST training configs use this tag for a handful of plain
    integer-tuple values (e.g. ``freqs_per_bands``). Registering only this
    one constructor keeps loading safe — no arbitrary Python object
    construction — while accepting the YAML files exactly as shipped
    upstream, unmodified.
    """


def _construct_python_tuple(loader: yaml.SafeLoader, node: yaml.Node) -> tuple:
    return tuple(loader.construct_sequence(node))


_TupleSafeLoader.add_constructor(
    "tag:yaml.org,2002:python/tuple", _construct_python_tuple
)


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=_TupleSafeLoader)


def _to_namespace(value: Any) -> Any:
    """Recursively convert nested dicts to SimpleNamespace for dot access."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in value.items()})
    return value


@dataclass
class ModelConfig:
    """Parsed model configuration for one separation checkpoint."""

    audio: dict[str, Any]
    model: dict[str, Any]
    training: dict[str, Any]
    inference: dict[str, Any]

    @property
    def sample_rate(self) -> int:
        return int(self.audio["sample_rate"])

    @property
    def instruments(self) -> list[str]:
        return list(self.training["instruments"])

    @property
    def target_instrument(self) -> str | None:
        return self.training.get("target_instrument") or None

    @property
    def num_stems(self) -> int:
        """Number of distinct output stems (1 for single-target models)."""
        return 1 if self.target_instrument else len(self.instruments)

    @property
    def default_segment_size(self) -> int:
        return int(self.inference["dim_t"])

    @property
    def stft_hop_length(self) -> int:
        """Roformer chunking hop length; falls back to ``audio.hop_length``."""
        return int(self.model.get("stft_hop_length", self.audio["hop_length"]))

    @property
    def hop_length(self) -> int:
        """TFC-TDF chunking hop length, from the ``audio`` section."""
        return int(self.audio["hop_length"])

    def as_namespace(self) -> SimpleNamespace:
        """Full nested namespace view, for the vendored ``TFC_TDF_net``."""
        return SimpleNamespace(
            audio=_to_namespace(self.audio),
            model=_to_namespace(self.model),
            training=_to_namespace(self.training),
            inference=_to_namespace(self.inference),
        )


def load_model_config(config_name: str) -> ModelConfig:
    """Load a bundled model YAML by its config filename stem (no extension)."""
    path = _CONFIGS_DIR / f"{config_name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No bundled config '{config_name}' at {path}")
    raw = _load_yaml(path)
    return ModelConfig(
        audio=raw.get("audio", {}),
        model=raw.get("model", {}),
        training=raw.get("training", {}),
        inference=raw.get("inference", {}),
    )
