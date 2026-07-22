"""Preflight, resumable state, and reporting helpers for CLI jobs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import soundfile as sf

from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP, INPUT_FORMAT_MAP, can_upmix, detect_input_format
from upmixer.result import UpmixResult


class PreflightError(ValueError):
    """Raised when a job cannot safely start."""


def config_fingerprint(config: UpmixConfig, input_format: str | None) -> str:
    """Return a stable fingerprint of settings that affect an output file."""
    payload = {"config": asdict(config), "input_format": input_format}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def input_identity(path: str) -> dict[str, int]:
    """Return inexpensive source identity data for resumable jobs."""
    stat = Path(path).stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def expected_output_sample_rate(config: UpmixConfig, input_sample_rate: int) -> int:
    """Resolve the actual delivery rate, including ADM-BWF's default."""
    if config.output_type == "adm-bwf" and config.output_sample_rate is None:
        return 48_000
    return config.output_sample_rate or input_sample_rate


def inspect_output(path: str) -> dict[str, int]:
    """Read technical metadata needed to trust an already-written output."""
    info = sf.info(path)
    return {"sample_rate": info.samplerate, "channels": info.channels, "frames": info.frames}


def preflight_job(
    input_path: str,
    output_path: str,
    config: UpmixConfig,
    input_format_override: str | None = None,
) -> dict[str, Any]:
    """Validate a resolved job without writing files."""
    source = Path(input_path)
    destination = Path(output_path)
    if not source.is_file():
        raise PreflightError(f"Input file does not exist or is not a file: {source}")
    if source.resolve() == destination.resolve():
        raise PreflightError("Input and output paths must be different")
    try:
        info = sf.info(str(source))
    except RuntimeError as exc:
        raise PreflightError(f"Cannot read input audio metadata '{source}': {exc}") from exc

    if input_format_override is not None:
        if input_format_override not in INPUT_FORMAT_MAP:
            raise PreflightError(f"Unknown input format '{input_format_override}'")
        input_fmt = INPUT_FORMAT_MAP[input_format_override]
        if input_fmt.n_channels != info.channels:
            raise PreflightError(
                f"Input format '{input_format_override}' expects {input_fmt.n_channels} "
                f"channels but file has {info.channels}"
            )
    else:
        try:
            input_fmt = detect_input_format(info.channels)
        except ValueError as exc:
            raise PreflightError(str(exc)) from exc

    output_fmt = FORMAT_MAP[config.output_format]
    if not can_upmix(input_fmt, output_fmt):
        raise PreflightError(f"Cannot upmix {input_fmt.name} to {output_fmt.name}")

    output_sr = expected_output_sample_rate(config, info.samplerate)
    if config.output_type == "adm-bwf":
        if output_sr not in (48_000, 96_000):
            raise PreflightError("Dolby ADM-BWF requires a 48 kHz or 96 kHz output sample rate")
        if config.output_subtype != "PCM_24":
            raise PreflightError("Dolby ADM-BWF requires PCM_24 output")
        if destination.suffix.lower() != ".wav":
            raise PreflightError("Dolby ADM-BWF output path must use a .wav extension")

    return {
        "input": str(source),
        "output": str(destination),
        "input_format": input_fmt.name,
        "input_sample_rate": info.samplerate,
        "output_sample_rate": output_sr,
        "output_channels": output_fmt.n_channels,
        "input_identity": input_identity(str(source)),
        "config_fingerprint": config_fingerprint(config, input_format_override),
    }


@dataclass
class RunState:
    """Durable per-output completion state used by ``--resume``."""

    path: Path
    entries: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, path: str | Path) -> "RunState":
        state_path = Path(path)
        if not state_path.exists():
            return cls(state_path, {})
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PreflightError(f"Cannot read run state '{state_path}': {exc}") from exc
        return cls(state_path, raw.get("entries", {}))

    def matches(self, plan: dict[str, Any]) -> bool:
        entry = self.entries.get(str(Path(plan["output"]).resolve()))
        if not entry:
            return False
        if entry.get("input_identity") != plan["input_identity"]:
            return False
        if entry.get("config_fingerprint") != plan["config_fingerprint"]:
            return False
        try:
            actual = inspect_output(plan["output"])
        except RuntimeError:
            return False
        expected = entry.get("output_metadata", {})
        return actual == expected and actual["sample_rate"] == plan["output_sample_rate"] and actual["channels"] == plan["output_channels"]

    def record(self, plan: dict[str, Any], result: UpmixResult) -> None:
        output = str(Path(plan["output"]).resolve())
        self.entries[output] = {
            "input": plan["input"],
            "input_identity": plan["input_identity"],
            "config_fingerprint": plan["config_fingerprint"],
            "output_metadata": inspect_output(plan["output"]),
            "result": result.to_dict(),
        }
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.tmp")
        temp.write_text(json.dumps({"version": 1, "entries": self.entries}, indent=2), encoding="utf-8")
        temp.replace(self.path)


def write_report(path: str | Path, report: dict[str, Any]) -> None:
    """Write a portable JSON report atomically."""
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp = report_path.with_name(f".{report_path.name}.tmp")
    temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temp.replace(report_path)
