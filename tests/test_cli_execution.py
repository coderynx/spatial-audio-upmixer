"""CLI coverage for preflight-only execution controls."""
from __future__ import annotations

import sys

import numpy as np
import soundfile as sf

from upmixer.__main__ import main


def test_help_lists_vocal_sub_stems(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["upmixer", "--help"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    help_text = capsys.readouterr().out
    assert "lead-vocals" in help_text
    assert "backing-vocals" in help_text


def test_dry_run_prints_resolved_job(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((480, 2)), 48_000)
    output = tmp_path / "nested" / "out.wav"
    monkeypatch.setattr(sys, "argv", ["upmixer", str(source), str(output), "--dry-run"])

    main()

    assert f"READY: {source} -> {output}" in capsys.readouterr().out
    assert not output.exists()
