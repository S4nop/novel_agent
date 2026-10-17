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


# ── per-episode directions (coworker feedback 3-2) ──────────────────────────
class DirectionLLM(FakeLLM):
    """Answers the directions call and records what the planner was told."""

    def __init__(self):
        super().__init__()
        self.plan_prompts = []

    def structured(self, messages, schema):
        from novel_agent.schemas import BeatSheetDraft, DirectionDraft, DirectionsDraft
        if schema is DirectionsDraft:
            return DirectionsDraft(options=[
                DirectionDraft(title=f"방향{i}", synopsis=f"줄거리{i}",
                               key_scene=f"장면{i}", cut_point=f"절단{i}") for i in (1, 2, 3)])
        if schema is BeatSheetDraft:
            self.plan_prompts.append(messages[-1]["content"])
        return super().structured(messages, schema)


def test_three_directions_arrive_through_a_job(client, monkeypatch):
    pid, _ = _project(client)
    _use(monkeypatch, DirectionLLM())
    r = client.post(f"/api/projects/{pid}/directions", json={"episode": 1})
    assert r.status_code == 202
    job = _wait(client, pid, r.json()["job_id"])
    assert [o["title"] for o in job["result"]["options"]] == ["방향1", "방향2", "방향3"]


def test_the_last_directions_survive_a_reload(client, monkeypatch):
    pid, _ = _project(client)
    _use(monkeypatch, DirectionLLM())
    _wait(client, pid, client.post(f"/api/projects/{pid}/directions",
                                   json={"episode": 1}).json()["job_id"])
    latest = client.get(f"/api/projects/{pid}/directions/latest").json()
    assert latest["episode"] == 1 and len(latest["options"]) == 3
    assert latest["stale"] is False


def test_directions_go_stale_once_the_story_moves_on(client, monkeypatch):
    """Proposed for 1화 against yesterday's canon, they no longer describe the
    situation the next episode starts from."""
    pid, store = _project(client)
    _use(monkeypatch, DirectionLLM())
    _wait(client, pid, client.post(f"/api/projects/{pid}/directions",
                                   json={"episode": 1}).json()["job_id"])
    c = store.load_canon()
    c.last_modified_by = "author"
    c.version += 1
    store.save_canon(c)
    assert client.get(f"/api/projects/{pid}/directions/latest").json()["stale"] is True


def test_the_chosen_direction_reaches_the_planner(client, monkeypatch):
    pid, _ = _project(client)
    llm = DirectionLLM()
    _use(monkeypatch, llm)
    job_id = client.post(f"/api/projects/{pid}/episode", json={
        "episode": 1, "direction": "제목: 덕배의 배신\n줄거리: 덕배가 먼저 등을 돌린다"}).json()["job_id"]
    result = _wait(client, pid, job_id)["result"]

    assert "덕배가 먼저 등을 돌린다" in llm.plan_prompts[-1]
    assert result["direction"].startswith("제목: 덕배의 배신")


# ── 한꺼번에 집필 (coworker feedback 3-1) ──────────────────────────────────
class ChargingLLM(FakeLLM):
    """Bills the usage meter the console hands it, so budgets mean something,
    and can hold the Nth text call until released."""

    def __init__(self, usage=None, hold_at=None):
        super().__init__()
        from novel_agent.llm import Usage
        self.usage = usage or Usage()
        self.hold_at, self.calls = hold_at, 0
        self.release = threading.Event()

    def text(self, messages, *, max_tokens=8192):
        self.calls += 1
        if self.calls == self.hold_at:
            assert self.release.wait(10)
        self.usage.add(input_tokens=10_000, output_tokens=10_000)   # ~₩300 at test prices
        return super().text(messages, max_tokens=max_tokens)


def _charging(monkeypatch, **kw):
    made = []

    def build(*a, usage=None, **k):
        made.append(ChargingLLM(usage=usage, **kw))
        return made[-1]
    monkeypatch.setattr(web, "build_llm", build)
    return made


