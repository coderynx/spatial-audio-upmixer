import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from tests.web_test_helpers import _wav_bytes, web_client


def test_stem_jobs_fail_before_queue_when_dependency_is_missing(
    web_client,
    monkeypatch,
):
    imported = web_client.post(
        "/api/v1/imports",
        files=[
            ("files", ("tone.wav", _wav_bytes(), "audio/wav")),
            ("relative_paths", (None, "tone.wav")),
        ],
    ).json()

    def unavailable(_manifest, _capability):
        raise ValueError("Stem separation is unavailable")

    monkeypatch.setattr(
        "upmixer_web.routes_jobs.ensure_stem_separation_available",
        unavailable,
    )
    payload = {
        "import_id": imported["id"],
        "name": "Stem master",
        "manifest": {
            "version": "1.0.0",
            "engine": {"mode": "stem", "stems": ["Vocals"]},
            "mixing": {"channel_layout": "5.1"},
        },
        "start": True,
    }
    response = web_client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "Stem separation is unavailable"

    payload["start"] = False
    paused = web_client.post("/api/v1/jobs", json=payload)
    assert paused.status_code == 201
    resume = web_client.post(f"/api/v1/jobs/{paused.json()['id']}/resume")
    assert resume.status_code == 422


def test_realtime_job_completes_and_downloads(web_client):
    imported = web_client.post(
        "/api/v1/imports",
        files=[
            ("files", ("tone.wav", _wav_bytes(), "audio/wav")),
            ("relative_paths", (None, "tone.wav")),
        ],
    ).json()
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Tone master",
        "manifest": {
            "version": "1.0.0",
            "engine": {"mode": "realtime"},
            "mixing": {
                "channel_layout": "5.1",
                "spatial": {"profile": "balanced", "intensity": 0.5, "preanalyze": False},
            },
            "mastering": {"loudness": {"normalize": False}},
            "format": {"type": "wav", "subtype": "PCM_24", "sample_rate": 48000},
        },
        "start": True,
    })
    assert response.status_code == 201
    job_id = response.json()["id"]

    deadline = time.monotonic() + 10
    job = None
    while time.monotonic() < deadline:
        job = web_client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert job is not None
    assert job["status"] == "completed", job.get("error")
    assert job["progress"] == 1.0
    artifact = job["artifacts"][0]
    download = web_client.get(artifact["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("audio/wav")
    assert len(download.content) > 44
