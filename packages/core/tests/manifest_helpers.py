"""Shared manifest fixtures builders for the manifest test modules."""
from __future__ import annotations

import json
from pathlib import Path


def _write_json(directory: str, data: dict, name: str = "job.json") -> str:
    path = str(Path(directory) / name)
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_yaml(directory: str, text: str, name: str = "job.yaml") -> str:
    path = str(Path(directory) / name)
    Path(path).write_text(text, encoding="utf-8")
    return path


def _minimal(assets=None, **extra) -> dict:
    """Return a minimal valid manifest dict."""
    return {
        "version": "1.0.0",
        "assets": assets or [{"input": "in.flac", "output": "out.wav"}],
        **extra,
    }