def test_the_whole_serial_is_written_as_one_job(client, monkeypatch):
    pid, store = _project(client)
    _charging(monkeypatch)
    r = client.post(f"/api/projects/{pid}/serial", json={"target_episodes": 3})
    assert r.status_code == 202
    result = _wait(client, pid, r.json()["job_id"])["result"]

    assert store.latest_episode_number() == 3
    assert result["committed"] == 3
    assert [o["episode"] for o in result["outcomes"]] == [1, 2, 3]


def test_progress_is_visible_while_the_serial_runs(client, monkeypatch):
    pid, _ = _project(client)
    made = _charging(monkeypatch, hold_at=2)       # holds inside 2화
    job_id = client.post(f"/api/projects/{pid}/serial", json={"target_episodes": 3}).json()["job_id"]

    # 2화 is held mid-draft, so once its start is reported 1화's outcome is
    # already recorded — waiting on the outcome alone raced the start report.
    deadline = time.monotonic() + 10
    job = {}
    while time.monotonic() < deadline and "2/3화" not in job.get("stage_label", ""):
        time.sleep(0.02)
        job = client.get(f"/api/projects/{pid}/jobs/{job_id}").json()
    assert "2/3화" in job["stage_label"]
    assert [o["episode"] for o in job["progress"]["outcomes"]] == [1]
    made[-1].release.set()
    _wait(client, pid, job_id)


def test_stopping_the_serial_keeps_what_it_finished(client, monkeypatch):
    pid, store = _project(client)
    made = _charging(monkeypatch, hold_at=2)
    job_id = client.post(f"/api/projects/{pid}/serial", json={"target_episodes": 5}).json()["job_id"]
    _wait_for_stage(client, pid, job_id, "serial")
    while not made or made[-1].calls < 2:
        time.sleep(0.02)

    client.post(f"/api/projects/{pid}/jobs/{job_id}/cancel")
    made[-1].release.set()
    job = _wait(client, pid, job_id)

    assert job["status"] == "done"
    assert store.latest_episode_number() == 2      # the episode in hand was finished
    assert "3화부터" in job["result"]["stopped_because"]


def test_the_budget_is_what_this_run_spends_not_the_projects_lifetime_total(client, monkeypatch):
    """The console meter is cumulative; capping a new run by it would stop the
    run before its first episode on any project that had already spent."""
    pid, store = _project(client)
    state = json.loads((web._root() / pid / "state.json").read_text(encoding="utf-8"))
    state["usage"] = {"input_tokens": 10**9, "output_tokens": 10**9, "calls": 999}
    (web._root() / pid / "state.json").write_text(json.dumps(state), encoding="utf-8")
    _charging(monkeypatch)

    job_id = client.post(f"/api/projects/{pid}/serial",
                         json={"target_episodes": 2, "budget_krw": 5000}).json()["job_id"]
    _wait(client, pid, job_id)
    assert store.latest_episode_number() == 2
    after = client.get(f"/api/projects/{pid}").json()["usage"]
    assert after["calls"] > 999                    # and it still lands on the project meter


def test_a_small_budget_stops_the_serial_early(client, monkeypatch):
    pid, store = _project(client)
    _charging(monkeypatch)
    job_id = client.post(f"/api/projects/{pid}/serial",
                         json={"target_episodes": 5, "budget_krw": 1}).json()["job_id"]
    result = _wait(client, pid, job_id)["result"]
    assert store.latest_episode_number() == 1
    assert "예산" in result["stopped_because"]


def test_an_estimate_is_given_before_starting(client):
    pid, _ = _project(client)
    est = client.get(f"/api/projects/{pid}/serial/estimate?target_episodes=10").json()
    assert est["remaining"] == 10
    assert 0 < est["krw_min"] <= est["krw_max"]
    assert 0 < est["minutes_min"] <= est["minutes_max"]


def test_a_finished_episode_can_be_read_while_the_run_continues(client, monkeypatch):
    pid, _ = _project(client)
    _charging(monkeypatch)
    _wait(client, pid, client.post(f"/api/projects/{pid}/serial",
                                   json={"target_episodes": 1}).json()["job_id"])
    ep = client.get(f"/api/projects/{pid}/episodes/1").json()
    assert ep["prose"] == CLEAN and ep["committed"] is True
