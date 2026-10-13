"""HTTP API tests against a throwaway data dir. The worker isn't started, so no models run."""
import json

import pytest
from fastapi.testclient import TestClient

from server import app as app_module
from server import jobs


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "DATA", tmp_path / "jobs")
    (tmp_path / "jobs").mkdir()
    return TestClient(app_module.app)  # no `with`: lifespan (and the GPU worker) stays off


def _fake_done_job(name="Song"):
    job = jobs.create(f"{name}.wav", __import__("io").BytesIO(b"RIFF"), {})
    d = jobs.DATA / job["id"]
    (d / "stems").mkdir()
    (d / "stems" / "bass.wav").write_bytes(b"RIFFfake")
    (d / "result.json").write_text(json.dumps({
        "duration": 1.0, "tracks": [{"id": "bass", "label": "Bass", "file": "bass.wav", "empty": False},
                                    {"id": "guitar", "label": "Guitar", "file": "guitar.wav", "empty": True}],
        "lyrics": {"lines": []}, "singers": [], "original": {"file": "original.wav", "peaks": []},
    }))
    jobs.update(job["id"], status="done")
    return job["id"]


def test_rejects_non_audio_extension(client):
    r = client.post("/api/jobs", files={"file": ("notes.md", b"# hi")})
    assert r.status_code == 400 and "Unsupported" in r.json()["detail"]


def test_upload_creates_queued_job(client):
    r = client.post("/api/jobs", files={"file": ("My Song.mp3", b"ID3fake")}, data={"drumkit": "false"})
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "queued" and job["name"] == "My Song" and job["options"]["drumkit"] is False
    assert client.get(f"/api/jobs/{job['id']}").json()["queue_position"] >= 1


def test_unknown_and_malformed_ids_404(client):
    assert client.get("/api/jobs/doesnotexist").status_code == 404
    assert client.get("/api/jobs/..%2f..%2fetc").status_code == 404


def test_stem_names_are_whitelisted(client):
    jid = _fake_done_job()
    assert client.get(f"/api/jobs/{jid}/stems/bass.wav").status_code == 200
    for bad in ["..%2fjob.json", "job.json", "BASS.WAV", "a.wav.exe"]:
        assert client.get(f"/api/jobs/{jid}/stems/{bad}").status_code == 404


def test_download_filename_uses_song_and_track(client):
    jid = _fake_done_job("Café: Live?")
    r = client.get(f"/api/jobs/{jid}/stems/bass.wav?download=1")
    assert "Caf" in r.headers["content-disposition"] and "Bass" in r.headers["content-disposition"]


def test_zip_skips_empty_tracks(client):
    import io
    import zipfile

    jid = _fake_done_job()
    r = client.get(f"/api/jobs/{jid}/download.zip")
    assert r.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist() == ["01 Bass.wav"]


def test_zip_refused_while_processing(client):
    r = client.post("/api/jobs", files={"file": ("a.wav", b"RIFF")})
    assert client.get(f"/api/jobs/{r.json()['id']}/download.zip").status_code == 409


def test_rename_and_delete(client):
    jid = _fake_done_job()
    assert client.patch(f"/api/jobs/{jid}", json={"name": "  New name "}).json()["name"] == "New name"
    assert client.patch(f"/api/jobs/{jid}", json={"name": "   "}).status_code == 400
    assert client.delete(f"/api/jobs/{jid}").status_code == 200
    assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_cannot_delete_running_job(client):
    jid = _fake_done_job()
    jobs.update(jid, status="running")
    assert client.delete(f"/api/jobs/{jid}").status_code == 409


def test_missing_lyrics_404(client):
    jid = _fake_done_job()
    assert client.get(f"/api/jobs/{jid}/lyrics.lrc").status_code == 404
    assert client.get(f"/api/jobs/{jid}/lyrics.exe").status_code == 404
