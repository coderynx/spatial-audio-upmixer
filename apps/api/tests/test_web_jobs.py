import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from _helpers import _wav_bytes


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

    # submit_job's pre-check and resume_job's re-check each bind their own
    # module-level reference to ensure_stem_separation_available, so both
    # must be patched to stub out both call sites.
    monkeypatch.setattr(
        "upmixer_web.features.jobs.routes.ensure_stem_separation_available",
        unavailable,
    )
    monkeypatch.setattr(
        "upmixer_web.features.jobs.service.ensure_stem_separation_available",
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


def test_a_job_completes_and_downloads(web_client, in_process_jobs):
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
            "engine": {"mode": "stem"},
            "mixing": {
                "channel_layout": "5.1",
            },
            "mastering": {"loudness": {"normalize": False}},
                "format": {"type": "multichannel", "codec": "wav_pcm", "subtype": "PCM_24", "sample_rate": 48000},
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


@pytest.mark.parametrize(
    ("codec", "extension", "media_type"),
    [
        ("flac", ".flac", "audio/flac"),
        ("ogg_vorbis", ".ogg", "audio/ogg"),
        ("ogg_opus", ".opus", "audio/ogg"),
    ],
)
def test_a_job_delivers_its_codec_extension_and_content_type(
    web_client, in_process_jobs, codec, extension, media_type
):
    imported = web_client.post(
        "/api/v1/imports",
        files=[
            ("files", ("tone.wav", _wav_bytes(), "audio/wav")),
            ("relative_paths", (None, "tone.wav")),
        ],
    ).json()
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": f"Tone {codec}",
        "manifest": {
            "version": "1.0.0",
            "engine": {"mode": "stem"},
            "mixing": {
                "channel_layout": "5.1",
            },
            "mastering": {"loudness": {"normalize": False}},
            "format": {
                "type": "multichannel", "codec": codec,
                "subtype": "PCM_24", "sample_rate": 48000,
            },
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
    artifact = job["artifacts"][0]
    assert artifact["filename"].endswith(extension)
    assert artifact["content_type"] == media_type
    download = web_client.get(artifact["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith(media_type)


def test_a_job_rejects_a_codec_the_layout_cannot_carry(web_client):
    imported = web_client.post(
        "/api/v1/imports",
        files=[
            ("files", ("tone.wav", _wav_bytes(), "audio/wav")),
            ("relative_paths", (None, "tone.wav")),
        ],
    ).json()
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Too many channels for FLAC",
        "manifest": {
            "version": "1.0.0",
            "engine": {"mode": "stem"},
            "mixing": {"channel_layout": "7.1.4"},
            "format": {"type": "multichannel", "codec": "flac", "subtype": "PCM_24"},
        },
        "start": True,
    })
    assert response.status_code == 422
    assert "8 channels" in response.json()["detail"]
