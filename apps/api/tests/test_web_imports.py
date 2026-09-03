import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from _helpers import _wav_bytes


def test_album_import_preview_and_paused_job(web_client):
    response = web_client.post(
        "/api/v1/imports",
        files=[
            ("files", ("01.wav", _wav_bytes(), "audio/wav")),
            ("files", ("02.wav", _wav_bytes(550.0), "audio/wav")),
            ("relative_paths", (None, "Example Album/01.wav")),
            ("relative_paths", (None, "Example Album/02.wav")),
        ],
    )
    assert response.status_code == 201
    imported = response.json()
    assert imported["kind"] == "album"
    assert imported["title"] == "Example Album"
    assert [asset["position"] for asset in imported["assets"]] == [0, 1]
    assert all(asset["audio_url"] for asset in imported["assets"])

    audio_url = imported["assets"][0]["audio_url"]
    audio = web_client.get(audio_url)
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/")
    partial = web_client.get(audio_url, headers={"Range": "bytes=0-15"})
    assert partial.status_code == 206
    assert partial.content == audio.content[:16]
    assert web_client.get(
        audio_url.replace(imported["id"], "wrong-import", 1)
    ).status_code == 404

    manifest = {
        "version": "1.0.0",
        "engine": {"mode": "stem"},
        "mixing": {"channel_layout": "5.1"},
        "format": {"type": "multichannel", "codec": "wav_pcm", "subtype": "PCM_24", "sample_rate": 48000},
    }
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Album master",
        "manifest": manifest,
        "start": False,
    })
    assert response.status_code == 201
    job = response.json()
    assert job["status"] == "paused"
    assert len(job["tracks"]) == 2

    clone = web_client.post(f"/api/v1/jobs/{job['id']}/clone", json={"start": False})
    assert clone.status_code == 201
    assert clone.json()["source_job_id"] == job["id"]
    assert all(track["asset"]["audio_url"] for track in job["tracks"])


def test_mastering_reference_upload_runs_and_rejects_client_path(web_client, in_process_jobs):
    imported = web_client.post(
        "/api/v1/imports",
        files=[("files", ("tone.wav", _wav_bytes(), "audio/wav"))],
        data={"relative_paths": "tone.wav"},
    ).json()
    reference = web_client.post(
        f"/api/v1/imports/{imported['id']}/mastering-references",
        files={"file": ("reference.wav", _wav_bytes(660.0), "audio/wav")},
    )
    assert reference.status_code == 201
    reference_data = reference.json()
    assert reference_data["filename"] == "reference.wav"
    assert reference_data["channels"] == 2

    accepted = {
        "version": "1.0.0",
        "format": {
            "downmix": {
                "enabled": False,
                "output": None,
                "surround_coeff": 0.7071,
            },
        },
    }
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Null downmix output",
        "manifest": accepted,
    })
    assert response.status_code == 201

    manifest = {
        "version": "1.0.0",
        "engine": {"mode": "stem"},
        "mixing": {"channel_layout": "5.1"},
        "mastering": {
            "loudness": {"normalize": False},
            "match_reference": {
                "strength": 0.5,
                "spectrum": True,
                "rms": True,
                "max_db": 8.0,
            },
        },
        "format": {"type": "multichannel", "codec": "wav_pcm", "subtype": "PCM_24", "sample_rate": 48000},
    }
    response = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Reference master",
        "manifest": manifest,
        "mastering_reference_id": reference_data["id"],
        "start": True,
    })
    assert response.status_code == 201
    job_id = response.json()["id"]
    assert response.json()["mastering_reference"]["id"] == reference_data["id"]
    assert "path" not in response.json()["manifest"]["mastering"]["match_reference"]

    deadline = time.monotonic() + 10
    job = None
    while time.monotonic() < deadline:
        job = web_client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert job is not None
    assert job["status"] == "completed", job.get("error")

    manifest["mastering"]["match_reference"]["path"] = "/unsafe/reference.wav"
    rejected = web_client.post("/api/v1/jobs", json={
        "import_id": imported["id"],
        "name": "Unsafe reference",
        "manifest": manifest,
        "start": False,
    })
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == (
        "mastering.match_reference.path is managed by reference upload"
    )
