"""Job store (one folder + job.json per song) and a single GPU worker thread."""
import json
import os
import queue
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import pipeline

DATA = Path(os.environ.get("WAVY_DATA") or Path(__file__).resolve().parent.parent / "data") / "jobs"
DATA.mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()  # Windows can't replace a file another thread has open: guard reads too
_queue: "queue.Queue[str]" = queue.Queue()
_cancel_requested: set[str] = set()


class Cancelled(Exception):
    pass
_TOTAL_WEIGHT = sum(w for _, _, w in pipeline.STAGES)


def _path(job_id: str) -> Path:
    if not job_id.isalnum():
        raise KeyError(job_id)
    return DATA / job_id


def load(job_id: str) -> dict:
    p = _path(job_id) / "job.json"
    with _lock:
        if not p.exists():
            raise KeyError(job_id)
        return json.loads(p.read_text(encoding="utf-8"))


def save(job: dict) -> None:
    with _lock:
        p = _path(job["id"]) / "job.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(job), encoding="utf-8")
        tmp.replace(p)


def update(job_id: str, **fields) -> dict:
    with _lock:
        job = load(job_id)
        job.update(fields)
        save(job)
        return job


def list_jobs() -> list[dict]:
    jobs = []
    for d in DATA.iterdir():
        try:
            jobs.append(load(d.name))
        except (KeyError, json.JSONDecodeError):
            continue
    return sorted(jobs, key=lambda j: j["created"], reverse=True)


def create(filename: str, data_stream, options: dict) -> dict:
    job_id = uuid.uuid4().hex[:12]
    d = _path(job_id)
    d.mkdir(parents=True)
    suffix = Path(filename).suffix.lower()[:8] or ".audio"
    with open(d / f"source{suffix}", "wb") as f:
        shutil.copyfileobj(data_stream, f, length=1 << 20)
    job = {
        "id": job_id,
        "name": Path(filename).stem,
        "source": f"source{suffix}",
        "created": time.time(),
        "status": "queued",
        "stage": None,
        "progress": 0.0,
        "options": options,
        "stages": [{"key": k, "label": label} for k, label, _ in pipeline.STAGES],
        "error": None,
    }
    save(job)
    _queue.put(job_id)
    return job


def delete(job_id: str) -> None:
    job = load(job_id)
    if job["status"] == "running":
        raise RuntimeError("Can't delete a song while it's being processed")
    shutil.rmtree(_path(job_id), ignore_errors=True)


def _process(job_id: str) -> None:
    job = load(job_id)
    weights = {k: w for k, _, w in pipeline.STAGES}
    order = [k for k, _, _ in pipeline.STAGES]
    started = time.time()

    last = {"stage": None, "t": 0.0}

    def report(stage: str, frac: float):
        now = time.time()
        if stage == last["stage"] and now - last["t"] < 0.4:  # progress bars tick fast; don't thrash the disk
            return
        if job_id in _cancel_requested:
            raise Cancelled()
        last.update(stage=stage, t=now)
        done = sum(weights[k] for k in order[: order.index(stage)])
        update(job_id, status="running", stage=stage,
               progress=round((done + weights[stage] * frac) / _TOTAL_WEIGHT, 4))

    update(job_id, status="running", started=started)
    try:
        pipeline.run(_path(job_id), _path(job_id) / job["source"], job.get("options", {}), report)
        update(job_id, status="done", stage=None, progress=1.0, finished=time.time(),
               elapsed=round(time.time() - started, 1))
    except Cancelled:
        update(job_id, status="cancelled", stage=None, finished=time.time())
    except Exception as e:  # surface the failure to the UI instead of dying silently
        traceback.print_exc()
        if "out of memory" in str(e).lower():
            e = RuntimeError("Ran out of GPU memory. Close other GPU-heavy apps (games, other AI tools) and retry.")
        update(job_id, status="error", error=str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}",
               finished=time.time())


def _worker():
    while True:
        job_id = _queue.get()
        try:
            if load(job_id)["status"] != "queued":  # cancelled or deleted while waiting
                continue
            _process(job_id)
        except KeyError:
            continue
        except Exception:
            traceback.print_exc()
        finally:
            _cancel_requested.discard(job_id)


def cancel(job_id: str) -> dict:
    with _lock:
        job = load(job_id)
        if job["status"] == "queued":
            return update(job_id, status="cancelled")
        if job["status"] == "running":
            _cancel_requested.add(job_id)
            return update(job_id, cancelling=True)
        return job


def retry(job_id: str) -> dict:
    with _lock:
        job = load(job_id)
        if job["status"] not in ("error", "cancelled"):
            raise RuntimeError("Only failed or cancelled songs can be retried")
        job = update(job_id, status="queued", stage=None, progress=0.0, error=None, cancelling=False,
                     created=time.time())
    _queue.put(job_id)
    return job


def start_worker() -> None:
    # Re-queue work that was pending when the server last stopped.
    for job in sorted(list_jobs(), key=lambda j: j["created"]):
        if job["status"] in ("queued", "running"):
            update(job["id"], status="queued", stage=None, progress=0.0)
            _queue.put(job["id"])
    threading.Thread(target=_worker, daemon=True, name="unbake-worker").start()


def queue_position(job_id: str) -> int:
    pending = [j for j in list_jobs() if j["status"] == "queued"]
    pending.sort(key=lambda j: j["created"])
    ids = [j["id"] for j in pending]
    return ids.index(job_id) + 1 if job_id in ids else 0
