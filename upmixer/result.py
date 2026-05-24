"""Structured result returned by upmix pipeline process_file() calls.

Provides a machine-readable summary of a completed upmix operation for use
by library callers, scripting, and --json CLI output.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class UpmixResult:
    """Metadata produced by a completed upmix operation.

    Returned by :meth:`UpmixPipeline.process_file` and
    :meth:`StemUpmixPipeline.process_file`.  All fields are safe to
    serialise to JSON via :meth:`to_json`.

    Attributes:
        input_path: Absolute or relative path of the source file.
        output_path: Absolute or relative path of the written output file.
        input_format: Detected or overridden input format name (e.g. "Stereo").
        output_format: Output channel layout name (e.g. "7.1.4 Atmos").
        input_sample_rate: Sample rate of the source file in Hz.
        output_sample_rate: Sample rate of the written output in Hz.
        duration_seconds: Duration of the audio in seconds.
        n_channels_in: Number of input channels.
        n_channels_out: Number of output channels written.
        mode: Processing mode — ``"realtime"`` (STFT coherence) or ``"stem"``.
        measured_lkfs: Integrated loudness before normalization (BS.1770-4),
            or *None* if loudness normalization was disabled.
        measured_tp_dbtp: True Peak before normalization in dBTP, or *None*.
        applied_gain_db: Linear gain applied for loudness normalization in dB,
            or *None*.
        stems: Canonical stem names used during separation (stem mode only),
            or *None* in realtime mode.
        processing_time_seconds: Wall-clock time for the full operation.
    """

    input_path: str
    output_path: str
    input_format: str
    output_format: str
    input_sample_rate: int
    output_sample_rate: int
    duration_seconds: float
    n_channels_in: int
    n_channels_out: int
    mode: str
    measured_lkfs: float | None = None
    measured_tp_dbtp: float | None = None
    applied_gain_db: float | None = None
    stems: list[str] | None = None
    processing_time_seconds: float = 0.0

    def to_dict(self) -> dict:
        """Return a plain dict (JSON-serialisable)."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)
