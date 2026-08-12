"""Behavior tests for the local test console API.

Only the boundary is faked: the LLM. Project state really round-trips through
disk, so these catch persistence bugs too.
"""
import pytest
from fastapi.testclient import TestClient

from novel_agent.web import app as web


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("NOVEL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("NOVEL_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("NOVEL_LLM_MODEL", "test-model")
    web.load_settings.cache_clear() if hasattr(web.load_settings, "cache_clear") else None
    return TestClient(web.app)


# ── config ───────────────────────────────────────────────────────────────────
def test_config_reports_provider_and_never_leaks_the_key(client):
    c = client.get("/api/config").json()
    assert c["provider"] == "anthropic"
    assert c["model"] == "test-model"
    assert c["key_present"] is True
    assert "test-key" not in str(c)          # only a short hint may appear
    assert "presets" in c


# ── the free lint endpoint (no LLM at all) ───────────────────────────────────
def test_lint_endpoint_scores_prose_without_calling_a_model(client):
    r = client.post("/api/lint", json={"text": '"오이오이!!" 역시 나였다.'}).json()
    assert r["score"] < 100
    rules = {v["rule"] for v in r["violations"]}
    assert "라노벨체 감탄사" in rules
    assert any(v["severity"] == "blocker" for v in r["violations"])


def test_lint_endpoint_gives_clean_prose_a_full_score(client):
    text = "\n".join(['"가진 놈 것만 훔친다."', "놈이 칼자루를 쥐었다.", '"규칙인가?"'] * 4)
    r = client.post("/api/lint", json={"text": text}).json()
    assert r["score"] == 100
    assert r["violations"] == []


# ── project lifecycle ────────────────────────────────────────────────────────
def test_project_is_created_listed_and_reloaded(client):
    st = client.post("/api/projects", json={"idea": "네오 조선의 흑인 홍길동, 코믹"}).json()
    pid = st["id"]
    assert st["step"] == "created"

    listed = client.get("/api/projects").json()
    assert [p["id"] for p in listed] == [pid]

    again = client.get(f"/api/projects/{pid}").json()
    assert again["idea"] == "네오 조선의 흑인 홍길동, 코믹"


def test_unknown_project_returns_404(client):
    assert client.get("/api/projects/deadbeef").status_code == 404


def test_answers_enrich_the_idea_and_forbid_inventing_beyond_them(client):
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    st = client.post(f"/api/projects/{pid}/answers", json={"answers": [
        {"topic": "기술 수준", "question": "q", "answer": "조어 금지"}]}).json()
    assert st["step"] == "answered"
    assert "조어 금지" in st["enriched_idea"]
    assert "새로 상상하지 마세요" in st["enriched_idea"]


def test_a_forbidden_setting_answer_binds_as_non_negotiable_not_a_preference(client):
    """The author saying "this must not exist" is not a preference.

    Regression: the console posted answers without `hard_rule`, so a prohibition
    was silently demoted into the soft "작가가 정한 방향(선호)" block that the model
    is allowed to interpret. The separation existed in interview.py but was dead
    code — nothing in production ever set the flag.
    """
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    st = client.post(f"/api/projects/{pid}/answers", json={"answers": [
        {"topic": "절대 금지 설정", "question": "없어야 하는 것?",
         "answer": "암호화폐, 생체 데이터", "hard_rule": True},
        {"topic": "세계관 밀도", "question": "밀도?", "answer": "가볍게"},
    ]}).json()
    enriched = st["enriched_idea"]
    forbidden, preference = enriched.index("절대 금지"), enriched.index("선호")
    # the prohibition must appear in the non-negotiable block, above preferences
    assert forbidden < preference
    assert "암호화폐, 생체 데이터" in enriched[forbidden:preference]
    assert "가볍게" in enriched[preference:]


def test_episode_requires_a_locked_premise_first(client):
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/episode", json={"episode": 1})
    assert r.status_code == 400
    assert "lock" in r.json()["detail"]


def test_lock_requires_setup_first(client):
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/lock", json={"pick": 1})
    assert r.status_code == 400


# ── error surfacing ──────────────────────────────────────────────────────────
def test_missing_api_key_is_reported_as_a_readable_error(client, monkeypatch):
    monkeypatch.setenv("NOVEL_LLM_API_KEY", "")
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/interview")
    assert r.status_code == 400
    assert "NOVEL_LLM_API_KEY" in r.json()["detail"]


def test_quota_errors_surface_as_429(client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    monkeypatch.setattr(web, "build_llm", boom)
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/interview")
    assert r.status_code == 429
    assert "쿼터" in r.json()["detail"]


def test_index_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "novel-agent" in r.text


def test_drafted_episode_is_committed_so_the_next_one_can_see_it(client, tmp_path,
                                                                monkeypatch):
    """Without this, episode 2 has no previous-episode continuity at all."""
    import hashlib

    from novel_agent.artifacts import EpisodeRecord
    from novel_agent.canon_store import CanonStore

    from .factories import canon, genre_profile, north_star, voice_bible

    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    store = CanonStore(web._root() / pid / "_novel")
    store.initialize(genre_profile=genre_profile(), north_star=north_star(),
                     canon=canon(), voice_bible=voice_bible())

    prose = "1화 본문이다."
    store.commit_episode(EpisodeRecord(
        episode_number=1, prose=prose,
        accepted_draft_hash=hashlib.sha256(prose.encode()).hexdigest()[:16]))

    assert store.latest_episode_number() == 1
    assert store.load_episode(1).prose == prose


# ── prompt editing ───────────────────────────────────────────────────────────
def test_prompts_are_listed_with_validation_status(client):
    ps = client.get("/api/prompts").json()
    names = {p["name"] for p in ps}
    assert "style_rules" in names and "drafter_system" in names
    assert all(p["problems"] == [] for p in ps), "shipped prompts must be valid"


def test_a_prompt_can_be_read(client):
    p = client.get("/api/prompts/style_rules").json()
    assert p["name"] == "style_rules" and len(p["text"]) > 100


def test_saving_a_prompt_that_drops_a_placeholder_is_rejected(client, tmp_path,
                                                              monkeypatch):
    monkeypatch.setenv("NOVEL_PROMPTS_DIR", str(tmp_path))
    from novel_agent import prompt_store
    prompt_store.clear_cache()
    (tmp_path / "genre_inference.md").write_text("원본 ${idea}", encoding="utf-8")

    r = client.put("/api/prompts/genre_inference", json={"text": "자리표시자 없음"})
    assert r.status_code == 400
    assert "idea" in r.json()["detail"]
    assert "원본" in (tmp_path / "genre_inference.md").read_text(encoding="utf-8")


def test_saving_a_valid_prompt_edit_persists(client, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_PROMPTS_DIR", str(tmp_path))
    from novel_agent import prompt_store
    prompt_store.clear_cache()
    (tmp_path / "genre_inference.md").write_text("원본 ${idea}", encoding="utf-8")

    r = client.put("/api/prompts/genre_inference", json={"text": "수정본 ${idea}"})
    assert r.status_code == 200
    assert "수정본" in (tmp_path / "genre_inference.md").read_text(encoding="utf-8")


def test_lint_response_explains_why_and_how_to_fix(client):
    # self-praise must be in NARRATION — inside dialogue it is a character speaking
    r = client.post("/api/lint", json={"text": '역시 나였다.\n"그렇군."'}).json()
    assert r["pass_score"] == 85
    v = next(x for x in r["violations"] if x["rule"] == "자기 칭찬 서술")
    assert v["why"] and v["fix"]
    assert "오글거림" in v["why"]
    assert v["bad"] and v["good"]          # concrete contrast pair


# ── canon editing (tester feedback 2) ────────────────────────────────────────
def _seed_canon(pid):
    from novel_agent.canon_store import CanonStore
    from .factories import canon, genre_profile, north_star, voice_bible
    store = CanonStore(web._root() / pid / "_novel")
    store.initialize(genre_profile=genre_profile(), north_star=north_star(),
                     canon=canon(), voice_bible=voice_bible())
    return store


def test_canon_can_be_read_back(client):
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    _seed_canon(pid)
    c = client.get(f"/api/projects/{pid}/canon").json()
    assert "김현우" in c["characters"]


def test_author_can_hand_edit_canon_without_regenerating(client):
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    store = _seed_canon(pid)
    c = client.get(f"/api/projects/{pid}/canon").json()
    c["characters"]["김현우"]["immutable_descriptors"] = ["오른쪽 눈썹의 흉터"]

    r = client.put(f"/api/projects/{pid}/canon", json={"canon": c})
    assert r.status_code == 200
    saved = store.load_canon()
    assert saved.characters["김현우"].immutable_descriptors == ["오른쪽 눈썹의 흉터"]
    assert saved.version == 1                      # bumped
    assert saved.last_modified_by == "author"      # provenance recorded


def test_malformed_canon_is_rejected_and_the_old_one_survives(client):
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    store = _seed_canon(pid)
    r = client.put(f"/api/projects/{pid}/canon", json={"canon": {"characters": "not-a-dict"}})
    assert r.status_code == 400
    assert "김현우" in store.load_canon().characters


def test_canon_read_requires_setup_first(client):
    pid = client.post("/api/projects", json={"idea": "아이디어"}).json()["id"]
    assert client.get(f"/api/projects/{pid}/canon").status_code == 400
