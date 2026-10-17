"""Background jobs for the console — work that outlives an HTTP request.

One episode takes 12-20 minutes on the current model. As a single synchronous
request that is past the wait limit of most browsers and tunnels, and when the
connection dropped the result was unreachable even though the server finished
it (coworker feedback 1). A job returns at once, reports which stage it is in,
and keeps its result on disk so a reload or a reconnect gets it back.

Single process, local console: a thread per job and a JSON file per job under
the project's jobs/ directory. One job per project at a time, because two
concurrent writes of the same episode would commit it twice.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
import uuid
from typing import Any, Callable

RUNNING, DONE, ERROR, CANCELLED, INTERRUPTED = (
    "running", "done", "error", "cancelled", "interrupted")

# Cancel flags of jobs alive in THIS process. A file that says "running" with no
# entry here belongs to a server that has since died.
_LIVE: dict[str, threading.Event] = {}
_LOCK = threading.Lock()


class Cancelled(Exception):
    """Raised at a stage boundary once the author has asked the job to stop."""


class Busy(Exception):
    def __init__(self, job: dict):
        super().__init__(job.get("id", ""))
        self.job = job


class JobContext:
    """What the work function sees: report a stage, and stop when asked to.

    Cancellation is cooperative and checked at each stage boundary — the LLM
    call in flight finishes first, so the work must commit nothing before its
    final stage.
    """

    def __init__(self, path: pathlib.Path, job: dict, cancel: threading.Event):
        self._path, self._job, self._cancel = path, job, cancel

    def stage(self, key: str, label: str, detail: str = "") -> None:
        if self._cancel.is_set():
            raise Cancelled()
        self._job.update(stage=key, stage_label=label, stage_detail=detail,
                         updated_at=time.time())
        _write(self._path, self._job)

    def progress(self, **fields) -> None:
        """Report without the cancel check — for work that stops on its own
        terms (a serial finishes the episode in hand before honouring a stop)."""
        self._job.update(fields, updated_at=time.time())
        _write(self._path, self._job)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


def _jobs_dir(project_dir: pathlib.Path) -> pathlib.Path:
    d = project_dir / "jobs"
    d.mkdir(exist_ok=True)
    return d


def _write(path: pathlib.Path, job: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read(path: pathlib.Path) -> dict:
    job = json.loads(path.read_text(encoding="utf-8"))
    if job.get("status") == RUNNING and job.get("id") not in _LIVE:
        job.update(status=INTERRUPTED, error="서버가 재시작되어 작업이 중단됐습니다. "
                   "커밋 전에 멈췄으므로 다시 실행하면 같은 화부터 씁니다.")
        _write(path, job)
    return job


def get(project_dir: pathlib.Path, job_id: str) -> dict | None:
    path = _jobs_dir(project_dir) / f"{job_id}.json"
    return _read(path) if path.exists() else None


def latest(project_dir: pathlib.Path, kind: str | None = None) -> dict | None:
    jobs = [_read(p) for p in _jobs_dir(project_dir).glob("*.json")]
    jobs = [j for j in jobs if kind is None or j.get("kind") == kind]
    return max(jobs, key=lambda j: j.get("started_at", 0), default=None)


def active(project_dir: pathlib.Path) -> dict | None:
    return next((j for j in (_read(p) for p in _jobs_dir(project_dir).glob("*.json"))
                 if j.get("status") == RUNNING), None)


def cancel(project_dir: pathlib.Path, job_id: str) -> bool:
    with _LOCK:
        ev = _LIVE.get(job_id)
    if ev is None:
        return False
    ev.set()
    return True


def start(project_dir: pathlib.Path, kind: str, fn: Callable[[JobContext], Any],
          *, meta: dict | None = None) -> dict:
    """Run fn(ctx) in a thread. Raises Busy if this project already has a job."""
    with _LOCK:
        running = active(project_dir)
        if running is not None:
            raise Busy(running)
        now = time.time()
        job = {"id": uuid.uuid4().hex[:12], "kind": kind, "status": RUNNING,
               "stage": "queued", "stage_label": "대기", "stage_detail": "",
               "started_at": now, "updated_at": now, "meta": meta or {},
               "result": None, "error": None}
        path = _jobs_dir(project_dir) / f"{job['id']}.json"
        # registered as live BEFORE the file exists, or a concurrent read would
        # take the fresh "running" file for a dead server's orphan
        ev = threading.Event()
        _LIVE[job["id"]] = ev
        _write(path, job)

    def run() -> None:
        try:
            job["result"] = fn(JobContext(path, job, ev))
            job["status"] = DONE
        except Cancelled:
            job["status"] = CANCELLED
        except Exception as e:  # noqa: BLE001 — the job file is where it surfaces
            job["status"] = ERROR
            job["error"] = getattr(e, "detail", None) or f"{type(e).__name__}: {e}"
        finally:
            job["updated_at"] = time.time()
            _write(path, job)
            with _LOCK:
                _LIVE.pop(job["id"], None)

    threading.Thread(target=run, name=f"job-{job['id']}", daemon=True).start()
    return dict(job)
