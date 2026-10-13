"""HTTP API + static UI.  Run:  python -m server.app  (or start.bat)"""
import json
import os
from contextlib import asynccontextmanager
import re
import zipfile
from pathlib import Path

from . import env  # noqa: F401  (must precede ML imports)

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import jobs

WEB = Path(__file__).resolve().parent.parent / "web"
DEMO = Path(__file__).resolve().parent.parent / "samples" / "demo_duet.wav"
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aif", ".aiff", ".alac",
             ".webm", ".mp4", ".mkv", ".mov"}

@asynccontextmanager
async def lifespan(_app):
    jobs.start_worker()
    if os.environ.get("WAVY_OPEN_BROWSER"):
        import threading
        import webbrowser

        port = os.environ.get("WAVY_PORT", "7860")
        threading.Timer(1.0, webbrowser.open, [f"http://localhost:{port}"]).start()
    yield


app = FastAPI(title="Wavy", lifespan=lifespan)


def _job_or_404(job_id: str) -> dict:
    try:
        return jobs.load(job_id)
    except KeyError:
        raise HTTPException(404, "No such song")


def _with_result(job: dict) -> dict:
    out = dict(job)
    if job["status"] == "queued":
        out["queue_position"] = jobs.queue_position(job["id"])
    if job["status"] == "done":
        result = jobs.DATA / job["id"] / "result.json"
        if result.exists():
            out["result"] = json.loads(result.read_text(encoding="utf-8"))
    return out


@app.get("/api/jobs")
def list_jobs():
    return [{k: j.get(k) for k in ("id", "name", "created", "status", "progress", "stage", "elapsed", "error")}
            for j in jobs.list_jobs()]


@app.post("/api/jobs")
def create_job(
    file: UploadFile = File(...),
    drumkit: bool = Form(True),
    singers: bool = Form(True),
    lyrics: bool = Form(True),
    language: str = Form(""),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext or '?'}'. Try MP3, WAV, FLAC, M4A, OGG…")
    options = {"drumkit": drumkit, "singers": singers, "lyrics": lyrics, "language": language.strip() or None}
    return jobs.create(file.filename, file.file, options)


@app.post("/api/demo")
def create_demo():
    if not DEMO.exists():
        raise HTTPException(404, "Demo song is missing (setup.bat generates it on Windows)")
    with open(DEMO, "rb") as f:
        return jobs.create("Demo duet.wav", f, {"drumkit": True, "singers": True, "lyrics": True, "language": "en"})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _with_result(_job_or_404(job_id))


@app.patch("/api/jobs/{job_id}")
def rename_job(job_id: str, body: dict):
    _job_or_404(job_id)
    name = str(body.get("name", "")).strip()[:200]
    if not name:
        raise HTTPException(400, "Name can't be empty")
    return jobs.update(job_id, name=name)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    _job_or_404(job_id)
    try:
        jobs.delete(job_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "song"


@app.get("/api/jobs/{job_id}/stems/{stem}")
def get_stem(job_id: str, stem: str, download: bool = False):
    job = _job_or_404(job_id)
    if not re.fullmatch(r"[a-z0-9_]+\.wav", stem):
        raise HTTPException(404)
    path = jobs.DATA / job_id / "stems" / stem
    if not path.exists():
        raise HTTPException(404)
    if download:
        result = json.loads((jobs.DATA / job_id / "result.json").read_text(encoding="utf-8"))
        label = next((t["label"] for t in result["tracks"] if t["file"] == stem), "Original mix")
        return FileResponse(path, media_type="audio/wav", filename=f"{_safe_name(job['name'])} - {label}.wav")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/jobs/{job_id}/lyrics.{fmt}")
def get_lyrics(job_id: str, fmt: str):
    job = _job_or_404(job_id)
    if fmt not in ("lrc", "srt", "txt"):
        raise HTTPException(404)
    path = jobs.DATA / job_id / f"lyrics.{fmt}"
    if not path.exists():
        raise HTTPException(404, "No lyrics were found in this song")
    return PlainTextResponse(path.read_text(encoding="utf-8"), headers={
        "Content-Disposition": f'attachment; filename="{_safe_name(job["name"])}.{fmt}"'})


@app.get("/api/jobs/{job_id}/download.zip")
def download_zip(job_id: str):
    job = _job_or_404(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Still processing")
    d = jobs.DATA / job_id
    zpath = d / "stems.zip"
    if not zpath.exists():
        result = json.loads((d / "result.json").read_text(encoding="utf-8"))
        tmp = zpath.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            audible = [t for t in result["tracks"] if not t["empty"]]
            for i, t in enumerate(audible, 1):
                z.write(d / "stems" / t["file"], f"{i:02d} {_safe_name(t['label'])}.wav")
            for fmt in ("lrc", "srt", "txt"):
                if (d / f"lyrics.{fmt}").exists():
                    z.write(d / f"lyrics.{fmt}", f"lyrics.{fmt}")
        tmp.replace(zpath)
    return FileResponse(zpath, media_type="application/zip", filename=f"{_safe_name(job['name'])} (unbaked).zip")


@app.get("/api/health")
def health():
    import torch

    return {"ok": True, "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "demo": DEMO.exists()}


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


def main():
    import uvicorn

    port = int(os.environ.get("WAVY_PORT", "7860"))
    print(f"\n  Wavy is running at  http://localhost:{port}\n", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
