"""Long-running console work runs as a background job (coworker feedback 1).

A single episode takes 12-20 minutes on the current model. As one synchronous
HTTP request that is past the wait limit of most browsers and tunnels, and when
the connection dropped the console blanked the prose and put the error in the
log panel at the bottom of the page. Only the LLM is faked; jobs, threads and
the project files on disk are real.
"""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from novel_agent.web import app as web

from .test_driver import CLEAN, DIRTY, FakeLLM


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("NOVEL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("NOVEL_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("NOVEL_LLM_MODEL", "test-model")
    return TestClient(web.app)


def _project(client):
    from novel_agent.canon_store import CanonStore

    from .factories import canon, genre_profile, north_star, voice_bible

    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    store = CanonStore(web._root() / pid / "_novel")
    store.initialize(genre_profile=genre_profile(), north_star=north_star(),
                     canon=canon(), voice_bible=voice_bible())
    return pid, store


def _use(monkeypatch, llm):
    monkeypatch.setattr(web, "build_llm", lambda *a, **k: llm)


def _wait(client, pid, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/projects/{pid}/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError(f"job still running after {timeout}s: {job}")


def _wait_for_stage(client, pid, job_id, stage, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/projects/{pid}/jobs/{job_id}").json()
        if job.get("stage") == stage:
            return job
        time.sleep(0.02)
    raise AssertionError(f"never reached stage {stage}: {job}")


class GatedLLM(FakeLLM):
    """Blocks inside the draft call until released, so a test can act on a job
    while it is genuinely mid-flight."""

    def __init__(self, prose=CLEAN, fail=False):
        super().__init__(prose=prose)
        self.release = threading.Event()
        self.fail = fail

    def text(self, messages, *, max_tokens=8192):
        assert self.release.wait(10), "test never released the draft call"
        if self.fail:
            raise RuntimeError("upstream exploded")
        return super().text(messages, max_tokens=max_tokens)


# ── the request returns at once; the episode arrives through the job ────────
def test_writing_an_episode_returns_a_job_instead_of_waiting_for_the_prose(client, monkeypatch):
    pid, _ = _project(client)
    llm = GatedLLM()
    _use(monkeypatch, llm)

    r = client.post(f"/api/projects/{pid}/episode", json={"episode": 1})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert client.get(f"/api/projects/{pid}/jobs/{job_id}").json()["status"] == "running"

    llm.release.set()
    job = _wait(client, pid, job_id)
    assert job["status"] == "done"
    assert job["result"]["prose"] == CLEAN


def test_the_job_says_which_stage_it_is_in(client, monkeypatch):
    pid, _ = _project(client)
    llm = GatedLLM()
    _use(monkeypatch, llm)
    job_id = client.post(f"/api/projects/{pid}/episode", json={"episode": 1}).json()["job_id"]

    job = _wait_for_stage(client, pid, job_id, "draft")
    assert job["stage_label"]
    llm.release.set()
    _wait(client, pid, job_id)


def test_the_last_result_is_still_there_after_a_reload(client, monkeypatch):
    """The prose existed on disk when the connection dropped, but nothing in
    the console could fetch it back."""
    pid, _ = _project(client)
    _use(monkeypatch, FakeLLM())
    job_id = client.post(f"/api/projects/{pid}/episode", json={"episode": 1}).json()["job_id"]
    _wait(client, pid, job_id)

    latest = client.get(f"/api/projects/{pid}/jobs/latest").json()
    assert latest["id"] == job_id
    assert latest["result"]["prose"] == CLEAN


def test_a_second_job_is_refused_while_one_is_running(client, monkeypatch):
    """Two concurrent writes of the same episode would commit it twice."""
    pid, _ = _project(client)
    llm = GatedLLM()
    _use(monkeypatch, llm)
    job_id = client.post(f"/api/projects/{pid}/episode", json={"episode": 1}).json()["job_id"]

    again = client.post(f"/api/projects/{pid}/episode", json={"episode": 1})
    assert again.status_code == 409
    assert "실행 중" in again.json()["detail"]

    llm.release.set()
    _wait(client, pid, job_id)


def test_cancelling_before_the_commit_leaves_canon_untouched(client, monkeypatch):
    pid, store = _project(client)
    llm = GatedLLM()
    _use(monkeypatch, llm)
    job_id = client.post(f"/api/projects/{pid}/episode", json={"episode": 1}).json()["job_id"]
    _wait_for_stage(client, pid, job_id, "draft")

    assert client.post(f"/api/projects/{pid}/jobs/{job_id}/cancel").status_code == 200
    llm.release.set()

    job = _wait(client, pid, job_id)
    assert job["status"] == "cancelled"
    assert store.latest_episode_number() == 0
    assert store.load_summary().story_so_far == ""


def test_a_failure_is_reported_on_the_job_rather_than_swallowed(client, monkeypatch):
    pid, _ = _project(client)
    llm = GatedLLM(fail=True)
    llm.release.set()
    _use(monkeypatch, llm)
    job_id = client.post(f"/api/projects/{pid}/episode", json={"episode": 1}).json()["job_id"]

    job = _wait(client, pid, job_id)
    assert job["status"] == "error"
    assert "upstream exploded" in job["error"]


def test_a_job_left_running_by_a_server_that_died_reads_as_interrupted(client):
    """A restart kills the thread but not the file that says it is running —
    without this the project would refuse every new job forever."""
    pid, _ = _project(client)
    jobs = web._root() / pid / "jobs"
    jobs.mkdir()
    (jobs / "orphan.json").write_text(json.dumps({
        "id": "orphan", "kind": "episode", "status": "running", "stage": "draft",
        "stage_label": "집필", "started_at": 1.0, "updated_at": 1.0}), encoding="utf-8")

    assert client.get(f"/api/projects/{pid}/jobs/orphan").json()["status"] == "interrupted"


def test_an_interrupted_job_does_not_block_a_new_one(client, monkeypatch):
    pid, _ = _project(client)
    jobs = web._root() / pid / "jobs"
    jobs.mkdir()
    (jobs / "orphan.json").write_text(json.dumps({
        "id": "orphan", "kind": "episode", "status": "running", "started_at": 1.0,
        "updated_at": 1.0}), encoding="utf-8")
    _use(monkeypatch, FakeLLM())

    r = client.post(f"/api/projects/{pid}/episode", json={"episode": 1})
    assert r.status_code == 202
    _wait(client, pid, r.json()["job_id"])


# ── the gate always runs ─────────────────────────────────────────────────────
def test_switching_the_revise_loop_off_still_runs_the_gate(client, monkeypatch):
    """With revise off the episode was committed without ever being checked,
    while the console labelled it 미통과."""
    pid, store = _project(client)
    _use(monkeypatch, FakeLLM(prose=DIRTY))
    job_id = client.post(f"/api/projects/{pid}/episode",
                         json={"episode": 1, "revise": False}).json()["job_id"]

    result = _wait(client, pid, job_id)["result"]
    assert result["passed"] is False
    assert result["committed"] is False
    assert result["iterations"] == 0
    assert store.latest_episode_number() == 0
