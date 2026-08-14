"""Shared fixtures for the ``apps/api`` test suite."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

from upmixer_web.api import create_app
from upmixer_web.settings import Settings


@pytest.fixture(autouse=True)
def stem_separation_available(monkeypatch):
    """Neutralize the project-creation stem-engine gate.

    ``create_project_route`` forces ``engine.mode = "stem"``, so every project
    test 422s wherever the optional separation extras are absent (CI installs
    them nowhere). The gate itself is covered in ``test_web_jobs.py``.
    """
    monkeypatch.setattr(
        "upmixer_web.features.projects.routes.ensure_stem_separation_available",
        lambda *_args: None,
    )


@pytest.fixture
def web_client(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'web.db'}",
        worker_count=1,
    )
    with TestClient(create_app(settings)) as client:
        yield client
